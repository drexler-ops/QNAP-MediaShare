"""Browse / search / map endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import DupGroupMember, File, Item, User
from .deps import current_user, get_db
from .serialize import Names, file_dict, item_dict

router = APIRouter(prefix="/api", tags=["items"])


def _filters(
    owner: str | None,
    kind: str | None,
    has_exif: bool | None,
    has_gps: bool | None,
    camera: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str | None,
    q: str | None,
    live: bool | None = None,
) -> list:
    conds: list = [File.role == "primary"]
    if status == "all":
        pass
    elif status:
        conds.append(File.status == status)
    else:
        conds.append(File.status.in_(("active", "pending_delete")))
    if owner == "unassigned":
        conds.append(File.owner_id.is_(None))
    elif owner:
        conds.append(File.owner_id == int(owner))
    if kind in ("image", "video"):
        conds.append(File.kind == kind)
    if has_exif is not None:
        conds.append(File.has_exif.is_(has_exif))
    if has_gps is True:
        conds.append(File.gps_lat.is_not(None))
    elif has_gps is False:
        conds.append(File.gps_lat.is_(None))
    if camera:
        like = f"%{camera.lower()}%"
        conds.append(or_(func.lower(File.model).like(like), func.lower(File.make).like(like)))
    if date_from:
        conds.append(File.taken_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conds.append(File.taken_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if q:
        conds.append(func.lower(func.coalesce(File.original_path, File.path)).like(f"%{q.lower()}%"))
    if live is True:
        conds.append(File.item_id.in_(select(File.item_id).where(File.role == "live_video")))
    return conds


@router.get("/items")
def list_items(
    owner: str | None = None,
    kind: str | None = None,
    has_exif: bool | None = None,
    has_gps: bool | None = None,
    camera: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = None,
    q: str | None = None,
    live: bool | None = None,
    sort: str = "taken_desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    conds = _filters(owner, kind, has_exif, has_gps, camera, date_from, date_to, status, q, live)
    total = db.scalar(select(func.count(File.id)).where(*conds)) or 0
    order = {
        "taken_desc": (File.taken_at.desc().nulls_last(), File.id.desc()),
        "taken_asc": (File.taken_at.asc().nulls_last(), File.id),
        "added_desc": (File.first_seen_at.desc(), File.id.desc()),
        "size_desc": (File.size.desc(), File.id),
        "name": (File.path, File.id),
    }.get(sort, (File.taken_at.desc().nulls_last(), File.id.desc()))
    item_ids = list(
        db.scalars(select(File.item_id).where(*conds).order_by(*order).offset((page - 1) * page_size).limit(page_size))
    )
    items = {
        it.id: it for it in db.scalars(select(Item).where(Item.id.in_(item_ids)).options(selectinload(Item.files)))
    }
    in_groups = set(
        db.scalars(select(DupGroupMember.item_id).where(DupGroupMember.item_id.in_(item_ids)).distinct())
    )
    names = Names(db)
    out = []
    for iid in item_ids:
        if iid in items:
            d = item_dict(items[iid], names)
            d["in_group"] = iid in in_groups
            out.append(d)
    return {"total": total, "page": page, "page_size": page_size, "items": out}


@router.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    item = db.scalar(select(Item).where(Item.id == item_id).options(selectinload(Item.files)))
    if item is None:
        raise HTTPException(404, "Not found")
    names = Names(db)
    groups = list(db.scalars(select(DupGroupMember.group_id).where(DupGroupMember.item_id == item_id)))
    return {**item_dict(item, names), "group_ids": groups}


@router.get("/files/{file_id}")
def get_file(file_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    f = db.get(File, file_id)
    if f is None:
        raise HTTPException(404, "Not found")
    return file_dict(f, Names(db))


@router.get("/facets")
def facets(db: Session = Depends(get_db), _: User = Depends(current_user)):
    live = (File.role == "primary", File.status.in_(("active", "pending_delete")))
    cameras = db.execute(
        select(File.model, func.count(File.id))
        .where(*live, File.model.is_not(None))
        .group_by(File.model)
        .order_by(func.count(File.id).desc())
        .limit(60)
    ).all()
    lo, hi = db.execute(select(func.min(File.taken_at), func.max(File.taken_at)).where(*live)).one()
    return {
        "owners": [{"id": u.id, "name": u.name} for u in db.scalars(select(User).order_by(User.name))],
        "cameras": [{"model": m, "count": c} for m, c in cameras],
        "date_min": lo.date().isoformat() if lo else None,
        "date_max": hi.date().isoformat() if hi else None,
    }


@router.get("/map/points")
def map_points(
    owner: str | None = None,
    kind: str | None = None,
    camera: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(current_user),
):
    conds = _filters(owner, kind, None, True, camera, date_from, date_to, None, None)
    rows = db.execute(select(File.id, File.item_id, File.gps_lat, File.gps_lon).where(*conds).limit(300_000)).all()
    return {"points": [[fid, iid, round(lat, 6), round(lon, 6)] for fid, iid, lat, lon in rows]}
