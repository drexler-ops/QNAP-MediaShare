import io
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.config import config
from app.main import create_app
from app.models import AlbumItem, File, Job

from .test_api import login
from .test_workflow import make_file


def jpeg_bytes(color=(10, 200, 30), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def client(session, users):
    with TestClient(create_app()) as c:
        login(c, "Alex")
        yield c


def upload(client, name: str, data: bytes, chunk: int = 7, last_modified=None, album_id=None):
    r = client.post("/api/uploads", json={"filename": name, "size": len(data), "last_modified": last_modified,
                                          "album_id": album_id})
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    off = 0
    while off < len(data):
        piece = data[off:off + chunk]
        r = client.put(f"/api/uploads/{uid}?offset={off}", content=piece)
        assert r.status_code == 200, r.text
        off = r.json()["received"]
    return uid, client.post(f"/api/uploads/{uid}/complete")


def test_upload_is_filed_by_month_and_queued(client, session, users):
    data = jpeg_bytes()
    ts = int(datetime(2024, 7, 4, 12, 0).timestamp() * 1000)
    _, r = upload(client, "IMG_0042.JPG", data, chunk=1000, last_modified=ts)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "saved"
    assert body["path"] == "Alex/Uploads/2024-07/IMG_0042.jpg"  # month from the file date (no EXIF here)
    full = os.path.join(config.library_root, *body["path"].split("/"))
    assert open(full, "rb").read() == data
    assert abs(os.path.getmtime(full) - ts / 1000) < 2  # device timestamp preserved
    session.expire_all()
    f = session.get(File, body["file_id"])
    assert f.owner_id == users["Alex"].id and f.process_state == "pending" and f.sha256
    job = session.scalar(select(Job).where(Job.type == "process").order_by(Job.id.desc()))
    assert job.payload["ids"] == [f.id] and job.priority > 0  # processed right away, ahead of the queue
    # nothing left behind in the staging area
    assert [n for n in os.listdir(os.path.join(config.library_root, ".uploads-tmp"))] == []


def test_exact_duplicate_is_skipped(client, session, users):
    data = jpeg_bytes((1, 2, 3))
    existing = make_file(session, "Sam/2023/beach.jpg", users["Sam"], data)  # same bytes, not hashed yet
    session.commit()
    _, r = upload(client, "beach copy.jpg", data)
    body = r.json()
    assert body["status"] == "duplicate" and body["existing_path"] == "Sam/2023/beach.jpg"
    assert body["file_id"] == existing.id
    assert not os.path.exists(os.path.join(config.library_root, "Alex", "Uploads"))
    assert os.listdir(os.path.join(config.library_root, ".uploads-tmp")) == []


def test_name_clash_gets_a_suffix_and_album_add(client, session, users):
    a = client.post("/api/albums", json={"title": "Trip"}).json()
    _, r1 = upload(client, "pic.jpg", jpeg_bytes((5, 5, 5)), album_id=a["id"])
    _, r2 = upload(client, "pic.jpg", jpeg_bytes((9, 9, 9)), album_id=a["id"])
    p1, p2 = r1.json()["path"], r2.json()["path"]
    assert p2 == p1.replace("pic.jpg", "pic (1).jpg")
    assert r2.json()["album"] == "Trip"
    session.expire_all()
    assert len(session.scalars(select(AlbumItem).where(AlbumItem.album_id == a["id"])).all()) == 2


def test_resume_after_interruption(client):
    data = jpeg_bytes((50, 60, 70))
    r = client.post("/api/uploads", json={"filename": "v.jpg", "size": len(data)})
    uid = r.json()["id"]
    client.put(f"/api/uploads/{uid}?offset=0", content=data[:100])
    # a retried/duplicated chunk at the wrong offset is refused, not appended twice
    assert client.put(f"/api/uploads/{uid}?offset=0", content=data[:100]).status_code == 409
    assert client.get(f"/api/uploads/{uid}").json()["received"] == 100
    assert client.post(f"/api/uploads/{uid}/complete").status_code == 409  # incomplete
    client.put(f"/api/uploads/{uid}?offset=100", content=data[100:])
    assert client.post(f"/api/uploads/{uid}/complete").json()["status"] == "saved"


def test_validation_and_isolation(client, session, users):
    assert client.post("/api/uploads", json={"filename": "notes.txt", "size": 10}).status_code == 400
    assert client.post("/api/uploads", json={"filename": "huge.mov", "size": 10**15}).status_code == 400
    r = client.post("/api/uploads", json={"filename": "../../etc/passwd.jpg", "size": 5})
    assert r.status_code == 200 and r.json()["filename"] == "passwd.jpg"  # path parts stripped
    uid = r.json()["id"]
    assert client.put(f"/api/uploads/{uid}?offset=0", content=b"123456789").status_code == 413  # beyond declared size
    other = TestClient(client.app)
    login(other, "Sam")
    assert other.get(f"/api/uploads/{uid}").status_code == 404  # someone else's upload
    assert other.put(f"/api/uploads/{uid}?offset=0", content=b"x").status_code == 404
    assert client.get("/api/uploads/not-a-real-id").status_code == 404


def test_users_without_a_folder_cannot_upload(session, users):
    with TestClient(create_app()) as c:
        login(c, "admin")
        r = c.post("/api/uploads", json={"filename": "a.jpg", "size": 10})
        assert r.status_code == 400 and "folder" in r.json()["detail"]
        assert c.get("/api/uploads/config").json()["folder"] is None
