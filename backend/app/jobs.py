"""A tiny Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED), shared by NAS + PC workers."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .config import config
from .db import session_scope
from .models import File, Job
from .settings_store import get_settings

log = logging.getLogger(__name__)

PROCESS_BATCH = 50
SHA_BATCH = 25
MAX_ATTEMPTS = 3


def enqueue(
    session: Session, type_: str, payload: dict | None = None, priority: int = 0, unique: bool = False
) -> Job | None:
    if unique:
        exists = session.scalar(
            select(Job.id).where(Job.type == type_, Job.state == "queued").limit(1)
        )
        if exists:
            return None
    job = Job(type=type_, payload=payload or {}, priority=priority, state="queued")
    session.add(job)
    session.flush()
    return job


def claim(session: Session, worker: str) -> Job | None:
    job = session.scalar(
        select(Job)
        .where(Job.state == "queued")
        .order_by(Job.priority.desc(), Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    job.state = "running"
    job.worker = worker
    job.started_at = datetime.now()
    job.attempts += 1
    session.commit()
    return job


def reset_stale(session: Session, worker_prefix: str) -> int:
    """Requeue jobs left 'running' by a crashed/restarted worker."""
    cutoff = datetime.now() - timedelta(hours=6)
    res = session.execute(
        update(Job)
        .where(Job.state == "running")
        .where((Job.worker.like(f"{worker_prefix}-%")) | (Job.started_at < cutoff))
        .values(state="queued", worker=None)
    )
    session.commit()
    return res.rowcount or 0


def maybe_enqueue_group(session: Session, exclude_job_id: int | None = None) -> bool:
    """Queue a regroup once no processing work is outstanding."""
    q = select(func.count(Job.id)).where(Job.type == "process", Job.state.in_(("queued", "running")))
    if exclude_job_id is not None:
        q = q.where(Job.id != exclude_job_id)
    if session.scalar(q):
        return False
    return enqueue(session, "group", unique=True) is not None


def enqueue_batches(session: Session, ids: list[int], mode: str, size: int) -> int:
    n = 0
    for i in range(0, len(ids), size):
        enqueue(session, "process", {"ids": ids[i : i + size], "mode": mode})
        n += 1
    return n


# --- handlers ---------------------------------------------------------------------------


def handle_scan(session: Session, job: Job) -> dict[str, Any]:
    from .scanner.walker import scan

    res = scan(session)
    batches = enqueue_batches(session, res.to_process, "full", PROCESS_BATCH)
    if batches == 0:
        never_grouped = session.scalar(select(Job.id).where(Job.type == "group").limit(1)) is None
        changed = res.new or res.changed or res.missing or res.returned
        if changed or never_grouped or (job.payload or {}).get("force_group"):
            maybe_enqueue_group(session)
    return {
        "seen": res.seen, "new": res.new, "changed": res.changed, "missing": res.missing,
        "returned": res.returned, "batches": batches,
    }


def handle_process(session: Session, job: Job) -> dict[str, Any]:
    from .scanner.process import hash_files, process_files

    ids = list(job.payload.get("ids", []))
    if job.payload.get("mode") == "sha":
        result: dict[str, Any] = {"hashed": hash_files(session, ids)}
    else:
        result = process_files(session, ids)
    maybe_enqueue_group(session, exclude_job_id=job.id)
    return result


def _sha_needed(session: Session) -> list[int]:
    """Files whose size collides with another file but which have no SHA-256 yet."""
    live = (File.role == "primary", File.status.in_(("active", "pending_delete")), File.process_state == "done")
    dup_sizes = select(File.size).where(*live).group_by(File.size).having(func.count(File.id) > 1)
    return list(session.scalars(select(File.id).where(*live, File.sha256.is_(None), File.size.in_(dup_sizes))))


def handle_group(session: Session, job: Job) -> dict[str, Any]:
    from .dedup.grouping import run_grouping

    need = _sha_needed(session)
    if need:
        batches = enqueue_batches(session, need, "sha", SHA_BATCH)
        return {"deferred_for_sha": len(need), "batches": batches}
    return run_grouping(session)


def handle_preview(session: Session, job: Job) -> dict[str, Any]:
    from .paths import abs_path
    from .scanner.thumbs import preview_path
    from .scanner.video import make_preview

    f = session.get(File, job.payload["file_id"])
    if f is None:
        return {"skipped": True}
    make_preview(abs_path(f.path), preview_path(f.id))
    return {"file_id": f.id}


def handle_purge(session: Session, job: Job) -> dict[str, Any]:
    from .deletion.workflow import purge

    settings = get_settings(session)
    res = purge(session, None, int(settings["purge_days"]))
    return {"purged_items": len(res["done"]), "failed": len(res["failed"])}


HANDLERS: dict[str, Callable[[Session, Job], dict[str, Any]]] = {
    "scan": handle_scan,
    "process": handle_process,
    "group": handle_group,
    "preview": handle_preview,
    "purge": handle_purge,
}


def run_job(job_id: int) -> None:
    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            result = HANDLERS[job.type](session, job)
            job.state = "done"
            job.result = result
            job.error = None
            job.finished_at = datetime.now()
    except Exception as exc:
        log.exception("job %s failed", job_id)
        with session_scope() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.error = f"{exc}\n{traceback.format_exc()[-2000:]}"
            job.finished_at = datetime.now()
            job.state = "queued" if job.attempts < MAX_ATTEMPTS else "error"
            job.worker = None


def worker_prefix() -> str:
    return config.worker_name
