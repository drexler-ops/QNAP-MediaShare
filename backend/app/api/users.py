"""User management. Listing is open to everyone; creating/changing users is admin-only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..models import File, User
from ..paths import owner_folder
from .deps import current_user, get_db, require_admin

router = APIRouter(prefix="/api/users", tags=["users"])


class UserIn(BaseModel):
    name: str
    home_folder: str | None = None
    is_admin: bool = False


class UserPatch(BaseModel):
    name: str | None = None
    home_folder: str | None = None
    is_admin: bool | None = None
    is_active: bool | None = None


def reassign_owners(db: Session) -> int:
    """Recompute file ownership from top-level folder names. Returns rows changed."""
    folders = {u.home_folder.lower(): u.id for u in db.scalars(select(User)) if u.home_folder}
    updates: list[tuple[int, int | None]] = []
    for fid, path, original, owner in db.execute(select(File.id, File.path, File.original_path, File.owner_id)):
        new_owner = folders.get(owner_folder(original or path).lower())
        if new_owner != owner:
            updates.append((fid, new_owner))
    for fid, new_owner in updates:
        db.get(File, fid).owner_id = new_owner  # type: ignore[union-attr]
    return len(updates)


def _out(u: User, counts: dict[int | None, int]) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "home_folder": u.home_folder,
        "is_admin": u.is_admin,
        "is_active": u.is_active,
        "must_set_password": u.must_set_password,
        "files": counts.get(u.id, 0),
    }


def _file_counts(db: Session) -> dict[int | None, int]:
    return dict(
        db.execute(
            select(File.owner_id, func.count(File.id))
            .where(File.status.in_(("active", "pending_delete")), File.role == "primary")
            .group_by(File.owner_id)
        ).all()
    )


def _check_folder(db: Session, folder: str | None, user_id: int | None) -> None:
    if not folder:
        return
    if "/" in folder or "\\" in folder:
        raise HTTPException(400, "Folder must be a single top-level folder name, e.g. 'alex'")
    q = select(User).where(func.lower(User.home_folder) == folder.lower())
    if user_id is not None:
        q = q.where(User.id != user_id)
    clash = db.scalar(q)
    if clash:
        raise HTTPException(400, f"{clash.name} already uses that folder")


def _other_active_admins(db: Session, user_id: int) -> int:
    return db.scalar(
        select(func.count(User.id)).where(User.is_admin.is_(True), User.is_active.is_(True), User.id != user_id)
    ) or 0


@router.get("")
def list_users(db: Session = Depends(get_db), _: User = Depends(current_user)):
    counts = _file_counts(db)
    users = [_out(u, counts) for u in db.scalars(select(User).order_by(User.name))]
    return {"users": users, "unassigned": counts.get(None, 0)}


@router.post("")
def create_user(body: UserIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if db.scalar(select(User).where(func.lower(User.name) == name.lower())):
        raise HTTPException(400, "That name is taken")
    folder = (body.home_folder or "").strip() or None
    _check_folder(db, folder, None)
    # No password yet: the user chooses one the first time they sign in.
    u = User(name=name, home_folder=folder, is_admin=body.is_admin, password_hash=None, must_set_password=True)
    db.add(u)
    db.flush()
    moved = reassign_owners(db)
    audit(db, admin.id, "create_user", "user", u.id, name=name, home_folder=folder, is_admin=body.is_admin,
          files_reassigned=moved)
    return _out(u, _file_counts(db))


@router.patch("/{user_id}")
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "No such user")
    changes: dict = {}
    if body.name is not None and body.name.strip() and body.name.strip() != u.name:
        new_name = body.name.strip()
        if db.scalar(select(User).where(func.lower(User.name) == new_name.lower(), User.id != u.id)):
            raise HTTPException(400, "That name is taken")
        changes["name"] = {"from": u.name, "to": new_name}
        u.name = new_name
    if body.home_folder is not None:
        folder = body.home_folder.strip() or None
        if folder != u.home_folder:
            _check_folder(db, folder, u.id)
            changes["home_folder"] = {"from": u.home_folder, "to": folder}
            u.home_folder = folder
    removing_admin = (body.is_admin is False and u.is_admin) or (body.is_active is False and u.is_active and u.is_admin)
    if removing_admin and _other_active_admins(db, u.id) == 0:
        raise HTTPException(400, "There must always be at least one active admin")
    if body.is_admin is not None and body.is_admin != u.is_admin:
        changes["is_admin"] = body.is_admin
        u.is_admin = body.is_admin
    if body.is_active is not None and body.is_active != u.is_active:
        changes["is_active"] = body.is_active
        u.is_active = body.is_active
    db.flush()
    if "home_folder" in changes:
        changes["files_reassigned"] = reassign_owners(db)
    if changes:
        audit(db, admin.id, "update_user", "user", u.id, **changes)
    return _out(u, _file_counts(db))


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Forgotten password: the user is signed out and picks a new password at their next sign-in."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "No such user")
    u.must_set_password = True
    u.password_hash = None
    audit(db, admin.id, "reset_password", "user", u.id, name=u.name)
    return _out(u, _file_counts(db))
