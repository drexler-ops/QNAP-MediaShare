"""JSON shapes shared by several endpoints."""
from __future__ import annotations

import posixpath
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import File, Item, User


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class Names:
    """Caches user id -> name for one request."""

    def __init__(self, session: Session):
        self.map = {u.id: u.name for u in session.scalars(select(User))}

    def __call__(self, uid: int | None) -> str | None:
        return self.map.get(uid) if uid else None


def file_dict(f: File, names: Names) -> dict[str, Any]:
    return {
        "id": f.id,
        "item_id": f.item_id,
        "role": f.role,
        "path": f.path,
        "original_path": f.original_path,
        "name": posixpath.basename(f.original_path or f.path),
        "folder": posixpath.dirname(f.original_path or f.path),
        "ext": f.ext,
        "kind": f.kind,
        "size": f.size,
        "width": f.width,
        "height": f.height,
        "duration": f.duration,
        "codec": f.codec,
        "taken_at": iso(f.taken_at),
        "make": f.make,
        "model": f.model,
        "lens": f.lens,
        "gps_lat": f.gps_lat,
        "gps_lon": f.gps_lon,
        "has_gps": f.has_gps,
        "has_exif": f.has_exif,
        "exif_score": f.exif_score,
        "exif": f.exif,
        "status": f.status,
        "owner_id": f.owner_id,
        "owner": names(f.owner_id) or "Unassigned",
        "thumb": f.thumb_ready,
        "process_state": f.process_state,
        "process_error": f.process_error,
        "first_seen_at": iso(f.first_seen_at),
        "quarantined_at": iso(f.quarantined_at),
    }


def item_dict(item: Item, names: Names) -> dict[str, Any]:
    primary = item.primary
    companion = next((f for f in item.files if f is not primary), None)
    return {
        "id": item.id,
        "primary": file_dict(primary, names) if primary else None,
        "companion": file_dict(companion, names) if companion else None,
        "is_live_photo": companion is not None and companion.role == "live_video",
        "owner_id": primary.owner_id if primary else None,
        "owner": (names(primary.owner_id) if primary else None) or "Unassigned",
    }
