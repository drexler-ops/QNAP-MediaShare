import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.deletion import workflow
from app.deletion.workflow import WorkflowError
from app.models import AuditLog, DupGroup, DupGroupMember, File, Item
from app.paths import abs_path


def make_file(session, rel, owner, data=b"x", kind="image", role="primary", item=None, **kw):
    full = abs_path(rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    if item is None:
        item = Item()
        session.add(item)
        session.flush()
    f = File(path=rel, owner_id=owner.id if owner else None, item_id=item.id, role=role, size=len(data),
             mtime=0, ext=rel.rsplit(".", 1)[-1].lower(), kind=kind, process_state="done", **kw)
    session.add(f)
    session.flush()
    return f


def make_group(session, files, keep_index=0, type_="exact"):
    g = DupGroup(type=type_, confidence=1.0, status="open")
    g.members = [DupGroupMember(item_id=f.item_id, suggested_keep=i == keep_index) for i, f in enumerate(files)]
    session.add(g)
    session.flush()
    return g


@pytest.fixture
def setup(session, users):
    alex, sam = users["Alex"], users["Sam"]
    a = make_file(session, "Alex/IMG_1.HEIC", alex, b"original")
    b = make_file(session, "Sam/IMG_1.HEIC", sam, b"original")
    g = make_group(session, [a, b])
    session.commit()
    return session, alex, sam, a, b, g


def test_cannot_delete_every_copy(setup):
    session, alex, sam, a, b, g = setup
    with pytest.raises(WorkflowError, match="keep at least one"):
        workflow.mark(session, alex, g.id, [(a.item_id, "delete"), (b.item_id, "delete")])


def test_only_owner_can_approve_then_quarantine_restore_purge(setup):
    session, alex, sam, a, b, g = setup
    # Alex marks Sam's copy for deletion.
    (d,) = [x for x in workflow.mark(session, alex, g.id, [(a.item_id, "keep"), (b.item_id, "delete")])
            if x.action == "delete"]
    assert d.state == "marked"
    assert session.get(File, b.id).status == "pending_delete"
    assert session.get(DupGroup, g.id).status == "resolved"

    # Alex can't approve deletion of Sam's file.
    with pytest.raises(WorkflowError) as exc:
        workflow.approve(session, alex, [d.id])
    assert exc.value.status == 403

    # Sam approves -> file moves into quarantine.
    res = workflow.approve(session, sam, [d.id])
    assert res["done"] == [d.id]
    f = session.get(File, b.id)
    assert f.status == "quarantined"
    assert f.original_path == "Sam/IMG_1.HEIC"
    assert f.path.startswith(".quarantine/") and f.path.endswith("Sam/IMG_1.HEIC")
    assert not os.path.exists(abs_path("Sam/IMG_1.HEIC"))
    assert os.path.exists(abs_path(f.path))

    # Restore puts it back exactly where it was.
    workflow.restore(session, alex, [b.item_id])
    f = session.get(File, b.id)
    assert f.status == "active" and f.path == "Sam/IMG_1.HEIC"
    assert os.path.exists(abs_path("Sam/IMG_1.HEIC"))
    assert session.get(DupGroup, g.id).status == "open"

    # Mark again, approve, then purge (purge_days=0 makes it immediately eligible).
    (d2,) = [x for x in workflow.mark(session, alex, g.id, [(b.item_id, "delete")])]
    workflow.approve(session, sam, [d2.id])
    qpath = session.get(File, b.id).path
    assert workflow.purge(session, alex, purge_days=30)["done"] == []  # not old enough
    workflow.purge(session, alex, purge_days=0)
    assert session.get(File, b.id).status == "purged"
    assert not os.path.exists(abs_path(qpath))
    assert os.path.exists(abs_path("Alex/IMG_1.HEIC"))  # the keeper is untouched

    actions = [a.action for a in session.scalars(select(AuditLog).order_by(AuditLog.id))]
    for expected in ("mark_delete", "approve_quarantine", "restore", "purge"):
        assert expected in actions


def test_owner_marking_own_file_still_needs_separate_approval(setup):
    session, alex, sam, a, b, g = setup
    (d,) = workflow.mark(session, sam, g.id, [(b.item_id, "delete")])
    assert d.state == "marked"
    assert os.path.exists(abs_path("Sam/IMG_1.HEIC"))
    workflow.approve(session, sam, [d.id])
    assert session.get(File, b.id).status == "quarantined"


def test_reject_and_cancel_return_file_to_active(setup):
    session, alex, sam, a, b, g = setup
    (d,) = workflow.mark(session, alex, g.id, [(b.item_id, "delete")])
    workflow.reject(session, sam, [d.id])
    assert session.get(File, b.id).status == "active"
    assert session.get(DupGroup, g.id).status == "open"

    (d2,) = workflow.mark(session, alex, g.id, [(b.item_id, "delete")])
    with pytest.raises(WorkflowError):
        workflow.cancel(session, sam.__class__(id=999, name="x", password_hash=""), [d2.id])
    workflow.cancel(session, alex, [d2.id])
    assert session.get(File, b.id).status == "active"


def test_changing_mind_before_approval(setup):
    session, alex, sam, a, b, g = setup
    workflow.mark(session, alex, g.id, [(b.item_id, "delete")])
    workflow.mark(session, alex, g.id, [(b.item_id, "keep")])
    assert session.get(File, b.id).status == "active"
    # Now it's fine to delete a instead (b is kept).
    workflow.mark(session, alex, g.id, [(a.item_id, "delete")])


def test_live_photo_quarantines_both_files(session, users):
    sam = users["Sam"]
    still = make_file(session, "Sam/IMG_5.HEIC", sam, b"still")
    make_file(session, "Sam/IMG_5.MOV", sam, b"motion", kind="video", role="live_video",
              item=session.get(Item, still.item_id))
    other = make_file(session, "Sam/copy/IMG_5.HEIC", sam, b"still")
    g = make_group(session, [other, still])
    (d,) = workflow.mark(session, sam, g.id, [(still.item_id, "delete")])
    workflow.approve(session, sam, [d.id])
    files = session.scalars(select(File).where(File.item_id == still.item_id)).all()
    assert {f.status for f in files} == {"quarantined"}
    assert not os.path.exists(abs_path("Sam/IMG_5.MOV"))


def test_delete_respects_survivors_across_groups(session, users):
    alex = users["Alex"]
    a = make_file(session, "Alex/a.jpg", alex, b"1")
    b = make_file(session, "Alex/b.jpg", alex, b"2")
    c = make_file(session, "Alex/c.jpg", alex, b"3")
    g1 = make_group(session, [a, b])
    g2 = make_group(session, [b, c], type_="burst")
    workflow.mark(session, alex, g2.id, [(c.item_id, "delete")])
    # Deleting b would leave burst group g2 with nothing.
    with pytest.raises(WorkflowError):
        workflow.mark(session, alex, g1.id, [(b.item_id, "delete")])


def test_unassigned_files_can_be_approved_by_anyone(session, users):
    alex = users["Alex"]
    a = make_file(session, "Alex/x.jpg", alex, b"1")
    loose = make_file(session, "Misc/x.jpg", None, b"1")
    g = make_group(session, [a, loose])
    (d,) = workflow.mark(session, alex, g.id, [(loose.item_id, "delete")])
    assert workflow.approve(session, alex, [d.id])["done"] == [d.id]


def test_quarantine_name_collisions(session, users):
    sam = users["Sam"]
    a = make_file(session, "Sam/keep.jpg", sam, b"k")
    b = make_file(session, "Sam/dup.jpg", sam, b"d")
    g = make_group(session, [a, b])
    (d,) = workflow.mark(session, sam, g.id, [(b.item_id, "delete")])
    when = datetime.now().replace(microsecond=0)
    clash = abs_path(f".quarantine/{when:%Y-%m-%d}/Sam/dup.jpg")
    os.makedirs(os.path.dirname(clash), exist_ok=True)
    open(clash, "wb").close()
    workflow.approve(session, sam, [d.id])
    assert session.get(File, b.id).path.endswith("dup__1.jpg")


def test_restarting_worker_requeues_only_its_own_jobs(session):
    from app.jobs import reset_stale
    from app.models import Job

    now = datetime.now()
    jobs = {w: Job(type="process", state="running", worker=w, started_at=now, payload={})
            for w in ("pc-1", "pc-12", "pc-office-1", "pc_2", "nas-0")}
    old = Job(type="process", state="running", worker="nas-1", started_at=now - timedelta(hours=7), payload={})
    session.add_all([*jobs.values(), old])
    session.commit()
    assert reset_stale(session, "pc") == 3  # pc-1, pc-12 and the 7-hour-old job
    for w, j in jobs.items():
        session.refresh(j)
        assert (j.state == "queued") == (w in ("pc-1", "pc-12")), w
    session.refresh(old)
    assert old.state == "queued" and old.worker is None
