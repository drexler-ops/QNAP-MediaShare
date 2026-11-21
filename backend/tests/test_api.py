import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import File, User

from .test_workflow import make_file, make_group


@pytest.fixture
def client(session, users):
    with TestClient(create_app()) as c:
        yield c


def login(c, name, password=None):
    c.post("/api/auth/logout")
    password = password or ("adminpass123" if name.lower() == "admin" else "secret123")
    r = c.post("/api/auth/login", json={"username": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_requires_login(client):
    assert client.get("/api/groups/summary").status_code == 401
    assert client.post("/api/auth/login", json={"username": "Sam", "password": "nope"}).status_code == 401


def test_review_approve_quarantine_flow(client, session, users):
    alex, sam = users["Alex"], users["Sam"]
    a = make_file(session, "Alex/IMG_1.HEIC", alex, b"same", has_exif=True, exif_score=9)
    b = make_file(session, "Sam/IMG_1.jpg", sam, b"same")
    g = make_group(session, [a, b])
    session.commit()

    login(client, "alex")  # case-insensitive names
    detail = client.get(f"/api/groups/{g.id}").json()
    assert [m["suggested_keep"] for m in detail["members"]] == [True, False]
    assert detail["members"][1]["owner"] == "Sam"

    r = client.post(f"/api/groups/{g.id}/decisions",
                    json={"decisions": [{"item_id": a.item_id, "action": "keep"},
                                        {"item_id": b.item_id, "action": "delete"}]})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"

    # Last-copy protection is enforced server-side.
    r = client.post(f"/api/groups/{g.id}/decisions", json={"decisions": [{"item_id": a.item_id, "action": "delete"}]})
    assert r.status_code == 400

    pend = client.get("/api/pending").json()
    assert pend["to_approve"] == [] and len(pend["waiting_on_others"]) == 1
    did = pend["waiting_on_others"][0]["decision_id"]
    assert client.post("/api/decisions/approve", json={"ids": [did]}).status_code == 403

    login(client, "Sam")
    assert client.get("/api/pending/count").json()["count"] == 1
    row = client.get("/api/pending").json()["to_approve"][0]
    assert row["kept"]["primary"]["path"] == "Alex/IMG_1.HEIC"
    assert client.post("/api/decisions/approve", json={"ids": [did]}).json()["done"] == [did]

    q = client.get("/api/quarantine").json()
    assert len(q["items"]) == 1 and q["items"][0]["approved_by"] == "Sam"
    assert client.post("/api/quarantine/restore", json={"ids": [b.item_id]}).json()["done"] == [b.item_id]

    audit = client.get("/api/audit").json()
    assert {"mark_delete", "approve_quarantine", "restore"} <= set(audit["actions"])


def test_bulk_exact(client, session, users):
    alex, sam = users["Alex"], users["Sam"]
    groups = []
    for i in range(3):
        a = make_file(session, f"Alex/{i}.jpg", alex, f"d{i}".encode())
        b = make_file(session, f"Sam/{i}.jpg", sam, f"d{i}".encode())
        groups.append(make_group(session, [a, b]))
    session.commit()
    login(client, "Alex")
    prev = client.get("/api/groups/bulk-exact").json()
    assert prev["groups"] == 3 and prev["files"] == 3 and prev["by_owner"] == {"Sam": 3}
    res = client.post("/api/groups/bulk-exact").json()
    assert res == {"groups": 3, "files": 3, "skipped": 0}
    session.expire_all()
    statuses = {f.path: f.status for f in session.scalars(select(File))}
    assert statuses["Sam/0.jpg"] == "pending_delete" and statuses["Alex/0.jpg"] == "active"
    assert client.get("/api/groups/bulk-exact").json()["groups"] == 0


def test_users_and_folder_reassignment(client, session, users):
    loose = make_file(session, "Riley/pic.jpg", None, b"z")
    session.commit()
    login(client, "admin")
    r = client.post("/api/users", json={"name": "Riley", "home_folder": "Riley"})
    assert r.status_code == 200
    session.expire_all()
    owner = session.scalar(select(User).where(User.name == "Riley"))
    assert session.get(File, loose.id).owner_id == owner.id


def test_admin_seeded_once(session):
    from app.db import seed_admin

    admins = session.scalars(select(User).where(User.is_admin.is_(True))).all()
    assert [a.name for a in admins] == ["admin"]
    assert seed_admin(session) is None  # already have an admin: nothing new


def test_only_admin_manages_users(client, users):
    login(client, "Sam")
    assert client.post("/api/users", json={"name": "Eve"}).status_code == 403
    assert client.patch(f"/api/users/{users['Alex'].id}", json={"home_folder": "X"}).status_code == 403
    assert client.post(f"/api/users/{users['Alex'].id}/reset-password").status_code == 403
    assert client.get("/api/users").status_code == 200  # anyone can see who's who
    assert client.get("/api/auth/me").json()["is_admin"] is False
    assert login(client, "admin")["is_admin"] is True


def test_new_user_sets_password_on_first_login(client):
    login(client, "admin")
    r = client.post("/api/users", json={"name": "Riley", "home_folder": "Riley"})
    assert r.json()["must_set_password"] is True
    client.post("/api/auth/logout")

    first = client.post("/api/auth/login", json={"username": "riley"})
    assert first.status_code == 200 and first.json() == {"needs_password_setup": True, "name": "Riley"}
    assert client.get("/api/auth/me").status_code == 401  # not signed in until a password is chosen
    assert client.post("/api/auth/setup-password", json={"new_password": "123"}).status_code == 400
    me = client.post("/api/auth/setup-password", json={"new_password": "riley-pw"}).json()
    assert me["name"] == "Riley"
    assert client.get("/api/auth/me").status_code == 200

    login(client, "Riley", "riley-pw")
    # Setup can't be replayed once the password is set.
    client.post("/api/auth/logout")
    assert client.post("/api/auth/setup-password", json={"new_password": "hijack!!"}).status_code == 400


def test_admin_reset_retriggers_prompt_and_signs_user_out(client, users):
    sam = TestClient(client.app)
    login(sam, "Sam")
    login(client, "admin")
    assert client.post(f"/api/users/{users['Sam'].id}/reset-password").status_code == 200
    assert sam.get("/api/groups/summary").status_code == 401  # existing session is cut off
    # Old password no longer works; she's prompted to pick a new one instead.
    r = sam.post("/api/auth/login", json={"username": "Sam", "password": "secret123"})
    assert r.json()["needs_password_setup"] is True
    sam.post("/api/auth/setup-password", json={"new_password": "brand-new"})
    login(sam, "Sam", "brand-new")


def test_deactivate_and_last_admin_guard(client, users):
    login(client, "admin")
    admin_id = client.get("/api/auth/me").json()["id"]
    assert client.patch(f"/api/users/{admin_id}", json={"is_admin": False}).status_code == 400
    assert client.patch(f"/api/users/{admin_id}", json={"is_active": False}).status_code == 400
    assert client.patch(f"/api/users/{users['Alex'].id}", json={"is_admin": True}).status_code == 200
    assert client.patch(f"/api/users/{admin_id}", json={"is_admin": False}).status_code == 200

    assert client.patch(f"/api/users/{users['Sam'].id}", json={"is_active": False}).status_code == 403  # no longer admin
    login(client, "Alex")
    assert client.patch(f"/api/users/{users['Sam'].id}", json={"is_active": False}).status_code == 200
    assert TestClient(client.app).post("/api/auth/login", json={"username": "Sam", "password": "secret123"}).status_code == 401
    assert client.patch(f"/api/users/{users['Alex'].id}", json={"home_folder": "Sam"}).status_code == 400  # taken


def test_settings_roundtrip(client):
    login(client, "Sam")
    s = client.put("/api/admin/settings", json={"purge_days": 14, "reencode_threshold": 5}).json()
    assert s["purge_days"] == 14 and s["reencode_threshold"] == 5
    assert client.put("/api/admin/settings", json={"purge_days": "x"}).status_code == 400
    assert client.put("/api/admin/settings", json={"nope": 1}).status_code == 400
