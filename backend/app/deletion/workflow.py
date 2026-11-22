"""Two-stage manual deletion: mark -> owner approves -> quarantine -> (restore | purge).

Rules enforced here (not just in the UI):
  * Every duplicate group must keep at least one live copy.
  * Only the owner of a file (or anyone, for files outside a user folder) can approve its deletion.
  * Nothing is ever deleted outright: approval moves files to quarantine; purge is a later, separate step.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..audit import audit
from ..models import ACTIVE_STATES, DELETE_STATES, Decision, DupGroup, DupGroupMember, File, Item, User
from ..scanner.thumbs import delete_thumbs
from . import quarantine
from .. import albums as album_service

log = logging.getLogger(__name__)


class WorkflowError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _item(session: Session, item_id: int) -> Item:
    item = session.scalar(select(Item).where(Item.id == item_id).options(selectinload(Item.files)))
    if item is None:
        raise WorkflowError(f"item {item_id} not found", 404)
    return item


def item_owner_id(item: Item) -> int | None:
    p = item.primary
    return p.owner_id if p else None


def active_decision(session: Session, group_id: int | None, item_id: int) -> Decision | None:
    return session.scalar(
        select(Decision)
        .where(Decision.group_id == group_id, Decision.item_id == item_id, Decision.state.in_(ACTIVE_STATES))
        .order_by(Decision.id.desc())
    )


def _item_deleting(session: Session, item_id: int) -> bool:
    return (
        session.scalar(
            select(Decision.id).where(Decision.item_id == item_id, Decision.state.in_(DELETE_STATES)).limit(1)
        )
        is not None
    )


def _item_alive(item: Item) -> bool:
    p = item.primary
    return bool(p and p.status in ("active", "pending_delete"))


def _check_survivors(session: Session, group_ids: Iterable[int]) -> None:
    for gid in set(group_ids):
        group = session.get(DupGroup, gid)
        if group is None:
            continue
        survivors = 0
        for m in group.members:
            item = _item(session, m.item_id)
            if _item_alive(item) and not _item_deleting(session, m.item_id):
                survivors += 1
        if survivors == 0:
            raise WorkflowError(f"Group {gid} must keep at least one copy.")


def refresh_group_status(session: Session, group: DupGroup) -> None:
    if group.status == "dismissed":
        return
    decided = 0
    for m in group.members:
        d = active_decision(session, group.id, m.item_id)
        item_gone = _item_deleting(session, m.item_id)
        if item_gone or (d is not None and d.action in ("keep", "delete")):
            decided += 1
    group.status = "resolved" if group.members and decided == len(group.members) else "open"


def _set_files_status(item: Item, status: str) -> None:
    for f in item.files:
        if f.status in ("active", "pending_delete"):
            f.status = status


def _groups_of_item(session: Session, item_id: int) -> list[int]:
    return list(session.scalars(select(DupGroupMember.group_id).where(DupGroupMember.item_id == item_id)))


# --- stage 1: mark ------------------------------------------------------------------------


def mark(session: Session, user: User, group_id: int, decisions: list[tuple[int, str]]) -> list[Decision]:
    group = session.get(DupGroup, group_id)
    if group is None:
        raise WorkflowError("group not found", 404)
    member_ids = {m.item_id for m in group.members}
    created: list[Decision] = []
    affected_groups = {group_id}

    for item_id, action in decisions:
        if action not in ("keep", "delete", "skip"):
            raise WorkflowError(f"invalid action {action!r}")
        if item_id not in member_ids:
            raise WorkflowError(f"item {item_id} is not in group {group_id}")
        item = _item(session, item_id)
        current = active_decision(session, group_id, item_id)
        if current and current.state in ("approved", "quarantined", "purged"):
            if action == "delete":
                continue  # already on its way out; nothing to do
            raise WorkflowError(f"item {item_id} is already {current.state}; restore it from Quarantine first")
        if current and current.action == action:
            continue
        if current:
            current.state = "superseded"
            current.resolved_by, current.resolved_at = user.id, _now()

        if action == "delete":
            if not _item_alive(item):
                raise WorkflowError(f"item {item_id} no longer exists on disk")
            # A delete is global for the item: retire keep-decisions about it in other groups.
            for other in session.scalars(
                select(Decision).where(
                    Decision.item_id == item_id,
                    Decision.group_id != group_id,
                    Decision.state == "recorded",
                    Decision.action == "keep",
                )
            ):
                other.state = "superseded"
                other.resolved_by, other.resolved_at = user.id, _now()
            affected_groups.update(_groups_of_item(session, item_id))
            state = "marked"
            _set_files_status(item, "pending_delete")
        else:
            state = "recorded"
        d = Decision(group_id=group_id, item_id=item_id, action=action, state=state, marked_by=user.id)
        session.add(d)
        session.flush()
        if current and current.state == "superseded" and current.action == "delete" and action != "delete":
            if not _item_deleting(session, item_id):
                _set_files_status(item, "active")
        created.append(d)
        audit(session, user.id, f"mark_{action}", "item", item_id, group_id=group_id, decision_id=d.id)

    session.flush()
    _check_survivors(session, affected_groups)
    for gid in affected_groups:
        g = session.get(DupGroup, gid)
        if g:
            refresh_group_status(session, g)
    return created


def dismiss_group(session: Session, user: User, group_id: int, dismissed: bool = True) -> DupGroup:
    group = session.get(DupGroup, group_id)
    if group is None:
        raise WorkflowError("group not found", 404)
    if dismissed:
        pending = session.scalar(
            select(Decision.id).where(Decision.group_id == group_id, Decision.state == "marked").limit(1)
        )
        if pending:
            raise WorkflowError("Cancel the pending deletions in this group before dismissing it.")
        group.status = "dismissed"
    else:
        group.status = "open"
        refresh_group_status(session, group)
    audit(session, user.id, "dismiss_group" if dismissed else "reopen_group", "group", group_id)
    return group


# --- stage 2: owner approval -> quarantine -------------------------------------------------


def _require_owner(user: User, item: Item) -> None:
    owner = item_owner_id(item)
    if owner is not None and owner != user.id:
        raise WorkflowError("Only the file's owner can approve or reject its deletion.", 403)


def approve(session: Session, user: User, decision_ids: list[int]) -> dict[str, list]:
    done: list[int] = []
    failed: list[dict] = []
    for did in decision_ids:
        d = session.get(Decision, did)
        if d is None or d.state != "marked":
            failed.append({"id": did, "error": "not awaiting approval"})
            continue
        item = _item(session, d.item_id)
        _require_owner(user, item)
        now = _now()
        d.state, d.approved_by, d.approved_at = "approved", user.id, now
        moved: list[tuple[File, str]] = []
        try:
            for f in item.files:
                if f.status not in ("active", "pending_delete"):
                    continue
                qrel = quarantine.move_to_quarantine(f.path, now)
                moved.append((f, qrel))
        except Exception as exc:
            # Roll back any partial move so a Live Photo is never half-quarantined.
            for f, qrel in moved:
                try:
                    quarantine.move_back(qrel, f.path)
                except Exception:  # pragma: no cover
                    log.exception("could not undo quarantine of %s", f.path)
            d.error = str(exc)[:500]
            failed.append({"id": did, "error": str(exc)})
            audit(session, user.id, "quarantine_failed", "item", d.item_id, decision_id=did, error=str(exc))
            continue
        for f, qrel in moved:
            f.original_path, f.path = f.path, qrel
            f.status, f.quarantined_at = "quarantined", now
        d.state, d.error = "quarantined", None
        # Other pending delete marks for the same item (from other groups) follow along.
        for other in session.scalars(
            select(Decision).where(Decision.item_id == d.item_id, Decision.id != d.id, Decision.state == "marked")
        ):
            other.state, other.approved_by, other.approved_at = "quarantined", user.id, now
        audit(session, user.id, "approve_quarantine", "item", d.item_id, decision_id=did,
              files=[{"from": f.original_path, "to": f.path} for f, _ in moved])
        session.flush()
        # Albums that contained the deleted copy now show the copy being kept instead.
        album_service.remap_deleted_item(session, d.item_id, d.group_id, user.id)
        done.append(did)
        for gid in _groups_of_item(session, d.item_id):
            g = session.get(DupGroup, gid)
            if g:
                refresh_group_status(session, g)
    session.flush()
    return {"done": done, "failed": failed}


def reject(session: Session, user: User, decision_ids: list[int]) -> list[int]:
    out = []
    for did in decision_ids:
        d = session.get(Decision, did)
        if d is None or d.state != "marked":
            continue
        item = _item(session, d.item_id)
        _require_owner(user, item)
        d.state, d.resolved_by, d.resolved_at = "rejected", user.id, _now()
        session.flush()
        if not _item_deleting(session, d.item_id):
            _set_files_status(item, "active")
        audit(session, user.id, "reject_delete", "item", d.item_id, decision_id=did)
        if d.group_id:
            g = session.get(DupGroup, d.group_id)
            if g:
                refresh_group_status(session, g)
        out.append(did)
    return out


def cancel(session: Session, user: User, decision_ids: list[int]) -> list[int]:
    """The person who marked something (or the owner) withdraws a pending deletion."""
    out = []
    for did in decision_ids:
        d = session.get(Decision, did)
        if d is None or d.state != "marked":
            continue
        item = _item(session, d.item_id)
        if d.marked_by != user.id and item_owner_id(item) not in (None, user.id):
            raise WorkflowError("Only the person who marked it or the owner can cancel.", 403)
        d.state, d.resolved_by, d.resolved_at = "cancelled", user.id, _now()
        session.flush()
        if not _item_deleting(session, d.item_id):
            _set_files_status(item, "active")
        audit(session, user.id, "cancel_delete", "item", d.item_id, decision_id=did)
        if d.group_id:
            g = session.get(DupGroup, d.group_id)
            if g:
                refresh_group_status(session, g)
        out.append(did)
    return out


# --- quarantine: restore / purge -----------------------------------------------------------


def restore(session: Session, user: User, item_ids: list[int]) -> dict[str, list]:
    done, failed = [], []
    for item_id in item_ids:
        item = _item(session, item_id)
        qfiles = [f for f in item.files if f.status == "quarantined" and f.original_path]
        if not qfiles:
            failed.append({"id": item_id, "error": "not in quarantine"})
            continue
        try:
            for f in qfiles:
                quarantine.move_back(f.path, f.original_path)
                f.path, f.original_path = f.original_path, None
                f.status, f.quarantined_at = "active", None
        except Exception as exc:
            failed.append({"id": item_id, "error": str(exc)})
            continue
        for d in session.scalars(
            select(Decision).where(Decision.item_id == item_id, Decision.state.in_(("approved", "quarantined")))
        ):
            d.state, d.resolved_by, d.resolved_at = "restored", user.id, _now()
        audit(session, user.id, "restore", "item", item_id, files=[f.path for f in qfiles])
        session.flush()
        for gid in _groups_of_item(session, item_id):
            g = session.get(DupGroup, gid)
            if g:
                refresh_group_status(session, g)
        done.append(item_id)
    return {"done": done, "failed": failed}


def purge_eligible_cutoff(purge_days: int) -> datetime:
    return _now() - timedelta(days=purge_days)


def purge(
    session: Session, user: User | None, purge_days: int, item_ids: list[int] | None = None
) -> dict[str, list]:
    """Permanently delete quarantined files older than purge_days (optionally only the given items)."""
    cutoff = purge_eligible_cutoff(purge_days)
    q = select(File).where(File.status == "quarantined", File.quarantined_at <= cutoff)
    if item_ids is not None:
        q = q.where(File.item_id.in_(item_ids))
    files = list(session.scalars(q))
    done_items: set[int] = set()
    failed = []
    for f in files:
        try:
            quarantine.purge_file(f.path)
        except Exception as exc:
            failed.append({"id": f.id, "error": str(exc)})
            continue
        f.status = "purged"
        delete_thumbs(f.id)
        if f.item_id:
            done_items.add(f.item_id)
        audit(session, user.id if user else None, "purge", "file", f.id, path=f.path, original=f.original_path)
    for item_id in done_items:
        item = _item(session, item_id)
        if all(f.status == "purged" for f in item.files):
            for d in session.scalars(
                select(Decision).where(Decision.item_id == item_id, Decision.state == "quarantined")
            ):
                d.state = "purged"
                d.resolved_by = user.id if user else None
                d.resolved_at = _now()
    session.flush()
    return {"done": sorted(done_items), "failed": failed}
