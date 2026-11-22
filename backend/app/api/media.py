"""Thumbnails, in-app viewing (full-size images, playable videos) and explicit downloads.

The serve_* helpers are shared with the public share-link routes (api/public.py), which apply
their own access checks before calling them.
"""
from __future__ import annotations

import os
import posixpath
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import File, User
from ..paths import abs_path
from ..scanner.hashing import open_image_bytes
from ..scanner.thumbs import full_path, preview_path, thumb_path
from .deps import current_user, get_db

router = APIRouter(prefix="/api/files", tags=["media"])

BROWSER_SAFE_VIDEO = {"h264"}
# Image formats every browser renders natively (served as-is); others are converted once to JPEG.
BROWSER_SAFE_IMAGE = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
CACHE = {"Cache-Control": "private, max-age=86400"}


def _file(db: Session, file_id: int) -> File:
    f = db.get(File, file_id)
    if f is None:
        raise HTTPException(404, "Not found")
    return f


def source_path(f: File) -> str:
    if f.status == "purged":
        raise HTTPException(410, "This file was permanently deleted")
    path = abs_path(f.path)
    if not os.path.isfile(path):
        raise HTTPException(404, "File is not on disk")
    return path


def serve_thumb(file_id: int, size: str) -> FileResponse:
    if size not in ("s", "l"):
        raise HTTPException(400, "size must be s or l")
    path = thumb_path(file_id, size)
    if not os.path.isfile(path):
        raise HTTPException(404, "Thumbnail not ready")
    return FileResponse(path, media_type="image/webp", headers=CACHE)


def serve_full(f: File) -> FileResponse:
    if f.kind != "image":
        raise HTTPException(400, "Not an image")
    src = source_path(f)
    if f.ext in BROWSER_SAFE_IMAGE:
        return FileResponse(src, media_type=BROWSER_SAFE_IMAGE[f.ext], headers=CACHE)
    out = full_path(f.id)
    if not os.path.isfile(out):
        with open(src, "rb") as fh:
            img = open_image_bytes(fh.read(), f.ext, src)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = f"{out}.{uuid.uuid4().hex}.tmp"
        img.save(tmp, "JPEG", quality=90, optimize=True)
        os.replace(tmp, out)
    return FileResponse(out, media_type="image/jpeg", headers=CACHE)


def serve_download(f: File) -> FileResponse:
    src = source_path(f)
    name = posixpath.basename(f.original_path or f.path)
    return FileResponse(src, media_type=f.mime or "application/octet-stream", filename=name,
                        content_disposition_type="attachment")


def _direct_playable(f: File) -> bool:
    return f.codec in BROWSER_SAFE_VIDEO and f.ext in ("mp4", "m4v", "mov")


def preview_state(db: Session, f: File, retry: bool = False) -> dict:
    """Is a browser-playable version ready? Starts converting (HEVC etc.) on first ask."""
    from .. import previews

    if f.kind != "video":
        raise HTTPException(400, "Not a video")
    if _direct_playable(f) and os.path.isfile(abs_path(f.path)):
        return {"status": "ready"}
    st = previews.request(f.id, f.path, retry=retry)
    if st["status"] == "ready" and (f.duration or 0) > previews.PREVIEW_SECONDS:
        st["preview_seconds"] = previews.PREVIEW_SECONDS  # only the start is playable in the app
    return st


def serve_preview(f: File) -> FileResponse:
    if f.kind != "video":
        raise HTTPException(400, "Not a video")
    if _direct_playable(f) and os.path.isfile(abs_path(f.path)):
        return FileResponse(abs_path(f.path), media_type="video/mp4")
    out = preview_path(f.id)
    if os.path.isfile(out):
        return FileResponse(out, media_type="video/mp4", headers=CACHE)
    raise HTTPException(404, "Preview not ready")


# --- signed-in routes -----------------------------------------------------------------------


@router.get("/{file_id}/thumb")
def thumb(file_id: int, size: str = "s", _: User = Depends(current_user)):
    return serve_thumb(file_id, size)


@router.get("/{file_id}/full")
def full(file_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Full-resolution image the browser can display inline (for the in-app viewer; never a download)."""
    return serve_full(_file(db, file_id))


@router.get("/{file_id}/original")
def original(file_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """The untouched original file, always as a download (viewing happens in-app via /full and /preview)."""
    return serve_download(_file(db, file_id))


@router.get("/{file_id}/preview/status")
def preview_status(file_id: int, retry: bool = False, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return preview_state(db, _file(db, file_id), retry=retry)


@router.get("/{file_id}/preview")
def preview(file_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return serve_preview(_file(db, file_id))
