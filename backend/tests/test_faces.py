"""Face pipeline + grouping + API, with a fake engine (real models are too heavy for unit tests)."""
import os

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.faces import grouping
from app.faces.engine import ARCFACE_DST, DetectedFace, align_face, nms, similarity_transform
from app.faces.pipeline import process_faces
from app.main import create_app
from app.models import Face, Job, Person
from app.scanner.thumbs import face_thumb_path, thumb_path

from .test_api import login
from .test_workflow import make_file

rng = np.random.default_rng(7)


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


IDENTITIES = {k: _unit(rng.normal(size=512).astype(np.float32)) for k in "ABC"}


def near(identity: str, noise: float = 0.6) -> np.ndarray:
    """Another 'photo' of the same person: same-person similarity ~0.7, different people ~0 (like ArcFace)."""
    return _unit(IDENTITIES[identity] + noise * _unit(rng.normal(size=512).astype(np.float32)))


class FakeEngine:
    """Detects the faces we tell it about (per file id) and returns identity-like vectors."""

    def __init__(self, plan):
        self.plan = plan  # file_id -> list of (identity, box)
        self.current = None

    def detect(self, img):
        return [
            DetectedFace(box=np.array(box, dtype=np.float32), score=0.9, kps=np.zeros((5, 2), dtype=np.float32) + i)
            for i, (_, box) in enumerate(self.plan[self.current])
        ]

    def embed(self, img, kps):
        return near(self.plan[self.current][int(kps[0, 0])][0])


def with_preview(session, users, rel, owner="Alex"):
    f = make_file(session, rel, users[owner], b"x")
    os.makedirs(os.path.dirname(thumb_path(f.id, "l")), exist_ok=True)
    Image.new("RGB", (400, 300), (90, 90, 90)).save(thumb_path(f.id, "l"), "WEBP")
    f.thumb_ready, f.face_state = True, "pending"
    return f


def run(session, files, plan):
    eng = FakeEngine(plan)
    for f in files:  # one at a time so the fake knows which plan applies
        eng.current = f.id
        process_faces(session, [f.id], eng=eng)


def test_maths_helpers():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    assert nms(boxes, np.array([0.9, 0.8, 0.7]), 0.4) == [0, 2]
    src = ARCFACE_DST * 2 + np.array([30, 40], dtype=np.float32)  # pure scale + shift
    m = similarity_transform(src.astype(np.float64), ARCFACE_DST.astype(np.float64))
    mapped = np.hstack([src, np.ones((5, 1))]) @ m.T
    assert np.allclose(mapped, ARCFACE_DST, atol=1e-3)
    assert align_face(Image.new("RGB", (300, 300)), src).size == (112, 112)


def test_cluster_groups_identities():
    vecs = np.stack([near("A") for _ in range(4)] + [near("B") for _ in range(3)] + [near("C")])
    groups = grouping.cluster(vecs, 0.5, 3)
    assert sorted(sorted(g) for g in groups) == [[0, 1, 2, 3], [4, 5, 6]]  # a lone face is not a group


def test_pipeline_and_grouping_end_to_end(session, users):
    files = [with_preview(session, users, f"Alex/p{i}.jpg") for i in range(6)]
    session.commit()
    plan = {f.id: [("A", (10, 10, 90, 90))] for f in files[:4]}
    plan.update({f.id: [("B", (200, 50, 280, 130)), ("A", (20, 20, 100, 100))] for f in files[4:]})
    run(session, files, plan)
    faces = session.scalars(select(Face)).all()
    assert len(faces) == 8 and all(os.path.isfile(face_thumb_path(f.id)) for f in faces)
    assert all(0 <= v <= 1 for f in faces for v in f.bbox)  # stored as fractions of the image

    stats = grouping.run_face_grouping(session)
    assert stats["clusters"] == 1  # 6 A faces form a group; only 2 B faces (< 3)
    person = session.scalar(select(Person))
    assert person.name is None and person.cover_face_id is not None


