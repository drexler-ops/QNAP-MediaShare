"""Engine / session management and first-run initialisation."""
from __future__ import annotations

import logging
import os
import secrets
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import config
from .models import Base, User

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def configure(url: str) -> Engine:
    """(Re)create the engine. Tests call this with an in-memory SQLite URL."""
    global _engine, _SessionLocal
    if url.startswith("sqlite"):
        _engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    _SessionLocal = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure(config.database_url)
    assert _engine is not None
    return _engine


def new_session() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def tables_exist() -> bool:
    try:
        return inspect(get_engine()).has_table("jobs")
    except Exception:
        return False


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config as AlembicConfig

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "alembic"))
    command.upgrade(cfg, "head")


def init_db(use_migrations: bool | None = None) -> None:
    use_migrations = config.run_migrations if use_migrations is None else use_migrations
    if use_migrations:
        run_migrations()
    else:
        Base.metadata.create_all(get_engine())
    with session_scope() as s:
        seed_admin(s)


def seed_admin(session: Session) -> User | None:
    """Ensure at least one active admin exists. Returns the account if one was created/promoted."""
    from .auth import hash_password

    if session.scalar(select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True)).limit(1)):
        return None
    name = config.admin_username
    user = session.scalar(select(User).where(User.name == name))
    password = config.admin_password or secrets.token_urlsafe(12)
    if user is None:
        user = User(name=name, password_hash=hash_password(password), is_admin=True, is_active=True)
        session.add(user)
    else:  # an account with that name exists (e.g. after an upgrade): make it the admin
        user.is_admin, user.is_active = True, True
        user.password_hash = hash_password(password)
        user.must_set_password = False
    session.flush()
    shown = "(from ADMIN_PASSWORD)" if config.admin_password else password
    print(
        f"\n================ {config.app_name}: admin account ================\n"
        f"    username: {name}\n"
        f"    password: {shown}\n"
        "  Sign in, create everyone's accounts under Settings -> Users,\n"
        "  then change this password under Settings -> My account.\n"
        "============================================================\n",
        flush=True,
    )
    return user
