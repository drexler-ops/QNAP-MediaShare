"""Album permissions, membership, ordering, share links, and keeping albums intact when duplicates are deleted."""
from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import audit
from .auth import verify_password
from .models import (
    DELETE_STATES,
    Album,
    AlbumItem,
    AlbumMember,
    Decision,
    DupGroupMember,
    File,
    Item,
    ShareLink,
    User,
)

LIVE = ("active", "pending_delete")


class AlbumError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- permissions ----------------------------------------------------------------------------


def role_of(album: Album, user: User) -> str | None:
    """owner | editor | viewer | None (no access)."""
    if album.owner_id == user.id:
        return "owner"
    for m in album.members:
        if m.user_id == user.id:
            return m.role
    return None


def require(album: Album | None, user: User, need: str) -> str:
    """need: view | edit | own. Unknown and forbidden albums look the same (404) to non-members."""
    role = role_of(album, user) if album else None
    if role is None:
        raise AlbumError("Album not found", 404)
    if need == "edit" and role not in ("owner", "editor"):
        raise AlbumError("You can view this album but not change it", 403)
    if need == "own" and role != "owner":
        raise AlbumError("Only the album's owner can do that", 403)
    return role


def visible_albums(session: Session, user: User) -> list[Album]:
    member_of = select(AlbumMember.album_id).where(AlbumMember.user_id == user.id)
    return list(
        session.scalars(
            select(Album).where((Album.owner_id == user.id) | Album.id.in_(member_of)).order_by(Album.updated_at.desc())
        )
    )


# --- items ----------------------------------------------------------------------------------


def add_items(session: Session, album: Album, user: User, item_ids: list[int]) -> int:
    existing = {ai.item_id for ai in album.items}
    valid = set(session.scalars(select(Item.id).where(Item.id.in_(item_ids))))
    pos = max((ai.position for ai in album.items), default=0.0)
    added = 0
    for iid in item_ids:  # keep the caller's order
        if iid in existing or iid not in valid:
            continue
        pos += 1.0
        album.items.append(AlbumItem(item_id=iid, position=pos, added_by=user.id))
        existing.add(iid)
        added += 1
    if album.cover_item_id is None and album.items:
        album.cover_item_id = album.items[0].item_id
    album.updated_at = datetime.now()
    if added:
        audit(session, user.id, "album_add", "album", album.id, items=added)
    return added


def remove_items(session: Session, album: Album, user: User, item_ids: list[int]) -> int:
    drop = set(item_ids)
    before = len(album.items)
    album.items[:] = [ai for ai in album.items if ai.item_id not in drop]
    removed = before - len(album.items)
    if album.cover_item_id in drop:
        album.cover_item_id = album.items[0].item_id if album.items else None
    album.updated_at = datetime.now()
    if removed:
        audit(session, user.id, "album_remove", "album", album.id, items=removed)
    return removed


def reorder(album: Album, item_ids: list[int]) -> None:
    """Items listed come first in that order; anything not listed keeps its relative order after them."""
    rank = {iid: i for i, iid in enumerate(item_ids)}
    ordered = sorted(album.items, key=lambda ai: (rank.get(ai.item_id, len(rank)), ai.position))
    for i, ai in enumerate(ordered):
        ai.position = float(i + 1)
    album.updated_at = datetime.now()


def set_members(session: Session, album: Album, user: User, members: list[tuple[int, str]]) -> None:
    wanted: dict[int, str] = {}
    for uid, role in members:
        if role not in ("viewer", "editor"):
            raise AlbumError(f"invalid role {role!r}")
        if uid == album.owner_id:
            continue
        wanted[uid] = role
    known = set(session.scalars(select(User.id).where(User.id.in_(list(wanted)))))
    album.members[:] = [m for m in album.members if m.user_id in wanted]
    current = {m.user_id: m for m in album.members}
    for uid, role in wanted.items():
        if uid not in known:
            raise AlbumError(f"unknown user {uid}")
        if uid in current:
            current[uid].role = role
        else:
            album.members.append(AlbumMember(user_id=uid, role=role))
    audit(session, user.id, "album_members", "album", album.id, members={str(k): v for k, v in wanted.items()})


