"""Upload endpoints (signed-in users). Resumable: init -> PUT chunks at offsets -> complete."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import uploads as svc
from ..models import User
from .deps import current_user, get_db

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


class StartIn(BaseModel):
    filename: str = Field(min_length=1, max_length=400)
    size: int = Field(gt=0)
    last_modified: int | None = None  # ms since epoch (File.lastModified in the browser)
    album_id: int | None = None


def _wrap(fn, *args):
    try:
        return fn(*args)
    except svc.UploadError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/config")
def upload_config(user: User = Depends(current_user)):
    return {
        "folder": user.home_folder,
        "destination": f"{user.home_folder}/Uploads/<year-month>/" if user.home_folder else None,
        "max_bytes": svc.max_bytes(),
        "chunk_size": svc.CHUNK_SIZE,
        "extensions": svc.ALLOWED_EXTS,
    }


@router.post("")
def start(body: StartIn, user: User = Depends(current_user)):
    meta = _wrap(svc.start, user, body.filename, body.size, body.last_modified, body.album_id)
    return {"id": meta.id, "filename": meta.filename, "received": 0, "chunk_size": svc.CHUNK_SIZE}


@router.get("/{upload_id}")
def status(upload_id: str, user: User = Depends(current_user)):
    return {"received": _wrap(svc.received, upload_id, user)}


@router.put("/{upload_id}")
async def put_chunk(upload_id: str, request: Request, offset: int = Query(ge=0), user: User = Depends(current_user)):
    try:
        got = await svc.append(upload_id, user, offset, request.stream())
    except svc.UploadError as e:
        raise HTTPException(e.status, str(e)) from e
    return {"received": got}


@router.post("/{upload_id}/complete")
def complete(upload_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _wrap(svc.complete, db, upload_id, user)


@router.delete("/{upload_id}")
def cancel(upload_id: str, user: User = Depends(current_user)):
    _wrap(svc.cancel, upload_id, user)
    return {"ok": True}
