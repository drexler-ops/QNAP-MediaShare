"""Thumbnails, originals and browser-playable video previews."""
from __future__ import annotations

import os
import posixpath

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..jobs import enqueue
from ..models import File, Job, User
from ..paths import abs_path
from ..scanner.thumbs import preview_path, thumb_path
from .deps import current_user, get_db

router = APIRouter(prefix="/api/files", tags=["media"])

BROWSER_SAFE_VIDEO = {"h264"}
CACHE = {"Cache-Control": "private, max-age=86400"}


def _file(db: Session, file_id: int) -> File:
    f = db.get(File, file_id)
    if f is None:
        raise HTTPException(404, "Not found")
    return f


@router.get("/{file_id}/thumb")
def thumb(file_id: int, size: str = "s", db: Session = Depends(get_db), _: User = Depends(current_user)):
    if size not in ("s", "l"):
        raise HTTPException(400, "size must be s or l")
    path = thumb_path(file_id, size)
    if not os.path.isfile(path):
        raise HTTPException(404, "Thumbnail not ready")
    return FileResponse(path, media_type="image/webp", headers=CACHE)


@router.get("/{file_id}/original")
def original(
    file_id: int, download: bool = False, db: Session = Depends(get_db), _: User = Depends(current_user)
):
    f = _file(db, file_id)
    if f.status == "purged":
        raise HTTPException(410, "This file was permanently deleted")
    path = abs_path(f.path)
    if not os.path.isfile(path):
        raise HTTPException(404, "File is not on disk")
    name = posixpath.basename(f.original_path or f.path)
    return FileResponse(
        path,
        media_type=f.mime or None,
        filename=name,
        content_disposition_type="attachment" if download else "inline",
    )


def _direct_playable(f: File) -> bool:
    return f.codec in BROWSER_SAFE_VIDEO and f.ext in ("mp4", "m4v", "mov")


@router.get("/{file_id}/preview/status")
def preview_status(file_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Is a browser-playable version ready? Queues a background transcode (HEVC etc.) on first ask."""
    f = _file(db, file_id)
    if f.kind != "video":
        raise HTTPException(400, "Not a video")
    if (_direct_playable(f) and os.path.isfile(abs_path(f.path))) or os.path.isfile(preview_path(f.id)):
        return {"status": "ready"}
    jobs = [
        j for j in db.scalars(select(Job).where(Job.type == "preview").order_by(Job.id.desc()).limit(200))
        if (j.payload or {}).get("file_id") == f.id
    ]
    if any(j.state in ("queued", "running") for j in jobs):
        return {"status": "processing"}
    if jobs and jobs[0].state == "error":
        return {"status": "failed", "error": (jobs[0].error or "").splitlines()[0][:300]}
    enqueue(db, "preview", {"file_id": f.id}, priority=10)
    return {"status": "processing"}


@router.get("/{file_id}/preview")
def preview(file_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    f = _file(db, file_id)
    if f.kind != "video":
        raise HTTPException(400, "Not a video")
    if _direct_playable(f) and os.path.isfile(abs_path(f.path)):
        return FileResponse(abs_path(f.path), media_type="video/mp4")
    out = preview_path(f.id)
    if os.path.isfile(out):
        return FileResponse(out, media_type="video/mp4", headers=CACHE)
    raise HTTPException(404, "Preview not ready")