# --- keep albums intact when duplicates are deleted ------------------------------------------


def _alive(session: Session, item_id: int) -> bool:
    status = session.scalar(select(File.status).where(File.item_id == item_id, File.role == "primary"))
    if status not in LIVE:
        return False
    return session.scalar(
        select(Decision.id).where(Decision.item_id == item_id, Decision.state.in_(DELETE_STATES)).limit(1)
    ) is None


def replacement_for(session: Session, item_id: int, group_id: int | None) -> int | None:
    """The copy that survives in the deleted item's duplicate group: an explicit keep, else the suggestion."""
    if not group_id:
        return None
    candidates = list(
        session.scalars(
            select(Decision.item_id).where(
                Decision.group_id == group_id, Decision.action == "keep", Decision.state == "recorded",
                Decision.item_id != item_id,
            )
        )
    ) + list(
        session.scalars(
            select(DupGroupMember.item_id)
            .where(DupGroupMember.group_id == group_id, DupGroupMember.item_id != item_id)
            .order_by(DupGroupMember.suggested_keep.desc(), DupGroupMember.score.desc())
        )
    )
    for cand in candidates:
        if _alive(session, cand):
            return cand
    return None


def remap_deleted_item(session: Session, item_id: int, group_id: int | None, user_id: int | None) -> int:
    """After `item_id` is quarantined, point albums at the copy being kept. Returns albums changed."""
    rows = list(session.scalars(select(AlbumItem).where(AlbumItem.item_id == item_id)))
    if not rows:
        return 0
    keeper = replacement_for(session, item_id, group_id)
    if keeper is None:
        return 0  # nothing to swap to: the album shows it as unavailable (restorable from quarantine)
    changed = 0
    for row in rows:
        album = row.album
        already = any(ai.item_id == keeper for ai in album.items)
        if already:
            session.delete(row)
        else:
            row.item_id = keeper
        if album.cover_item_id == item_id:
            album.cover_item_id = keeper
        audit(session, user_id, "album_remap", "album", album.id, replaced=item_id, with_item=keeper)
        changed += 1
    session.flush()
    return changed


def album_counts(session: Session, item_ids: list[int]) -> dict[int, int]:
    rows = session.execute(
        select(AlbumItem.item_id, func.count(AlbumItem.id)).where(AlbumItem.item_id.in_(item_ids)).group_by(AlbumItem.item_id)
    )
    return dict(rows.all())


# --- share links ----------------------------------------------------------------------------


def new_token() -> str:
    return secrets.token_urlsafe(24)  # 192 bits: not guessable or enumerable


def link_state(link: ShareLink) -> str:
    if link.revoked_at:
        return "revoked"
    if link.expires_at and link.expires_at <= datetime.now():
        return "expired"
    return "active"


def resolve_link(session: Session, token: str) -> ShareLink | None:
    """An active link, or None. Revoked, expired and unknown tokens are indistinguishable to callers."""
    if not token or len(token) > 64:
        return None
    link = session.scalar(select(ShareLink).where(ShareLink.token == token))
    if link is None or link_state(link) != "active":
        return None
    return link


def check_link_password(link: ShareLink, password: str) -> bool:
    return link.password_hash is None or verify_password(password, link.password_hash)


def shared_files(session: Session, album: Album) -> dict[int, File]:
    """file_id -> File for everything a share link may serve (primary files and Live Photo motion)."""
    item_ids = [ai.item_id for ai in album.items]
    out: dict[int, File] = {}
    for f in session.scalars(select(File).where(File.item_id.in_(item_ids), File.status.in_(LIVE))):
        out[f.id] = f
    return out
