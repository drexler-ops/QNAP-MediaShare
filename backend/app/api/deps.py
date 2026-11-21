from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import new_session
from ..models import User


def get_db() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    # Deactivated users, and users whose password an admin reset, are signed out immediately.
    if user is None or not user.is_active or user.must_set_password:
        request.session.pop("uid", None)
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only an admin can do that")
    return user
