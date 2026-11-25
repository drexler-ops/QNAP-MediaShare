"""A tiny Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED), shared by NAS + PC workers."""
from __future__ import annotations

import logging
import re
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
    """Requeue jobs left 'running' by a crashed/restarted worker: this worker's own processes (exactly
    "<name>-<n>", so helper "pc" never touches helper "pc-office"'s jobs), or anything stuck over 6 h."""
    cutoff = datetime.now() - timedelta(hours=6)
    own = re.compile(rf"{re.escape(worker_prefix)}-\d+")
    ids = [jid for jid, worker, started in session.execute(
        select(Job.id, Job.worker, Job.started_at).where(Job.state == "running")
    ) if (worker and own.fullmatch(worker)) or (started is not None and started < cutoff)]
    if ids:
        session.execute(update(Job).where(Job.id.in_(ids)).values(state="queued", worker=None))
    session.commit()
    return len(ids)


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

    from .scanner.sidecar import backfill

    res = scan(session)
    side = backfill(session)  # Takeout .json / Apple .xmp copied in after their photos were indexed
    batches = enqueue_batches(session, res.to_process, "full", PROCESS_BATCH)
    if batches == 0:
        never_grouped = session.scalar(select(Job.id).where(Job.type == "group").limit(1)) is None
        changed = res.new or res.changed or res.missing or res.returned or side["dates"] or side["xmp_dates"]
        if changed or never_grouped or (job.payload or {}).get("force_group"):
            maybe_enqueue_group(session)
    return {
        "seen": res.seen, "new": res.new, "changed": res.changed, "missing": res.missing,
        "returned": res.returned, "batches": batches,
        **{k: v for k, v in (
            ("takeout_dates", side["dates"]), ("takeout_locations", side["locations"]),
            ("xmp_read", side["xmp_read"]), ("xmp_dates", side["xmp_dates"]), ("xmp_locations", side["xmp_locations"]),
        ) if v},
    }


def handle_process(session: Session, job: Job) -> dict[str, Any]:
    from .scanner.process import hash_files, process_files

    ids = list(job.payload.get("ids", []))
    if job.payload.get("mode") == "sha":
        result: dict[str, Any] = {"hashed": hash_files(session, ids)}
    else:
        result = process_files(session, ids)
        if get_settings(session)["faces_enabled"]:
            ready = list(session.scalars(select(File.id).where(
                File.id.in_(ids), File.kind == "image", File.thumb_ready.is_(True), File.face_state == "pending")))
            if ready:
                enqueue(session, "faces", {"ids": ready}, priority=-1)
        enqueue_previews(session, ids)  # new/changed videos get a playable copy before anyone opens them
    maybe_enqueue_group(session, exclude_job_id=job.id)
    return result


FACE_BATCH = 25


def handle_faces_scan(session: Session, job: Job) -> dict[str, Any]:
    """Queue face detection for every photo that hasn't had it yet."""
    ids = list(session.scalars(select(File.id).where(
        File.kind == "image", File.thumb_ready.is_(True), File.face_state == "pending",
        File.status.in_(("active", "pending_delete")))))
    n = 0
    for i in range(0, len(ids), FACE_BATCH):
        enqueue(session, "faces", {"ids": ids[i:i + FACE_BATCH]}, priority=-1)
        n += 1
    if not n:
        maybe_enqueue_face_group(session)
    return {"photos": len(ids), "batches": n}


def handle_faces(session: Session, job: Job) -> dict[str, Any]:
    from .faces.pipeline import process_faces

    result = process_faces(session, list(job.payload.get("ids", [])))
    maybe_enqueue_face_group(session, exclude_job_id=job.id)
    return result


def maybe_enqueue_face_group(session: Session, exclude_job_id: int | None = None) -> bool:
    q = select(func.count(Job.id)).where(Job.type == "faces", Job.state.in_(("queued", "running")))
    if exclude_job_id is not None:
        q = q.where(Job.id != exclude_job_id)
    if session.scalar(q):
        return False
    return enqueue(session, "face_group", unique=True, priority=-1) is not None


def handle_face_group(session: Session, job: Job) -> dict[str, Any]:
    from .faces.grouping import run_face_grouping

    return run_face_grouping(session)


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


PREVIEW_BATCH = 10


def enqueue_previews(session: Session, ids: list[int] | None = None) -> int:
    """Queue background conversion for videos browsers can't play as-is (ids=None: whole library).

    Low priority, after indexing and uploads; Live Photo motion clips (small, opened most) first.
    """
    from .previews import needs_preview

    q = select(File).where(File.kind == "video", File.status.in_(("active", "pending_delete")), File.process_state == "done")
    if ids is not None:
        q = q.where(File.id.in_(ids))
    todo = [f for f in session.scalars(q.order_by(File.id)) if needs_preview(f)]
    n = 0
    for live in (True, False):
        batch = [f.id for f in todo if (f.role == "live_video") == live]
        for i in range(0, len(batch), PREVIEW_BATCH):
            enqueue(session, "preview", {"ids": batch[i:i + PREVIEW_BATCH]}, priority=-1 if live else -2)
            n += 1
    return n


def handle_preview(session: Session, job: Job) -> dict[str, Any]:
    """Make browser-playable copies ahead of time, so nobody waits when opening a video."""
    from .previews import convert_now, needs_preview

    ids = job.payload.get("ids") or [job.payload["file_id"]]
    done, failed = 0, []
    for f in session.scalars(select(File).where(File.id.in_(ids))):
        if not needs_preview(f):
            continue  # already converted (maybe on demand) or no longer needed
        try:
            convert_now(f)
            done += 1
        except Exception as exc:  # one bad clip must not stop the batch; the viewer reports it if opened
            log.warning("preview for %s failed: %s", f.path, str(exc)[-200:])
            failed.append(f.id)
    return {"converted": done, "failed": failed}


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
    "faces_scan": handle_faces_scan,
    "faces": handle_faces,
    "face_group": handle_face_group,
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
