"""Albums (signed-in users): create, curate, share with other users, and manage public share links."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import albums as svc
from ..audit import audit
from ..auth import hash_password
from ..config import config
from ..models import Album, AlbumItem, Item, ShareLink, User
from .deps import current_user, get_db
from .serialize import Names, iso, item_dict

router = APIRouter(prefix="/api/albums", tags=["albums"])


class AlbumIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    item_ids: list[int] = []


class AlbumPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    cover_item_id: int | None = None


class ItemsIn(BaseModel):
    item_ids: list[int]


class MemberIn(BaseModel):
    user_id: int
    role: str = "viewer"


class MembersIn(BaseModel):
    members: list[MemberIn]


class LinkIn(BaseModel):
    expires_days: int | None = Field(default=None, ge=1, le=3650)
    password: str | None = None
    allow_download: bool = False


def _load(db: Session, album_id: int) -> Album | None:
    return db.scalar(
        select(Album)
        .where(Album.id == album_id)
        .options(selectinload(Album.items), selectinload(Album.members), selectinload(Album.links))
    )


def _guard(album: Album | None, user: User, need: str) -> tuple[Album, str]:
    try:
        role = svc.require(album, user, need)
    except svc.AlbumError as e:
        raise HTTPException(e.status, str(e)) from e
    assert album is not None
    return album, role


def _cover_file(db: Session, album: Album) -> int | None:
    iid = album.cover_item_id or (album.items[0].item_id if album.items else None)
    if not iid:
        return None
    item = db.scalar(select(Item).where(Item.id == iid).options(selectinload(Item.files)))
    return item.primary.id if item and item.primary else None


def share_url(token: str) -> str:
    return f"{config.public_share_url}/s/{token}" if config.public_share_url else f"/s/{token}"


def link_dict(link: ShareLink, names: Names) -> dict:
    return {
        "id": link.id,
        "url": share_url(link.token),
        "token": link.token,
        "state": svc.link_state(link),
        "created_by": names(link.created_by),
        "created_at": iso(link.created_at),
        "expires_at": iso(link.expires_at),
        "has_password": link.password_hash is not None,
        "allow_download": link.allow_download,
        "view_count": link.view_count,
        "last_viewed_at": iso(link.last_viewed_at),
    }


def summary(db: Session, album: Album, user: User, names: Names) -> dict:
    return {
        "id": album.id,
        "title": album.title,
        "description": album.description,
        "owner": names(album.owner_id),
        "owner_id": album.owner_id,
        "role": svc.role_of(album, user),
        "count": len(album.items),
        "cover_file_id": _cover_file(db, album),
        "shared_with": len(album.members),
        "active_links": sum(1 for link in album.links if svc.link_state(link) == "active"),
        "updated_at": iso(album.updated_at),
    }


def detail(db: Session, album: Album, user: User) -> dict:
    names = Names(db)
    role = svc.role_of(album, user)
    ids = [ai.item_id for ai in album.items]
    items = {it.id: it for it in db.scalars(select(Item).where(Item.id.in_(ids)).options(selectinload(Item.files)))}
    out = summary(db, album, user, names)
    out["cover_item_id"] = album.cover_item_id
    out["items"] = [
        {**item_dict(items[ai.item_id], names), "added_by": names(ai.added_by), "added_at": iso(ai.added_at)}
        for ai in album.items if ai.item_id in items
    ]
    out["members"] = [{"user_id": m.user_id, "name": names(m.user_id), "role": m.role} for m in album.members]
    out["links"] = [link_dict(link, names) for link in sorted(album.links, key=lambda x: x.id, reverse=True)] if role == "owner" else []
    return out


@router.get("")
def list_albums(db: Session = Depends(get_db), user: User = Depends(current_user)):
    names = Names(db)
    return {"albums": [summary(db, a, user, names) for a in svc.visible_albums(db, user)]}


@router.get("/editable")
def editable(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Albums the user can add to (for the 'Add to album' picker)."""
    names = Names(db)
    return {
        "albums": [
            summary(db, a, user, names) for a in svc.visible_albums(db, user) if svc.role_of(a, user) in ("owner", "editor")
        ]
    }


