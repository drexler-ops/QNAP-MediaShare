"""Background worker: N job-processing processes + (optionally) the scheduler.

    python -m app.worker
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import signal
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from .config import config

log = logging.getLogger("worker")


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(processName)s %(levelname)s %(name)s: %(message)s")


def job_loop(index: int) -> None:
    _setup_logging()
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # parent handles shutdown
    from .db import new_session
    from .jobs import claim, run_job

    name = f"{config.worker_name}-{index}"
    log.info("worker %s started", name)
    idle = 0.0
    while True:
        session = new_session()
        try:
            job = claim(session, name)
            job_id = job.id if job else None
            if job:
                log.info("running job %s (%s)", job.id, job.type)
        except Exception:
            log.exception("claim failed")
            job_id = None
            time.sleep(5)
        finally:
            session.close()
        if job_id is None:
            idle = min(idle + 1, 5)
            time.sleep(idle)
            continue
        idle = 0
        run_job(job_id)


def scheduler_tick() -> None:
    from .db import session_scope
    from .jobs import enqueue
    from .models import Job
    from .settings_store import get_settings

    with session_scope() as s:
        settings = get_settings(s)
        now = datetime.now()
        if not settings["scans_paused"]:
            busy = s.scalar(
                select(Job.id).where(Job.type.in_(("scan", "process")), Job.state.in_(("queued", "running"))).limit(1)
            )
            last = s.scalar(select(Job.created_at).where(Job.type == "scan").order_by(Job.id.desc()).limit(1))
            interval = timedelta(minutes=max(int(settings["scan_interval_minutes"]), 1))
            if not busy and (last is None or now - last >= interval):
                enqueue(s, "scan", {"trigger": "schedule"})
                log.info("scheduled scan queued")
        if settings["auto_purge"]:
            last = s.scalar(select(Job.created_at).where(Job.type == "purge").order_by(Job.id.desc()).limit(1))
            if last is None or now - last >= timedelta(hours=24):
                enqueue(s, "purge", {"trigger": "schedule"})


def wait_for_db() -> None:
    from .db import tables_exist

    while not tables_exist():
        log.info("waiting for database schema (is the api container running?)")
        time.sleep(5)


def main() -> None:
    from .config import apply_umask

    apply_umask()
    _setup_logging()
    wait_for_db()
    from .db import session_scope
    from .jobs import reset_stale

    with session_scope() as s:
        n = reset_stale(s, config.worker_name)
        if n:
            log.info("requeued %s stale jobs", n)

    ctx = mp.get_context("spawn")
    procs: dict[int, mp.Process] = {}

    def start(i: int) -> None:
        p = ctx.Process(target=job_loop, args=(i,), name=f"job-{i}", daemon=True)
        p.start()
        procs[i] = p

    for i in range(max(config.worker_concurrency, 1)):
        start(i)

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    log.info(
        "worker %s: %s processes, scheduler %s",
        config.worker_name, len(procs), "on" if config.scheduler_enabled else "off",
    )
    last_tick = 0.0
    while not stop:
        for i, p in list(procs.items()):
            if not p.is_alive():
                log.warning("process %s died (exit %s); restarting", p.name, p.exitcode)
                start(i)
        if config.scheduler_enabled and time.time() - last_tick >= 60:
            last_tick = time.time()
            try:
                scheduler_tick()
            except Exception:
                log.exception("scheduler tick failed")
        time.sleep(2)
    for p in procs.values():
        p.terminate()
    log.info("worker stopped")


if __name__ == "__main__":
    main()
