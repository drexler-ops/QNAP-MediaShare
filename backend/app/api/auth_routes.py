"""Sign-in, first-login password setup, and changing your own password."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..auth import hash_password, verify_password
from ..models import User
from .deps import current_user, get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

MIN_PASSWORD = 6


class LoginIn(BaseModel):
    username: str
    password: str = ""


class SetupIn(BaseModel):
    new_password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def user_dict(u: User) -> dict:
    return {"id": u.id, "name": u.name, "home_folder": u.home_folder, "is_admin": u.is_admin}


def check_new_password(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise HTTPException(status_code=400, detail=f"Use at least {MIN_PASSWORD} characters")


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    request.session.clear()
    user = db.scalar(select(User).where(func.lower(User.name) == body.username.strip().lower()))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Wrong name or password")
    if user.must_set_password:
        # New account, or an admin reset it: the user chooses a password before getting in.
        request.session["setup_uid"] = user.id
        return {"needs_password_setup": True, "name": user.name}
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Wrong name or password")
    request.session["uid"] = user.id
    audit(db, user.id, "login")
    return user_dict(user)


@router.post("/setup-password")
def setup_password(body: SetupIn, request: Request, db: Session = Depends(get_db)):
    uid = request.session.get("setup_uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active or not user.must_set_password:
        request.session.clear()
        raise HTTPException(status_code=400, detail="Start again from the sign-in page")
    check_new_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    user.must_set_password = False
    request.session.clear()
    request.session["uid"] = user.id
    audit(db, user.id, "set_password", "user", user.id)
    return user_dict(user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_dict(user)


@router.post("/password")
def change_password(body: PasswordIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is wrong")
    check_new_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    audit(db, user.id, "change_password", "user", user.id)
    return {"ok": True}
