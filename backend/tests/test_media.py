import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.scanner.thumbs import clear_derived, full_path

from .test_api import login
from .test_workflow import make_file


def image_bytes(fmt: str, size=(64, 48)) -> bytes:
    if fmt == "HEIF":
        pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def client(session, users):
    with TestClient(create_app()) as c:
        login(c, "Alex")
        yield c


def test_full_serves_browser_formats_directly(client, session, users):
    data = image_bytes("JPEG")
    f = make_file(session, "Alex/a.jpg", users["Alex"], data)
    session.commit()
    r = client.get(f"/api/files/{f.id}/full")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == data  # untouched original bytes
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert not os.path.exists(full_path(f.id))  # nothing cached for native formats


def test_full_converts_heic_once_and_caches(client, session, users):
    f = make_file(session, "Alex/b.HEIC", users["Alex"], image_bytes("HEIF", (120, 80)))
    session.commit()
    r = client.get(f"/api/files/{f.id}/full")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (120, 80)  # full resolution, not a thumbnail
    assert os.path.isfile(full_path(f.id))
    clear_derived(f.id)  # what processing does when the source file changes
    assert not os.path.exists(full_path(f.id))


def test_original_is_always_a_download(client, session, users):
    f = make_file(session, "Alex/c.jpg", users["Alex"], image_bytes("JPEG"))
    session.commit()
    r = client.get(f"/api/files/{f.id}/original")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment") and "c.jpg" in r.headers["content-disposition"]


def test_full_refuses_purged_missing_and_videos(client, session, users):
    gone = make_file(session, "Alex/d.jpg", users["Alex"], image_bytes("JPEG"), status="purged")
    missing = make_file(session, "Alex/e.jpg", users["Alex"], image_bytes("JPEG"))
    vid = make_file(session, "Alex/f.mp4", users["Alex"], b"xx", kind="video")
    session.commit()
    os.remove(os.path.join(os.environ["LIBRARY_ROOT"], "Alex", "e.jpg"))
    assert client.get(f"/api/files/{gone.id}/full").status_code == 410
    assert client.get(f"/api/files/{missing.id}/full").status_code == 404
    assert client.get(f"/api/files/{vid.id}/full").status_code == 400


def test_media_requires_sign_in(session, users):
    f = make_file(session, "Alex/g.jpg", users["Alex"], image_bytes("JPEG"))
    session.commit()
    with TestClient(create_app()) as anon:
        assert anon.get(f"/api/files/{f.id}/full").status_code == 401
        assert anon.get(f"/api/files/{f.id}/original").status_code == 401
