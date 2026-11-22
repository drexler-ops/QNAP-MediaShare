"""Public, read-only access to albums through share links (/s/<token>). No account needed.

Every request re-validates the link (exists, not revoked, not expired, password unlocked) and that
the requested file belongs to the shared album. Invalid links of any kind return the same 404, so
tokens can't be probed. Responses never include owner names, folder paths or GPS coordinates.
"""
from __future__ import annotations

import io
import posixpath
import threading
import time
import zipfile
from collections import defaultdict, deque
from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import albums as svc
from ..config import config
from ..models import Album, File, Item, ShareLink
from . import media
from .deps import get_db
from .serialize import iso

router = APIRouter(prefix="/api/public", tags=["public"])

NOT_FOUND = "This link doesn't exist or is no longer available."

# --- simple in-process rate limit for password attempts -----------------------------------------
_ATTEMPTS: dict[str, deque] = defaultdict(deque)
_LOCK = threading.Lock()
MAX_ATTEMPTS, WINDOW_S = 8, 300


def _blocked(key: str) -> bool:
    """True once MAX_ATTEMPTS wrong passwords were tried within WINDOW_S."""
    now = time.monotonic()
    with _LOCK:
        q = _ATTEMPTS[key]
        while q and now - q[0] > WINDOW_S:
            q.popleft()
        return len(q) >= MAX_ATTEMPTS


def _record_failure(key: str) -> None:
    with _LOCK:
        _ATTEMPTS[key].append(time.monotonic())


class UnlockIn(BaseModel):
    password: str


def _link(db: Session, token: str) -> ShareLink:
    link = svc.resolve_link(db, token)
    if link is None:
        raise HTTPException(404, NOT_FOUND)
    return link


def _unlocked(request: Request, link: ShareLink) -> bool:
    return link.password_hash is None or link.id in (request.session.get("share_ok") or [])


def _access(request: Request, db: Session, token: str) -> tuple[ShareLink, Album]:
    link = _link(db, token)
    if not _unlocked(request, link):
        raise HTTPException(401, "password_required")
    album = db.scalar(select(Album).where(Album.id == link.album_id).options(selectinload(Album.items)))
    if album is None:
        raise HTTPException(404, NOT_FOUND)
    return link, album


def _shared_file(request: Request, db: Session, token: str, file_id: int) -> tuple[ShareLink, File]:
    link, album = _access(request, db, token)
    f = svc.shared_files(db, album).get(file_id)
    if f is None:
        raise HTTPException(404, NOT_FOUND)
    return link, f


def _public_item(item: Item) -> dict | None:
    p = item.primary
    if p is None or p.status not in svc.LIVE:
        return None
    companion = next((f for f in item.files if f is not p and f.status in svc.LIVE), None)
    return {
        "item_id": item.id,
        "file_id": p.id,
        "kind": p.kind,
        "name": posixpath.basename(p.original_path or p.path),
        "ext": p.ext,
        "width": p.width,
        "height": p.height,
        "duration": p.duration,
        "size": p.size,
        "taken_at": iso(p.taken_at),
        "camera": " ".join(x for x in (p.make, p.model) if x) or None,
        "thumb": p.thumb_ready,
        "live_file_id": companion.id if companion is not None and companion.role == "live_video" else None,
    }


@router.get("/s/{token}")
def album_view(token: str, request: Request, db: Session = Depends(get_db)):
    link = _link(db, token)
    if not _unlocked(request, link):
        # Reveal nothing about the album until the password is given.
        raise HTTPException(401, "password_required")
    album = db.scalar(select(Album).where(Album.id == link.album_id).options(selectinload(Album.items)))
    if album is None:
        raise HTTPException(404, NOT_FOUND)
    ids = [ai.item_id for ai in album.items]
    items = {it.id: it for it in db.scalars(select(Item).where(Item.id.in_(ids)).options(selectinload(Item.files)))}
    out = [x for x in (_public_item(items[i]) for i in ids if i in items) if x]
    link.view_count += 1
    link.last_viewed_at = datetime.now()
    return {
        "app_name": config.app_name,
        "title": album.title,
        "description": album.description,
        "allow_download": link.allow_download,
        "expires_at": iso(link.expires_at),
        "items": out,
    }


