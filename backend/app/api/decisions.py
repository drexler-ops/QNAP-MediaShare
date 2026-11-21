"""Pending approvals (stage 2), quarantine (restore / purge) and the audit log."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..deletion import workflow
from ..models import AuditLog, Decision, DupGroupMember, File, Item, User
from ..settings_store import get_settings
from .deps import current_user, get_db
from .serialize import Names, iso, item_dict

router = APIRouter(prefix="/api", tags=["approvals"])


class IdsIn(BaseModel):
    ids: list[int]


class PurgeIn(BaseModel):
    item_ids: list[int] | None = None  # None = everything eligible


def _items(db: Session, ids: set[int]) -> dict[int, Item]:
    return {it.id: it for it in db.scalars(select(Item).where(Item.id.in_(ids)).options(selectinload(Item.files)))}


def _kept_copy(db: Session, group_id: int | None, exclude_item: int) -> int | None:
    """The item in the same group that is being kept (explicitly, or the suggestion)."""
    if not group_id:
        return None
    keep = db.scalar(
        select(Decision.item_id).where(
            Decision.group_id == group_id, Decision.action == "keep", Decision.state == "recorded",
            Decision.item_id != exclude_item,
        )
    )
    if keep:
        return keep
    return db.scalar(
        select(DupGroupMember.item_id).where(
            DupGroupMember.group_id == group_id, DupGroupMember.suggested_keep.is_(True),
            DupGroupMember.item_id != exclude_item,
        )
    )


def _decision_rows(db: Session, decisions: list[Decision]) -> list[dict]:
    names = Names(db)
    kept_ids = {d.id: _kept_copy(db, d.group_id, d.item_id) for d in decisions}
    items = _items(db, {d.item_id for d in decisions} | {k for k in kept_ids.values() if k})
    rows = []
    for d in decisions:
        it = items.get(d.item_id)
        if it is None:
            continue
        kept = items.get(kept_ids[d.id]) if kept_ids[d.id] else None
        rows.append({
            "decision_id": d.id,
            "group_id": d.group_id,
            "marked_by": names(d.marked_by),
            "marked_at": iso(d.marked_at),
            "item": item_dict(it, names),
            "kept": item_dict(kept, names) if kept else None,
        })
    return rows


@router.get("/pending")
def pending(db: Session = Depends(get_db), user: User = Depends(current_user)):
    marked = list(db.scalars(select(Decision).where(Decision.state == "marked").order_by(Decision.id)))
    items = _items(db, {d.item_id for d in marked})
    mine, others = [], []
    for d in marked:
        it = items.get(d.item_id)
        owner = workflow.item_owner_id(it) if it else None
        if owner is None or owner == user.id:
            mine.append(d)
        elif d.marked_by == user.id:
            others.append(d)
    return {"to_approve": _decision_rows(db, mine), "waiting_on_others": _decision_rows(db, others)}


@router.get("/pending/count")
def pending_count(db: Session = Depends(get_db), user: User = Depends(current_user)):
    owned_items = select(File.item_id).where(File.role == "primary", or_(File.owner_id == user.id, File.owner_id.is_(None)))
    n = db.scalar(
        select(func.count(Decision.id)).where(Decision.state == "marked", Decision.item_id.in_(owned_items))
    )
    return {"count": n or 0}


@router.post("/decisions/approve")
def approve(body: IdsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return workflow.approve(db, user, body.ids)


@router.post("/decisions/reject")
def reject(body: IdsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"done": workflow.reject(db, user, body.ids)}


@router.post("/decisions/cancel")
def cancel(body: IdsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {"done": workflow.cancel(db, user, body.ids)}


# --- quarantine ---------------------------------------------------------------------------


@router.get("/quarantine")
def quarantine_list(db: Session = Depends(get_db), _: User = Depends(current_user)):
    settings = get_settings(db)
    purge_days = int(settings["purge_days"])
    cutoff = workflow.purge_eligible_cutoff(purge_days)
    files = list(
        db.scalars(
            select(File).where(File.status == "quarantined", File.role == "primary").order_by(File.quarantined_at.desc())
        )
    )
    items = _items(db, {f.item_id for f in files if f.item_id})
    names = Names(db)
    approvals = {
        d.item_id: d
        for d in db.scalars(
            select(Decision).where(Decision.item_id.in_(items.keys()), Decision.state == "quarantined").order_by(Decision.id)
        )
    }
    rows = []
    for f in files:
        it = items.get(f.item_id or -1)
        if it is None:
            continue
        d = approvals.get(it.id)
        rows.append({
            "item": item_dict(it, names),
            "quarantined_at": iso(f.quarantined_at),
            "purge_after": iso(f.quarantined_at + timedelta(days=purge_days)) if f.quarantined_at else None,
            "eligible": bool(f.quarantined_at and f.quarantined_at <= cutoff),
            "marked_by": names(d.marked_by) if d else None,
            "approved_by": names(d.approved_by) if d else None,
        })
    total_bytes = sum(r["item"]["primary"]["size"] for r in rows if r["item"]["primary"])
    return {
        "purge_days": purge_days,
        "auto_purge": settings["auto_purge"],
        "items": rows,
        "total_bytes": total_bytes,
        "eligible": sum(1 for r in rows if r["eligible"]),
    }


@router.post("/quarantine/restore")
def restore(body: IdsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return workflow.restore(db, user, body.ids)


@router.post("/quarantine/purge")
def purge(body: PurgeIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    settings = get_settings(db)
    return workflow.purge(db, user, int(settings["purge_days"]), body.item_ids)


# --- audit --------------------------------------------------------------------------------


@router.get("/audit")
def audit_log(
    user_id: int | None = None,
    action: str | None = None,
    target_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    q = select(AuditLog)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if action:
        q = q.where(AuditLog.action == action)
    if target_id:
        q = q.where(AuditLog.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    names = Names(db)
    rows = [
        {
            "id": a.id, "ts": iso(a.ts), "user": names(a.user_id) or "system", "action": a.action,
            "target_type": a.target_type, "target_id": a.target_id, "details": a.details,
        }
        for a in db.scalars(q.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
    ]
    actions = list(db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))
    return {"total": total, "page": page, "rows": rows, "actions": actions}
