"""Walk the library and sync the `files` table with what is on disk."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import config
from ..models import File, Item, User
from ..paths import ext_of, kind_of, owner_folder

log = logging.getLogger(__name__)

SKIP_FILE_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}


def _skip_dir(name: str) -> bool:
    # The quarantine folder, hidden dirs (.@__thumb, .snapshot) and NAS system dirs
    # (QNAP @Recycle/@Recently-Snapshot, Synology @eaDir/#recycle).
    return (
        name == config.quarantine_dirname
        or name.startswith(".")
        or name.startswith("@")
        or name.lower() == "#recycle"
    )


def walk_library(root: str) -> Iterator[tuple[str, int, float]]:
    """Yield (relative_path, size, mtime) for every media file under root."""
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        full_dir = os.path.join(root, *rel_dir.split("/")) if rel_dir else root
        try:
            with os.scandir(full_dir) as it:
                entries = list(it)
        except OSError as exc:
            log.warning("cannot list %s: %s", full_dir, exc)
            continue
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not _skip_dir(entry.name):
                        stack.append(rel)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            name = entry.name.lower()
            if name in SKIP_FILE_NAMES or name.startswith("._") or kind_of(name) is None:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            yield rel, st.st_size, st.st_mtime


@dataclass
class ScanResult:
    seen: int = 0
    new: int = 0
    changed: int = 0
    missing: int = 0
    returned: int = 0
    to_process: list[int] = field(default_factory=list)


def scan(session: Session, root: str | None = None, batch_size: int = 500) -> ScanResult:
    root = root or config.library_root
    result = ScanResult()

    owners = {u.home_folder.lower(): u.id for u in session.scalars(select(User)) if u.home_folder}

    existing: dict[str, tuple[int, int, float, str, str]] = {
        row.path: (row.id, row.size, row.mtime, row.status, row.process_state)
        for row in session.execute(select(File.id, File.path, File.size, File.mtime, File.status, File.process_state))
    }
    on_disk: set[str] = set()
    pending_new: list[File] = []

    def flush_new() -> None:
        if not pending_new:
            return
        items = [Item() for _ in pending_new]
        session.add_all(items)
        session.flush()
        for f, it in zip(pending_new, items):
            f.item_id = it.id
        session.add_all(pending_new)
        session.flush()
        result.to_process.extend(f.id for f in pending_new)
        pending_new.clear()
        session.commit()

    for rel, size, mtime in walk_library(root):
        result.seen += 1
        on_disk.add(rel)
        row = existing.get(rel)
        if row is None:
            pending_new.append(
                File(
                    path=rel,
                    size=size,
                    mtime=mtime,
                    ext=ext_of(rel),
                    kind=kind_of(rel) or "image",
                    owner_id=owners.get(owner_folder(rel).lower()),
                    status="active",
                    process_state="pending",
                )
            )
            result.new += 1
            if len(pending_new) >= batch_size:
                flush_new()
            continue

        # (Quarantined/purged rows have paths inside .quarantine/, which the walk skips.)
        file_id, old_size, old_mtime, status, process_state = row
        changed = old_size != size or abs(old_mtime - mtime) > 1.0
        if status == "missing" and not changed:
            f = session.get(File, file_id)
            assert f is not None
            f.status = "active"
            result.returned += 1
        elif changed:
            f = session.get(File, file_id)
            assert f is not None
            f.size, f.mtime = size, mtime
            f.sha256 = f.phash = f.dhash = None
            f.video_sig = None
            f.thumb_ready = False
            f.process_state = "pending"
            if f.status == "missing":
                f.status = "active"
            result.changed += 1
            result.to_process.append(file_id)
        elif process_state == "pending":
            result.to_process.append(file_id)

    flush_new()

    # Anything active we know about that is no longer on disk is "missing".
    missing_ids = [
        fid for path, (fid, _s, _m, status, _p) in existing.items() if path not in on_disk and status == "active"
    ]
    for i in range(0, len(missing_ids), batch_size):
        for f in session.scalars(select(File).where(File.id.in_(missing_ids[i : i + batch_size]))):
            f.status = "missing"
    result.missing = len(missing_ids)
    session.commit()
    log.info(
        "scan: seen=%s new=%s changed=%s missing=%s returned=%s",
        result.seen, result.new, result.changed, result.missing, result.returned,
    )
    return result