@router.post("/s/{token}/unlock")
def unlock(token: str, body: UnlockIn, request: Request, db: Session = Depends(get_db)):
    link = _link(db, token)
    client = request.client.host if request.client else "?"
    key = f"{client}:{link.token}"
    if _blocked(key):
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    if not svc.check_link_password(link, body.password):
        _record_failure(key)
        raise HTTPException(401, "Wrong password")
    ok = set(request.session.get("share_ok") or [])
    ok.add(link.id)
    request.session["share_ok"] = sorted(ok)[-50:]
    return {"ok": True}


@router.get("/s/{token}/files/{file_id}/thumb")
def thumb(token: str, file_id: int, request: Request, size: str = "s", db: Session = Depends(get_db)):
    _shared_file(request, db, token, file_id)
    return media.serve_thumb(file_id, size)


@router.get("/s/{token}/files/{file_id}/full")
def full(token: str, file_id: int, request: Request, db: Session = Depends(get_db)):
    _, f = _shared_file(request, db, token, file_id)
    return media.serve_full(f)


@router.get("/s/{token}/files/{file_id}/preview/status")
def preview_status(token: str, file_id: int, request: Request, retry: bool = False, start: bool = True, db: Session = Depends(get_db)):
    _, f = _shared_file(request, db, token, file_id)
    return media.preview_state(db, f, retry=retry, start=start)


@router.get("/s/{token}/files/{file_id}/preview")
def preview(token: str, file_id: int, request: Request, original: bool = False, db: Session = Depends(get_db)):
    _, f = _shared_file(request, db, token, file_id)
    return media.serve_preview(f, original=original)


@router.get("/s/{token}/files/{file_id}/download")
def download(token: str, file_id: int, request: Request, db: Session = Depends(get_db)):
    link, f = _shared_file(request, db, token, file_id)
    if not link.allow_download:
        raise HTTPException(403, "Downloads are turned off for this link")
    return media.serve_download(f)


class _Sink(io.RawIOBase):
    """Write-only buffer for streaming a ZIP without a temp file."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        self.buf += b
        return len(b)

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _zip_stream(files: list[tuple[str, str, datetime | None]]) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for path, name, when in files:
            dt = (when or datetime(1980, 1, 1)).timetuple()[:6]
            info = zipfile.ZipInfo(name, date_time=dt if dt[0] >= 1980 else (1980, 1, 1, 0, 0, 0))
            try:
                with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                        yield sink.take()
            except FileNotFoundError:
                continue
            yield sink.take()
    yield sink.take()


@router.get("/s/{token}/download.zip")
def download_all(token: str, request: Request, db: Session = Depends(get_db)):
    link, album = _access(request, db, token)
    if not link.allow_download:
        raise HTTPException(403, "Downloads are turned off for this link")
    files = svc.shared_files(db, album)
    order = {ai.item_id: i for i, ai in enumerate(album.items)}
    entries: list[tuple[str, str, datetime | None]] = []
    used: set[str] = set()
    for f in sorted(files.values(), key=lambda x: (order.get(x.item_id or 0, 0), x.role != "primary", x.id)):
        try:
            path = media.source_path(f)
        except HTTPException:
            continue
        name = posixpath.basename(f.original_path or f.path)
        stem, ext = posixpath.splitext(name)
        n = 1
        while name.lower() in used:
            name = f"{stem} ({n}){ext}"
            n += 1
        used.add(name.lower())
        entries.append((path, name, f.taken_at))
    safe_title = "".join(c for c in album.title if c.isalnum() or c in " -_").strip() or "album"
    return StreamingResponse(
        _zip_stream(entries),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.zip"'},
    )