@router.post("")
def create(body: AlbumIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album = Album(title=body.title.strip(), description=(body.description or "").strip() or None, owner_id=user.id)
    db.add(album)
    db.flush()
    audit(db, user.id, "album_create", "album", album.id, title=album.title)
    if body.item_ids:
        svc.add_items(db, album, user, body.item_ids)
    db.flush()
    return detail(db, album, user)


@router.get("/{album_id}")
def get_album(album_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "view")
    return detail(db, album, user)


@router.patch("/{album_id}")
def update(album_id: int, body: AlbumPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, role = _guard(_load(db, album_id), user, "edit")
    if (body.title is not None or body.description is not None) and role != "owner":
        raise HTTPException(403, "Only the album's owner can rename it")
    if body.title is not None:
        album.title = body.title.strip()
    if body.description is not None:
        album.description = body.description.strip() or None
    if body.cover_item_id is not None:
        if body.cover_item_id not in {ai.item_id for ai in album.items}:
            raise HTTPException(400, "The cover must be a photo in this album")
        album.cover_item_id = body.cover_item_id
    album.updated_at = datetime.now()
    audit(db, user.id, "album_update", "album", album.id, **body.model_dump(exclude_none=True))
    return detail(db, album, user)


@router.delete("/{album_id}")
def delete(album_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "own")
    audit(db, user.id, "album_delete", "album", album.id, title=album.title, items=len(album.items))
    db.delete(album)  # photos themselves are untouched; links stop working
    return {"ok": True}


@router.post("/{album_id}/items")
def add(album_id: int, body: ItemsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "edit")
    added = svc.add_items(db, album, user, body.item_ids)
    db.flush()
    return {"added": added, "count": len(album.items)}


@router.post("/{album_id}/items/remove")
def remove(album_id: int, body: ItemsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "edit")
    removed = svc.remove_items(db, album, user, body.item_ids)
    db.flush()
    return {"removed": removed, "count": len(album.items)}


@router.put("/{album_id}/order")
def order(album_id: int, body: ItemsIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "edit")
    svc.reorder(album, body.item_ids)
    db.flush()
    return {"ok": True}


@router.put("/{album_id}/members")
def members(album_id: int, body: MembersIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "own")
    try:
        svc.set_members(db, album, user, [(m.user_id, m.role) for m in body.members])
    except svc.AlbumError as e:
        raise HTTPException(e.status, str(e)) from e
    db.flush()
    return detail(db, album, user)


@router.post("/{album_id}/links")
def create_link(album_id: int, body: LinkIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "own")
    pw = (body.password or "").strip()
    if pw and len(pw) < 4:
        raise HTTPException(400, "Use at least 4 characters for a link password")
    link = ShareLink(
        album_id=album.id,
        token=svc.new_token(),
        created_by=user.id,
        expires_at=datetime.now() + timedelta(days=body.expires_days) if body.expires_days else None,
        password_hash=hash_password(pw) if pw else None,
        allow_download=body.allow_download,
    )
    db.add(link)
    db.flush()
    audit(db, user.id, "share_link_create", "album", album.id, link_id=link.id, expires_days=body.expires_days,
          password=bool(pw), allow_download=body.allow_download)
    return link_dict(link, Names(db))


@router.post("/{album_id}/links/{link_id}/revoke")
def revoke_link(album_id: int, link_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    album, _ = _guard(_load(db, album_id), user, "own")
    link = next((x for x in album.links if x.id == link_id), None)
    if link is None:
        raise HTTPException(404, "Link not found")
    if link.revoked_at is None:
        link.revoked_at = datetime.now()
        audit(db, user.id, "share_link_revoke", "album", album.id, link_id=link.id)
    return link_dict(link, Names(db))


@router.get("/for-item/{item_id}")
def albums_for_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Albums (visible to the user) that contain an item, for the viewer's info panel."""
    rows = db.scalars(select(AlbumItem.album_id).where(AlbumItem.item_id == item_id))
    ids = set(rows)
    return {
        "albums": [{"id": a.id, "title": a.title} for a in svc.visible_albums(db, user) if a.id in ids]
    }