def test_naming_recognition_review_and_not_this_person(session, users):
    files = [with_preview(session, users, f"Alex/q{i}.jpg") for i in range(5)]
    session.commit()
    run(session, files, {f.id: [("A", (10, 10, 90, 90))] for f in files})
    grouping.run_face_grouping(session)
    cluster_id = session.scalar(select(Person.id))

    with TestClient(create_app()) as c:
        login(c, "Alex")
        assert c.patch(f"/api/people/{cluster_id}", json={"name": "Sue"}).json() == {"id": cluster_id, "merged": False}
        session.expire_all()
        assert all(f.assignment == "manual" for f in session.scalars(select(Face)))  # naming confirms the group

        new = with_preview(session, users, "Alex/new.jpg")  # a new photo of the same person
        session.commit()
        run(session, [new], {new.id: [("A", (10, 10, 90, 90))]})
        grouping.run_face_grouping(session)
        session.expire_all()
        face = session.scalar(select(Face).where(Face.file_id == new.id))
        assert face.person_id == cluster_id and face.assignment == "auto"

        c.post("/api/faces/unassign", json={"face_ids": [face.id]})  # "Not Sue" is remembered
        grouping.run_face_grouping(session)
        session.expire_all()
        face = session.get(Face, face.id)
        assert face.person_id != cluster_id and cluster_id in face.rejected

        assert c.get(f"/api/items?people={cluster_id}").json()["total"] == 5
        vf = c.get(f"/api/items/{files[0].item_id}/faces").json()
        assert vf[0]["name"] == "Sue" and len(vf[0]["bbox"]) == 4
        people = c.get("/api/people").json()
        assert [p["name"] for p in people["named"]] == ["Sue"] and people["named"][0]["photos"] == 5


def test_tag_new_name_merge_and_ignore(session, users):
    a = with_preview(session, users, "Alex/a.jpg")
    b = with_preview(session, users, "Alex/b.jpg")
    session.commit()
    run(session, [a, b], {a.id: [("B", (10, 10, 90, 90))], b.id: [("C", (10, 10, 90, 90)), ("B", (200, 10, 280, 90))]})
    fa, fc, fb = session.scalars(select(Face).order_by(Face.id)).all()
    with TestClient(create_app()) as c:
        login(c, "Sam")
        p1 = c.post("/api/faces/assign", json={"face_ids": [fa.id], "name": "Grandpa"}).json()["person_id"]
        p2 = c.post("/api/faces/assign", json={"face_ids": [fb.id], "name": "Gramps"}).json()["person_id"]
        # an existing name (any case) is reused, not duplicated
        assert c.post("/api/faces/assign", json={"face_ids": [fb.id], "name": "grandpa"}).json()["person_id"] == p1
        # renaming a person to an existing name merges them
        assert c.patch(f"/api/people/{p2}", json={"name": "Grandpa"}).json() == {"id": p1, "merged": True}
        session.expire_all()
        assert session.get(Person, p2) is None
        assert c.post("/api/faces/ignore", json={"face_ids": [fc.id]}).json()["deleted"] == 1  # "not a face"
        assert not os.path.exists(face_thumb_path(fc.id))
        actions = {r["action"] for r in c.get("/api/audit").json()["rows"]}
        assert {"person_create", "faces_assign", "person_merge", "faces_ignore"} <= actions


def test_reprocessing_keeps_names(session, users):
    f = with_preview(session, users, "Alex/r.jpg")
    session.commit()
    run(session, [f], {f.id: [("A", (10, 10, 90, 90))]})
    face = session.scalar(select(Face))
    p = Person(name="Riley")
    session.add(p)
    session.flush()
    face.person_id, face.assignment = p.id, "manual"
    session.commit()
    run(session, [f], {f.id: [("A", (12, 11, 91, 92))]})  # re-detected at almost the same spot
    session.expire_all()
    again = session.scalar(select(Face))
    assert again.person_id == p.id and again.assignment == "manual"  # the name carried over
    assert session.scalar(select(func.count(Face.id))) == 1  # old detection replaced, not duplicated


