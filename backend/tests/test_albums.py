import io
import zipfile
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.deletion import workflow
from app.main import create_app
from app.models import AlbumItem, ShareLink

from .test_api import login
from .test_workflow import make_file, make_group


def jpeg(color=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def photos(session, users):
    alex, sam = users["Alex"], users["Sam"]
    a = make_file(session, "Alex/a.jpg", alex, jpeg((1, 2, 3)))
    b = make_file(session, "Alex/b.jpg", alex, jpeg((4, 5, 6)), gps_lat=41.9, gps_lon=-87.6)
    c = make_file(session, "Sam/c.jpg", sam, jpeg((7, 8, 9)))
    session.commit()
    return a, b, c


@pytest.fixture
def client(session, users):
    with TestClient(create_app()) as c:
        yield c


def new_album(client, title, item_ids):
    r = client.post("/api/albums", json={"title": title, "item_ids": item_ids})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_add_remove_reorder_cover(client, photos):
    a, b, c = photos
    login(client, "Alex")
    album = new_album(client, "Vacation", [a.item_id, b.item_id])
    assert album["role"] == "owner" and album["count"] == 2
    assert [i["id"] for i in album["items"]] == [a.item_id, b.item_id]
    assert album["cover_file_id"] == a.id  # first photo becomes the cover

    r = client.post(f"/api/albums/{album['id']}/items", json={"item_ids": [c.item_id, a.item_id, 99999]})
    assert r.json() == {"added": 1, "count": 3}  # duplicates and unknown items are ignored
    client.put(f"/api/albums/{album['id']}/order", json={"item_ids": [c.item_id, a.item_id]})
    got = client.get(f"/api/albums/{album['id']}").json()
    assert [i["id"] for i in got["items"]] == [c.item_id, a.item_id, b.item_id]

    client.patch(f"/api/albums/{album['id']}", json={"cover_item_id": b.item_id})
    client.post(f"/api/albums/{album['id']}/items/remove", json={"item_ids": [b.item_id]})
    got = client.get(f"/api/albums/{album['id']}").json()
    assert got["count"] == 2 and got["cover_item_id"] == c.item_id  # cover moved off the removed photo


def test_private_shared_and_collaborative(client, photos, users):
    a, b, c = photos
    login(client, "Alex")
    album = new_album(client, "Private", [a.item_id])
    aid = album["id"]

    login(client, "Sam")
    assert client.get(f"/api/albums/{aid}").status_code == 404  # private: invisible, not just forbidden
    assert client.get("/api/albums").json()["albums"] == []

    login(client, "Alex")
    client.put(f"/api/albums/{aid}/members", json={"members": [{"user_id": users["Sam"].id, "role": "viewer"}]})
    login(client, "Sam")
    assert client.get(f"/api/albums/{aid}").json()["role"] == "viewer"
    assert client.post(f"/api/albums/{aid}/items", json={"item_ids": [c.item_id]}).status_code == 403
    assert client.get("/api/albums/editable").json()["albums"] == []

    login(client, "Alex")
    client.put(f"/api/albums/{aid}/members", json={"members": [{"user_id": users["Sam"].id, "role": "editor"}]})
    login(client, "Sam")
    assert client.post(f"/api/albums/{aid}/items", json={"item_ids": [c.item_id]}).json()["added"] == 1
    assert client.patch(f"/api/albums/{aid}", json={"title": "Mine now"}).status_code == 403  # only owner renames
    assert client.post(f"/api/albums/{aid}/links", json={}).status_code == 403  # only owner shares publicly
    assert client.delete(f"/api/albums/{aid}").status_code == 403
    assert client.get(f"/api/albums/{aid}").json()["links"] == []


def test_deleting_a_duplicate_swaps_album_to_kept_copy(session, users, photos):
    alex, sam = users["Alex"], users["Sam"]
    keep = make_file(session, "Alex/orig.HEIC", alex, b"orig")
    dup = make_file(session, "Sam/copy.jpg", sam, b"copy")
    g = make_group(session, [keep, dup], keep_index=0)
    session.commit()
    from app.models import Album

    album = Album(title="Trip", owner_id=sam.id, cover_item_id=dup.item_id)
    album.items = [AlbumItem(item_id=dup.item_id, position=1.0)]
    session.add(album)
    session.commit()

    (d,) = workflow.mark(session, alex, g.id, [(dup.item_id, "delete")])
    workflow.approve(session, sam, [d.id])
    session.commit()
    session.refresh(album)
    assert [ai.item_id for ai in album.items] == [keep.item_id]
    assert album.cover_item_id == keep.item_id


def test_share_link_public_view_hides_private_details(client, photos):
    a, b, c = photos
    login(client, "Alex")
    album = new_album(client, "Shared trip", [a.item_id, b.item_id])
    link = client.post(f"/api/albums/{album['id']}/links", json={"allow_download": False}).json()
    assert link["state"] == "active" and link["url"] == f"/s/{link['token']}"

    anon = TestClient(client.app)
    data = anon.get(f"/api/public/s/{link['token']}").json()
    assert data["title"] == "Shared trip" and [i["file_id"] for i in data["items"]] == [a.id, b.id]
    blob = str(data)
    for secret in ("Alex", "41.9", "gps", "folder", "path", "owner"):
        assert secret not in blob  # no owner names, folders or locations leak

    assert anon.get(f"/api/public/s/{link['token']}/files/{a.id}/full").status_code == 200
    # Files outside the album are not reachable through the link
    assert anon.get(f"/api/public/s/{link['token']}/files/{c.id}/full").status_code == 404
    # Downloads are refused server-side when the link doesn't allow them
    assert anon.get(f"/api/public/s/{link['token']}/files/{a.id}/download").status_code == 403
    assert anon.get(f"/api/public/s/{link['token']}/download.zip").status_code == 403
    # The rest of the app stays closed to anonymous visitors
    assert anon.get("/api/albums").status_code == 401
    assert anon.get(f"/api/files/{a.id}/full").status_code == 401


def test_share_link_downloads_zip(client, photos):
    a, b, _ = photos
    login(client, "Alex")
    album = new_album(client, "Zip me", [a.item_id, b.item_id])
    token = client.post(f"/api/albums/{album['id']}/links", json={"allow_download": True}).json()["token"]
    anon = TestClient(client.app)
    assert anon.get(f"/api/public/s/{token}/files/{a.id}/download").headers["content-disposition"].startswith("attachment")
    r = anon.get(f"/api/public/s/{token}/download.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert names == ["a.jpg", "b.jpg"]


def test_password_expiry_revoke_and_probing(client, session, photos):
    a, _, _ = photos
    login(client, "Alex")
    album = new_album(client, "Secret", [a.item_id])
    link = client.post(f"/api/albums/{album['id']}/links", json={"password": "hunter2"}).json()
    t = link["token"]
    anon = TestClient(client.app)

    r = anon.get(f"/api/public/s/{t}")
    assert r.status_code == 401 and "Secret" not in r.text  # nothing revealed before unlocking
    assert anon.get(f"/api/public/s/{t}/files/{a.id}/thumb").status_code == 401
    assert anon.post(f"/api/public/s/{t}/unlock", json={"password": "nope"}).status_code == 401
    assert anon.post(f"/api/public/s/{t}/unlock", json={"password": "hunter2"}).status_code == 200
    assert anon.get(f"/api/public/s/{t}").json()["title"] == "Secret"

    # Revoked, expired and made-up tokens are indistinguishable
    client.post(f"/api/albums/{album['id']}/links/{link['id']}/revoke")
    expired = client.post(f"/api/albums/{album['id']}/links", json={}).json()
    row = session.scalar(select(ShareLink).where(ShareLink.id == expired["id"]))
    row.expires_at = datetime.now() - timedelta(minutes=1)
    session.commit()
    bodies = {anon.get(f"/api/public/s/{tok}").text for tok in (t, expired["token"], "made-up-token")}
    assert len(bodies) == 1 and anon.get("/api/public/s/made-up-token").status_code == 404


def test_password_attempts_are_rate_limited(client, photos):
    a, _, _ = photos
    login(client, "Alex")
    album = new_album(client, "Locked", [a.item_id])
    t = client.post(f"/api/albums/{album['id']}/links", json={"password": "letmein"}).json()["token"]
    anon = TestClient(client.app)
    codes = [anon.post(f"/api/public/s/{t}/unlock", json={"password": f"guess{i}"}).status_code for i in range(10)]
    assert codes[:8] == [401] * 8 and codes[-1] == 429


def test_share_only_app_exposes_nothing_else(session, users, photos):
    a, _, _ = photos
    with TestClient(create_app()) as full:
        login(full, "Alex")
        album = new_album(full, "Public", [a.item_id])
        token = full.post(f"/api/albums/{album['id']}/links", json={}).json()["token"]
    with TestClient(create_app(share_only=True)) as pub:
        assert pub.get(f"/api/public/s/{token}").status_code == 200
        assert pub.get(f"/api/public/s/{token}/files/{a.id}/full").status_code == 200
        for path in ("/api/auth/login", "/api/albums", "/api/files/1/full", "/api/users", "/api/docs", "/", "/browse"):
            assert pub.get(path).status_code == 404, path
        assert pub.post("/api/auth/login", json={"username": "admin", "password": "x"}).status_code in (404, 405)
        r = pub.get(f"/api/public/s/{token}")
        assert r.headers["referrer-policy"] == "no-referrer" and r.headers["x-frame-options"] == "DENY"
        assert pub.get("/robots.txt").text.startswith("User-agent: *")
