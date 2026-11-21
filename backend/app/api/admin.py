"""Jobs, scanning controls, settings and library stats."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..jobs import enqueue
from ..models import DupGroup, File, Job, User
from ..settings_store import get_settings, update_settings
from .deps import current_user, get_db
from .serialize import iso

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/status")
def status(db: Session = Depends(get_db), _: User = Depends(current_user)):
    jobs: dict[str, dict[str, int]] = {}
    for t, s, c in db.execute(select(Job.type, Job.state, func.count(Job.id)).group_by(Job.type, Job.state)):
        jobs.setdefault(t, {})[s] = c
    files_by_state = dict(db.execute(select(File.process_state, func.count(File.id)).group_by(File.process_state)).all())
    files_by_status = dict(db.execute(select(File.status, func.count(File.id)).group_by(File.status)).all())
    kinds = dict(
        db.execute(
            select(File.kind, func.count(File.id))
            .where(File.status.in_(("active", "pending_delete")), File.role == "primary")
            .group_by(File.kind)
        ).all()
    )
    total_bytes = db.scalar(select(func.sum(File.size)).where(File.status.in_(("active", "pending_delete")))) or 0
    recent = [
        {
            "id": j.id, "type": j.type, "state": j.state, "worker": j.worker, "attempts": j.attempts,
            "created_at": iso(j.created_at), "started_at": iso(j.started_at), "finished_at": iso(j.finished_at),
            "result": j.result, "error": (j.error or "").splitlines()[0][:300] if j.error else None,
            "payload_size": len((j.payload or {}).get("ids", [])) or None,
        }
        for j in db.scalars(select(Job).where(Job.type != "process").order_by(Job.id.desc()).limit(25))
    ]
    errors = [
        {"id": f.id, "path": f.path, "error": f.process_error}
        for f in db.scalars(select(File).where(File.process_state == "error").order_by(File.id.desc()).limit(50))
    ]
    groups = dict(db.execute(select(DupGroup.status, func.count(DupGroup.id)).group_by(DupGroup.status)).all())
    workers = sorted({j.worker.rsplit("-", 1)[0] for j in db.scalars(select(Job).where(Job.state == "running")) if j.worker})
    return {
        "jobs": jobs,
        "files": {"by_process_state": files_by_state, "by_status": files_by_status, "kinds": kinds, "bytes": total_bytes},
        "groups": groups,
        "recent_jobs": recent,
        "file_errors": errors,
        "active_workers": workers,
    }


@router.post("/scan")
def scan_now(db: Session = Depends(get_db), user: User = Depends(current_user)):
    busy = db.scalar(select(Job.id).where(Job.type == "scan", Job.state.in_(("queued", "running"))).limit(1))
    if busy:
        raise HTTPException(409, "A scan is already queued or running")
    job = enqueue(db, "scan", {"trigger": "manual", "force_group": True}, priority=5)
    audit(db, user.id, "scan_now")
    return {"job_id": job.id if job else None}


@router.post("/regroup")
def regroup(db: Session = Depends(get_db), user: User = Depends(current_user)):
    job = enqueue(db, "group", unique=True, priority=5)
    audit(db, user.id, "regroup")
    return {"job_id": job.id if job else None}


@router.post("/retry-errors")
def retry_errors(db: Session = Depends(get_db), user: User = Depends(current_user)):
    from ..jobs import PROCESS_BATCH, enqueue_batches

    ids = list(db.scalars(select(File.id).where(File.process_state == "error", File.status != "missing")))
    for f in db.scalars(select(File).where(File.id.in_(ids))):
        f.process_state = "pending"
    enqueue_batches(db, ids, "full", PROCESS_BATCH)
    audit(db, user.id, "retry_errors", files=len(ids))
    return {"files": len(ids)}


@router.get("/settings")
def read_settings(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return get_settings(db)


@router.put("/settings")
def write_settings(body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(current_user)):
    before = get_settings(db)
    try:
        after = update_settings(db, body)
    except KeyError as exc:
        raise HTTPException(400, f"Unknown setting {exc}") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    changed = {k: {"from": before[k], "to": after[k]} for k in body if before.get(k) != after.get(k)}
    if changed:
        audit(db, user.id, "update_settings", **changed)
    return after
