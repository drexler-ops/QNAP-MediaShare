"""Uploads from users' devices: resumable chunked transfer -> duplicate check -> filed into the
uploader's folder (<home>/Uploads/<YYYY-MM>/) -> processed immediately.

Chunks are staged in a hidden folder on the library volume (skipped by scans), so finishing an
upload is an instant rename, never a copy.
"""
from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import audit
from .config import config
from .models import File, Item, User
from .paths import IMAGE_EXTS, VIDEO_EXTS, ext_of, kind_of, safe_join
from .scanner.hashing import sha256_file

log = logging.getLogger(__name__)

STAGING = ".uploads-tmp"
CHUNK_SIZE = 8 * 1024 * 1024  # what the browser sends per request
MAX_CHUNK = 32 * 1024 * 1024
STALE_SECONDS = 48 * 3600
LIVE = ("active", "pending_delete")
ALLOWED_EXTS = sorted(IMAGE_EXTS | VIDEO_EXTS)
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class UploadMeta:
    id: str
    user_id: int
    filename: str
    size: int
    mtime: float | None
    album_id: int | None
    created: float


def max_bytes() -> int:
    return int(os.environ.get("MAX_UPLOAD_MB", "20480")) * 1024 * 1024


def staging_dir() -> str:
    d = safe_join(config.library_root, STAGING)
    os.makedirs(d, exist_ok=True)
    return d


def _paths(upload_id: str) -> tuple[str, str]:
    if not _ID_RE.match(upload_id or ""):
        raise UploadError("Upload not found", 404)
    d = staging_dir()
    return os.path.join(d, upload_id + ".part"), os.path.join(d, upload_id + ".json")


def clean_filename(name: str) -> str:
    """Keep the device's file name but make it safe as a single path component."""
    base = posixpath.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", base).strip(". ")
    if not base:
        raise UploadError("Missing file name")
    stem, ext = posixpath.splitext(base)
    return (stem[:150] or "upload") + ext.lower()


def _load(upload_id: str, user: User) -> UploadMeta:
    part, meta_path = _paths(upload_id)
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = UploadMeta(**json.load(fh))
    except (OSError, ValueError, TypeError):
        raise UploadError("Upload not found", 404) from None
    if meta.user_id != user.id:
        raise UploadError("Upload not found", 404)
    if not os.path.exists(part):
        raise UploadError("Upload not found", 404)
    return meta


def received(upload_id: str, user: User) -> int:
    _load(upload_id, user)
    return os.path.getsize(_paths(upload_id)[0])


def cleanup_stale(now: float | None = None) -> int:
    """Remove abandoned partial uploads (older than 48h)."""
    now = now or time.time()
    removed = 0
    d = staging_dir()
    for name in os.listdir(d):
        p = os.path.join(d, name)
        try:
            if now - os.path.getmtime(p) > STALE_SECONDS:
                os.remove(p)
                removed += 1
        except OSError:
            pass
    return removed


def start(user: User, filename: str, size: int, last_modified_ms: int | None, album_id: int | None) -> UploadMeta:
    if not user.home_folder:
        raise UploadError("You don't have a folder yet. Ask an admin to set one under Settings → Users.")
    name = clean_filename(filename)
    if kind_of(name) is None:
        raise UploadError(f"{name}: only photos and videos can be uploaded")
    if size <= 0:
        raise UploadError(f"{name} is empty")
    if size > max_bytes():
        raise UploadError(f"{name} is larger than the {max_bytes() // (1024 * 1024 * 1024)} GB upload limit")
    cleanup_stale()
    meta = UploadMeta(
        id=uuid.uuid4().hex,
        user_id=user.id,
        filename=name,
        size=size,
        mtime=(last_modified_ms / 1000.0) if last_modified_ms else None,
        album_id=album_id,
        created=time.time(),
    )
    part, meta_path = _paths(meta.id)
    open(part, "wb").close()
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(asdict(meta), fh)
    return meta