def test_enabling_faces_queues_a_scan(session, users):
    with TestClient(create_app()) as c:
        login(c, "Alex")
        c.put("/api/admin/settings", json={"faces_enabled": True})
        assert session.scalar(select(Job).where(Job.type == "faces_scan")) is not None


def test_regroup_keeps_unnamed_group_ids(session, users):
    files = [with_preview(session, users, f"Alex/s{i}.jpg") for i in range(8)]
    session.commit()
    run(session, files, {f.id: [("A", (10, 10, 90, 90)), ("B", (200, 50, 280, 130))] for f in files})
    grouping.run_face_grouping(session)
    ids = sorted(session.scalars(select(Person.id)))
    assert len(ids) == 2
    grouping.run_face_grouping(session)  # nothing changed: same groups, same ids
    assert sorted(session.scalars(select(Person.id))) == ids

    with TestClient(create_app()) as c:
        login(c, "Alex")
        assert c.patch(f"/api/people/{ids[0]}", json={"name": "Sue"}).status_code == 200
    grouping.run_face_grouping(session)  # naming one group must not renumber the other
    session.expire_all()
    assert sorted(session.scalars(select(Person.id))) == ids
    assert session.get(Person, ids[0]).name == "Sue"


def test_edit_during_regroup_is_never_overwritten(session, users, monkeypatch):
    files = [with_preview(session, users, f"Alex/r{i}.jpg") for i in range(5)]
    session.commit()
    run(session, files, {f.id: [("A", (10, 10, 90, 90))] for f in files})
    grouping.run_face_grouping(session)
    pid = session.scalar(select(Person.id))

    calls = {"n": 0}

    def edit_while_computing(s):
        # A user names the group after the regroup read the data but before it writes.
        calls["n"] += 1
        if calls["n"] == 1:
            s.execute(Person.__table__.update().where(Person.id == pid).values(name="Sue"))
            s.execute(Face.__table__.update().where(Face.person_id == pid).values(assignment="manual"))
            s.commit()

    monkeypatch.setattr(grouping, "faces_lock", edit_while_computing)
    stats = grouping.run_face_grouping(session)
    assert calls["n"] == 2 and "faces" in stats  # first attempt saw the change and recomputed
    session.expire_all()
    p = session.get(Person, pid)
    assert p.name == "Sue"
    assert all(f.assignment == "manual" and f.person_id == pid for f in session.scalars(select(Face)))


def test_person_page_and_people_list_are_paged(session, users):
    files = [with_preview(session, users, f"Alex/pg{i}.jpg") for i in range(5)]
    session.commit()
    run(session, files, {f.id: [("A", (10, 10, 90, 90))] for f in files})
    grouping.run_face_grouping(session)
    pid = session.scalar(select(Person.id))
    with TestClient(create_app()) as c:
        login(c, "Alex")
        d = c.get(f"/api/people/{pid}?page_size=2&faces_page_size=3").json()
        assert (len(d["items"]), d["items_total"], len(d["face_list"]), d["faces_total"]) == (2, 5, 3, 5)
        last = c.get(f"/api/people/{pid}?page=3&page_size=2&faces_page=2&faces_page_size=3").json()
        assert (len(last["items"]), len(last["face_list"])) == (1, 2)
        seen = {i["id"] for n in (1, 2, 3) for i in c.get(f"/api/people/{pid}?page={n}&page_size=2").json()["items"]}
        assert len(seen) == 5  # pages don't overlap or skip
        lst = c.get("/api/people?unnamed_page_size=1").json()
        assert len(lst["unnamed"]) == 1 and lst["unnamed_total"] == 1 and lst["unnamed_page"] == 1
