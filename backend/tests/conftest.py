import os
import shutil
import sys
import tempfile

import pytest

# Configure the app for tests *before* importing it.
_TMP = tempfile.mkdtemp(prefix="photoaudit-test-")
os.environ["LIBRARY_ROOT"] = os.path.join(_TMP, "library")
os.environ["THUMBS_ROOT"] = os.path.join(_TMP, "thumbs")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "adminpass123"
os.environ["SKIP_INIT_DB"] = "1"
os.environ["SECRET_KEY"] = "test"
os.environ["FRONTEND_DIST"] = os.path.join(_TMP, "no-frontend")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db as dbmod  # noqa: E402
from app.config import config  # noqa: E402
from app.models import Base, User  # noqa: E402


@pytest.fixture
def library():
    for d in (config.library_root, config.thumbs_root):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    return config.library_root


@pytest.fixture
def session(library):
    engine = dbmod.configure("sqlite://")
    Base.metadata.create_all(engine)
    s = dbmod.new_session()
    dbmod.seed_admin(s)
    # Two ordinary users who own the "Alex" and "Sam" folders (password: secret123).
    from app.auth import hash_password

    for name in ("Alex", "Sam"):
        s.add(User(name=name, home_folder=name, password_hash=hash_password("secret123")))
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def users(session):
    from sqlalchemy import select

    return {u.name: u for u in session.scalars(select(User))}