async def append(upload_id: str, user: User, offset: int, body: AsyncIterator[bytes]) -> int:
    """Append one chunk. `offset` must equal what the server already has (makes retries safe)."""
    meta = _load(upload_id, user)
    part, _ = _paths(upload_id)
    have = os.path.getsize(part)
    if offset != have:
        raise UploadError(f"offset mismatch: server has {have} bytes", 409)
    written = 0
    with open(part, "ab") as fh:
        async for block in body:
            written += len(block)
            if written > MAX_CHUNK or have + written > meta.size:
                fh.truncate(have)
                raise UploadError("Chunk too large", 413)
            fh.write(block)
    return have + written


def cancel(upload_id: str, user: User) -> None:
    _load(upload_id, user)
    for p in _paths(upload_id):
        try:
            os.remove(p)
        except OSError:
            pass


def find_duplicate(session: Session, size: int, sha: str) -> File | None:
    """A byte-identical file already in the library (hashing same-size files that weren't hashed yet)."""
    for f in session.scalars(select(File).where(File.size == size, File.status.in_(LIVE))):
        if f.sha256 is None:
            try:
                f.sha256 = sha256_file(safe_join(config.library_root, f.path))
            except OSError:
                continue
        if f.sha256 == sha:
            return f
    return None


def _taken_at(path: str) -> datetime | None:
    try:
        from .scanner.metadata import parse_exif, run_exiftool

        tags = run_exiftool([path]).get(path)
        return parse_exif(tags)["taken_at"] if tags else None
    except Exception:  # exiftool unavailable or unreadable file: fall back to the file date
        return None


def _unique_rel(rel: str) -> str:
    stem, ext = posixpath.splitext(rel)
    candidate, n = rel, 1
    while os.path.exists(safe_join(config.library_root, candidate)):
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return candidate


def complete(session: Session, upload_id: str, user: User) -> dict:
    from . import albums as album_svc
    from .jobs import enqueue
    from .models import Album

    meta = _load(upload_id, user)
    part, meta_path = _paths(upload_id)
    have = os.path.getsize(part)
    if have != meta.size:
        raise UploadError(f"Upload incomplete: {have} of {meta.size} bytes received", 409)

    def discard() -> None:
        for p in (part, meta_path):
            try:
                os.remove(p)
            except OSError:
                pass

    sha = sha256_file(part)
    dup = find_duplicate(session, meta.size, sha)
    if dup is not None:
        discard()
        session.flush()
        audit(session, user.id, "upload_duplicate", "file", dup.id, filename=meta.filename)
        return {
            "status": "duplicate",
            "filename": meta.filename,
            "existing_path": dup.original_path or dup.path,
            "item_id": dup.item_id,
            "file_id": dup.id,
        }

    when = _taken_at(part) or (datetime.fromtimestamp(meta.mtime) if meta.mtime else None) or datetime.now()
    rel = _unique_rel(f"{user.home_folder}/Uploads/{when:%Y-%m}/{meta.filename}")
    dest = safe_join(config.library_root, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(part, dest)
    if meta.mtime:
        os.utime(dest, (meta.mtime, meta.mtime))
    try:
        os.chmod(dest, 0o664)
    except OSError:
        pass
    try:
        os.remove(meta_path)
    except OSError:
        pass

    st = os.stat(dest)
    f = session.scalar(select(File).where(File.path == rel))  # a scan may have just picked it up
    if f is None:
        item = Item()
        session.add(item)
        session.flush()
        f = File(
            path=rel, owner_id=user.id, item_id=item.id, size=st.st_size, mtime=st.st_mtime,
            ext=ext_of(rel), kind=kind_of(rel) or "image", status="active", process_state="pending", sha256=sha,
        )
        session.add(f)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            f = session.scalar(select(File).where(File.path == rel))
            if f is None:
                raise
    enqueue(session, "process", {"ids": [f.id], "mode": "full"}, priority=5)

    added_to = None
    if meta.album_id:
        album = session.get(Album, meta.album_id)
        if album is not None and album_svc.role_of(album, user) in ("owner", "editor"):
            album_svc.add_items(session, album, user, [f.item_id])
            added_to = album.title
    audit(session, user.id, "upload", "file", f.id, path=rel, size=st.st_size)
    session.flush()
    return {"status": "saved", "filename": meta.filename, "path": rel, "item_id": f.item_id, "file_id": f.id,
            "album": added_to}
