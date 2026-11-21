"""Duplicate groups: overview, review detail, marking decisions, bulk exact acceptance."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from ..audit import audit
from ..deletion import workflow
from ..models import ACTIVE_STATES, DELETE_STATES, Decision, DupGroup, DupGroupMember, File, Item, User
from .deps import current_user, get_db
from .serialize import Names, iso, item_dict

router = APIRouter(prefix="/api/groups", tags=["groups"])

TYPE_ORDER = case(
    (DupGroup.type == "exact", 0), (DupGroup.type == "reencode", 1), (DupGroup.type == "video", 2), else_=3
)


class DecisionIn(BaseModel):
    item_id: int
    action: Literal["keep", "delete", "skip"]


class DecisionsIn(BaseModel):
    decisions: list[DecisionIn]


def _group_query(type_: str | None, status: str | None, owner: str | None, min_confidence: float | None):
    q = select(DupGroup.id)
    if type_:
        q = q.where(DupGroup.type == type_)
    if status and status != "all":
        q = q.where(DupGroup.status == status)
    if min_confidence is not None:
        q = q.where(DupGroup.confidence >= min_confidence)
    if owner:
        owner_cond = File.owner_id.is_(None) if owner == "unassigned" else File.owner_id == int(owner)
        sub = (
            select(DupGroupMember.group_id)
            .join(File, (File.item_id == DupGroupMember.item_id) & (File.role == "primary"))
            .where(owner_cond)
        )
        q = q.where(DupGroup.id.in_(sub))
    return q.order_by(TYPE_ORDER, DupGroup.confidence.desc(), DupGroup.id)


@router.get("/summary")
def summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.execute(select(DupGroup.type, DupGroup.status, func.count(DupGroup.id)).group_by(DupGroup.type, DupGroup.status))
    out: dict[str, dict[str, int]] = {t: {"open": 0, "resolved": 0, "dismissed": 0} for t in ("exact", "reencode", "burst", "video")}
    for t, s, c in rows:
        out.setdefault(t, {})[s] = c
    pending = db.scalar(select(func.count(Decision.id)).where(Decision.state == "marked")) or 0
    return {"by_type": out, "pending_approvals": pending, "bulk_exact": len(_bulk_exact_groups(db))}


@router.get("")
def list_groups(
    type: str | None = None,
    status: str | None = "open",
    owner: str | None = None,
    min_confidence: float | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    q = _group_query(type, status, owner, min_confidence)
    total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    ids = list(db.scalars(q.offset((page - 1) * page_size).limit(page_size)))
    groups = {
        g.id: g for g in db.scalars(select(DupGroup).where(DupGroup.id.in_(ids)).options(selectinload(DupGroup.members)))
    }
    item_ids = {m.item_id for g in groups.values() for m in g.members}
    prim = {
        f.item_id: f
        for f in db.scalars(select(File).where(File.item_id.in_(item_ids), File.role == "primary"))
    }
    names = Names(db)
    out = []
    for gid in ids:
        g = groups[gid]
        members = sorted(g.members, key=lambda m: (not m.suggested_keep, -m.score))
        out.append({
            "id": g.id,
            "type": g.type,
            "status": g.status,
            "confidence": g.confidence,
            "size": len(g.members),
            "members": [
                {
                    "item_id": m.item_id,
                    "file_id": prim[m.item_id].id if m.item_id in prim else None,
                    "suggested_keep": m.suggested_keep,
                    "owner": names(prim[m.item_id].owner_id) if m.item_id in prim else None,
                    "status": prim[m.item_id].status if m.item_id in prim else None,
                }
                for m in members[:8]
            ],
            "reclaim_bytes": sum(prim[m.item_id].size for m in g.members if not m.suggested_keep and m.item_id in prim),
        })
    return {"total": total, "page": page, "page_size": page_size, "groups": out}


@router.get("/queue")
def queue(
    type: str | None = None,
    status: str | None = "open",
    owner: str | None = None,
    min_confidence: float | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    """Ordered group ids for keyboard navigation in the Review screen."""
    return {"ids": list(db.scalars(_group_query(type, status, owner, min_confidence).limit(20000)))}


def group_detail(db: Session, g: DupGroup) -> dict:
    names = Names(db)
    item_ids = [m.item_id for m in g.members]
    items = {
        it.id: it for it in db.scalars(select(Item).where(Item.id.in_(item_ids)).options(selectinload(Item.files)))
    }
    decisions = {
        d.item_id: d
        for d in db.scalars(
            select(Decision)
            .where(Decision.group_id == g.id, Decision.state.in_(ACTIVE_STATES))
            .order_by(Decision.id)
        )
    }
    deleting_elsewhere = set(
        db.scalars(
            select(Decision.item_id).where(
                Decision.item_id.in_(item_ids), Decision.state.in_(DELETE_STATES),
                (Decision.group_id != g.id) | Decision.group_id.is_(None),
            )
        )
    )
    other_groups: dict[int, list[int]] = {}
    for iid, gid in db.execute(
        select(DupGroupMember.item_id, DupGroupMember.group_id).where(
            DupGroupMember.item_id.in_(item_ids), DupGroupMember.group_id != g.id
        )
    ):
        other_groups.setdefault(iid, []).append(gid)

    members = []
    for m in sorted(g.members, key=lambda m: (not m.suggested_keep, -m.score, m.item_id)):
        it = items.get(m.item_id)
        if it is None:
            continue
        d = decisions.get(m.item_id)
        members.append({
            **item_dict(it, names),
            "match_type": m.match_type,
            "suggested_keep": m.suggested_keep,
            "score": m.score,
            "score_breakdown": m.score_breakdown,
            "decision": {
                "id": d.id, "action": d.action, "state": d.state,
                "marked_by": names(d.marked_by), "marked_at": iso(d.marked_at),
                "approved_by": names(d.approved_by),
            } if d else None,
            "deleting_elsewhere": m.item_id in deleting_elsewhere,
            "other_groups": other_groups.get(m.item_id, []),
        })
    return {"id": g.id, "type": g.type, "status": g.status, "confidence": g.confidence, "members": members}


@router.get("/bulk-exact")
def bulk_exact_preview(db: Session = Depends(get_db), _: User = Depends(current_user)):
    groups = _bulk_exact_groups(db)
    files = 0
    reclaim = 0
    by_owner: dict[str, int] = {}
    names = Names(db)
    for g, keep, drop in groups:
        files += len(drop)
        for f in drop:
            reclaim += f.size
            owner = names(f.owner_id) or "Unassigned"
            by_owner[owner] = by_owner.get(owner, 0) + 1
    sample = [
        {"group_id": g.id, "keep_file_id": keep.id, "drop_file_ids": [f.id for f in drop]}
        for g, keep, drop in groups[:24]
    ]
    return {"groups": len(groups), "files": files, "reclaim_bytes": reclaim, "by_owner": by_owner, "sample": sample}


@router.post("/bulk-exact")
def bulk_exact_accept(db: Session = Depends(get_db), user: User = Depends(current_user)):
    groups = _bulk_exact_groups(db)
    marked = 0
    skipped = 0
    for g, keep, drop in groups:
        try:
            with db.begin_nested():
                decisions = [(keep.item_id, "keep")] + [(f.item_id, "delete") for f in drop]
                workflow.mark(db, user, g.id, decisions)  # type: ignore[arg-type]
                marked += len(drop)
        except workflow.WorkflowError:
            skipped += 1
    audit(db, user.id, "bulk_mark_exact", None, None, groups=len(groups), files=marked, skipped=skipped)
    return {"groups": len(groups) - skipped, "files": marked, "skipped": skipped}


def _bulk_exact_groups(db: Session) -> list[tuple[DupGroup, File, list[File]]]:
    """Open exact groups where nobody has decided anything yet."""
    decided = select(Decision.group_id).where(Decision.state.in_(ACTIVE_STATES), Decision.group_id.is_not(None))
    groups = list(
        db.scalars(
            select(DupGroup)
            .where(DupGroup.type == "exact", DupGroup.status == "open", DupGroup.id.not_in(decided))
            .options(selectinload(DupGroup.members))
            .order_by(DupGroup.id)
        )
    )
    item_ids = {m.item_id for g in groups for m in g.members}
    prim = {
        f.item_id: f
        for f in db.scalars(select(File).where(File.item_id.in_(item_ids), File.role == "primary"))
    }
    out = []
    for g in groups:
        keep = next((prim.get(m.item_id) for m in g.members if m.suggested_keep), None)
        if keep is None or keep.status not in ("active", "pending_delete"):
            continue
        drop = [
            prim[m.item_id] for m in g.members
            if not m.suggested_keep and m.item_id in prim and prim[m.item_id].status in ("active", "pending_delete")
        ]
        if drop:
            out.append((g, keep, drop))
    return out


@router.get("/{group_id}")
def get_group(group_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    g = db.scalar(select(DupGroup).where(DupGroup.id == group_id).options(selectinload(DupGroup.members)))
    if g is None:
        raise HTTPException(404, "Group not found")
    return group_detail(db, g)


@router.post("/{group_id}/decisions")
def post_decisions(
    group_id: int, body: DecisionsIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    workflow.mark(db, user, group_id, [(d.item_id, d.action) for d in body.decisions])
    db.flush()
    g = db.get(DupGroup, group_id)
    assert g is not None
    return group_detail(db, g)


@router.post("/{group_id}/dismiss")
def dismiss(group_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = workflow.dismiss_group(db, user, group_id, True)
    return {"id": g.id, "status": g.status}


@router.post("/{group_id}/reopen")
def reopen(group_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = workflow.dismiss_group(db, user, group_id, False)
    return {"id": g.id, "status": g.status}
