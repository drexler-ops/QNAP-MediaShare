"""People & faces (signed-in users; one shared family list, every change audited)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..audit import audit
from ..faces.engine import models_ready
from ..faces.grouping import faces_lock, person_counts, refresh_covers
from ..faces.pipeline import delete_face_thumb
from ..jobs import enqueue
from ..models import Face, File, Item, Job, Person, User
from ..scanner.thumbs import face_thumb_path
from ..settings_store import get_settings
from .deps import current_user, get_db
from .serialize import Names, item_dict

router = APIRouter(prefix="/api", tags=["people"])
LIVE = ("active", "pending_delete")


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    hidden: bool | None = None


class MergeIn(BaseModel):
    into: int


class AssignIn(BaseModel):
    face_ids: list[int]
    person_id: int | None = None
    name: str | None = Field(default=None, max_length=128)


class FaceIds(BaseModel):
    face_ids: list[int]


def _person(db: Session, pid: int) -> Person:
    p = db.get(Person, pid)
    if p is None:
        raise HTTPException(404, "That group no longer exists (it was merged or regrouped). The list has been refreshed; please try again.")
    return p


def _person_dict(p: Person, counts: dict[int, tuple[int, int]]) -> dict:
    n, photos = counts.get(p.id, (0, 0))
    return {"id": p.id, "name": p.name, "hidden": p.hidden, "cover_face_id": p.cover_face_id, "faces": n, "photos": photos}


def _by_name(db: Session, name: str, exclude: int | None = None) -> Person | None:
    q = select(Person).where(func.lower(Person.name) == name.lower())
    if exclude is not None:
        q = q.where(Person.id != exclude)
    return db.scalar(q)


def _make_manual(db: Session, pid: int) -> None:
    """Freeze a person's current faces as confirmed (when it's named or hidden by a user)."""
    for f in db.scalars(select(Face).where(Face.person_id == pid, Face.assignment.in_(("cluster", "auto")))):
        f.assignment = "manual"


def _merge(db: Session, src: Person, dst: Person, user: User) -> None:
    for f in db.scalars(select(Face).where(Face.person_id == src.id)):
        f.person_id, f.assignment = dst.id, "manual"
    for f in db.scalars(select(Face).where(Face.suggested_person_id == src.id)):
        f.suggested_person_id = dst.id
    audit(db, user.id, "person_merge", "person", dst.id, merged=src.id, name=dst.name)
    db.delete(src)
    db.flush()
    refresh_covers(db)


def _regrouping(db: Session) -> bool:
    return bool(db.scalar(select(func.count(Job.id)).where(Job.type == "face_group", Job.state.in_(("queued", "running")))))


def _regroup(db: Session) -> None:
    enqueue(db, "face_group", unique=True, priority=3)


@router.get("/people")
def list_people(include_hidden: bool = False, db: Session = Depends(get_db), _: User = Depends(current_user)):
    counts = person_counts(db)
    people = list(db.scalars(select(Person)))
    named = sorted((p for p in people if p.name and (include_hidden or not p.hidden)), key=lambda p: p.name.lower())
    unnamed = sorted(
        (p for p in people if not p.name and (include_hidden or not p.hidden) and counts.get(p.id, (0, 0))[0]),
        key=lambda p: -counts.get(p.id, (0, 0))[0],
    )
    total = db.scalar(select(func.count(File.id)).where(File.kind == "image", File.status.in_(LIVE))) or 0
    done = db.scalar(select(func.count(File.id)).where(
        File.kind == "image", File.status.in_(LIVE), File.face_state.in_(("done", "skip", "error")))) or 0
    review = db.scalar(select(func.count(Face.id)).where(Face.suggested_person_id.is_not(None), Face.person_id.is_(None))) or 0
    return {
        "named": [_person_dict(p, counts) for p in named],
        "unnamed": [_person_dict(p, counts) for p in unnamed[:120]],
        "unnamed_total": len(unnamed),
        "review": review,
        "status": {
            "enabled": bool(get_settings(db)["faces_enabled"]),
            "models_ready": models_ready(),
            "photos_total": total,
            "photos_done": done,
            "faces": db.scalar(select(func.count(Face.id))) or 0,
            # Background regroup pending: names are saved already, but groups/counts are still updating.
            "regrouping": _regrouping(db),
        },
    }


@router.get("/people/names")
def names(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{"id": p.id, "name": p.name} for p in db.scalars(select(Person).where(Person.name.is_not(None)).order_by(Person.name))]


@router.get("/people/review")
def review(limit: int = Query(120, le=500), db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.execute(
        select(Face, File.item_id, Person.name)
        .join(File, File.id == Face.file_id)
        .join(Person, Person.id == Face.suggested_person_id)
        .where(Face.person_id.is_(None), File.status.in_(LIVE))
        .order_by(Face.similarity.desc())
        .limit(limit)
    )
    return [
        {"face_id": f.id, "file_id": f.file_id, "item_id": item_id, "suggested_id": f.suggested_person_id,
         "suggested_name": name, "similarity": f.similarity}
        for f, item_id, name in rows
    ]


@router.get("/people/{person_id}")
def get_person(person_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    p = _person(db, person_id)
    faces = db.execute(
        select(Face, File.item_id)
        .join(File, File.id == Face.file_id)
        .where(Face.person_id == p.id, File.status.in_(LIVE))
        .order_by(File.taken_at.desc().nulls_last(), Face.id.desc())
    ).all()
    item_ids = list(dict.fromkeys(item_id for _, item_id in faces if item_id))
    items = {it.id: it for it in db.scalars(select(Item).where(Item.id.in_(item_ids)).options(selectinload(Item.files)))}
    names_ = Names(db)
    return {
        **_person_dict(p, person_counts(db)),
        "face_list": [
            {"face_id": f.id, "file_id": f.file_id, "item_id": item_id, "assignment": f.assignment, "similarity": f.similarity}
            for f, item_id in faces
        ],
        "items": [item_dict(items[i], names_) for i in item_ids if i in items],
        "regrouping": _regrouping(db),
    }


@router.patch("/people/{person_id}")
def update_person(person_id: int, body: PersonPatch, db: Session = Depends(get_db), user: User = Depends(current_user)):
    faces_lock(db)  # wait for a regroup that is writing right now; it never overwrites edits
    p = _person(db, person_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Name can't be empty")
        other = _by_name(db, name, exclude=p.id)
        _make_manual(db, p.id)
        if other is not None:  # naming a group with an existing name merges them
            _merge(db, p, other, user)
            _regroup(db)
            return {"id": other.id, "merged": True}
        audit(db, user.id, "person_name", "person", p.id, old=p.name, new=name)
        p.name = name
    if body.hidden is not None and body.hidden != p.hidden:
        if body.hidden:
            _make_manual(db, p.id)  # keep these faces out of future automatic groups
        p.hidden = body.hidden
        audit(db, user.id, "person_hide" if body.hidden else "person_unhide", "person", p.id, name=p.name)
    _regroup(db)
    return {"id": p.id, "merged": False}


@router.post("/people/{person_id}/merge")
def merge(person_id: int, body: MergeIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    faces_lock(db)  # wait for a regroup that is writing right now; it never overwrites edits
    src, dst = _person(db, person_id), _person(db, body.into)
    if src.id == dst.id:
        raise HTTPException(400, "Can't merge a person into themselves")
    if not dst.name and src.name:
        dst.name = src.name
    _merge(db, src, dst, user)
    _regroup(db)
    return {"id": dst.id}


@router.post("/faces/assign")
def assign(body: AssignIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    faces_lock(db)  # wait for a regroup that is writing right now; it never overwrites edits
    if body.person_id is None and not (body.name or "").strip():
        raise HTTPException(400, "Choose a person or type a name")
    if body.person_id is not None:
        person = _person(db, body.person_id)
    else:
        name = body.name.strip()
        person = _by_name(db, name)
        if person is None:
            person = Person(name=name)
            db.add(person)
            db.flush()
            audit(db, user.id, "person_create", "person", person.id, name=name)
    faces = list(db.scalars(select(Face).where(Face.id.in_(body.face_ids))))
    for f in faces:
        f.person_id, f.assignment, f.suggested_person_id = person.id, "manual", None
        if f.rejected and person.id in f.rejected:
            f.rejected = [r for r in f.rejected if r != person.id]
    audit(db, user.id, "faces_assign", "person", person.id, faces=len(faces), name=person.name)
    db.flush()
    refresh_covers(db)
    _regroup(db)
    return {"person_id": person.id, "name": person.name, "faces": len(faces)}


@router.post("/faces/confirm")
def confirm(body: FaceIds, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Accept the suggested person for these faces."""
    faces_lock(db)  # wait for a regroup that is writing right now; it never overwrites edits
    n = 0
    for f in db.scalars(select(Face).where(Face.id.in_(body.face_ids), Face.suggested_person_id.is_not(None))):
        f.person_id, f.assignment, f.suggested_person_id = f.suggested_person_id, "manual", None
        n += 1
    audit(db, user.id, "faces_confirm", None, None, faces=n)
    _regroup(db)
    return {"confirmed": n}


@router.post("/faces/unassign")
def unassign(body: FaceIds, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """'Not this person': remove and remember, so it's never auto-assigned to them again."""
    faces_lock(db)  # wait for a regroup that is writing right now; it never overwrites edits
    n = 0
    for f in db.scalars(select(Face).where(Face.id.in_(body.face_ids))):
        wrong = f.person_id or f.suggested_person_id
        if wrong:
            f.rejected = sorted(set(f.rejected or []) | {wrong})
        f.person_id, f.assignment, f.suggested_person_id, f.similarity = None, "none", None, None
        n += 1
    audit(db, user.id, "faces_unassign", None, None, faces=n)
    db.flush()
    refresh_covers(db)
    _regroup(db)
    return {"removed": n}


@router.post("/faces/ignore")
def ignore(body: FaceIds, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """'Not a face' (a pattern, a statue, a poster): delete the detection."""
    faces_lock(db)  # wait for a regroup that is writing right now; it never overwrites edits
    faces = list(db.scalars(select(Face).where(Face.id.in_(body.face_ids))))
    for f in faces:
        delete_face_thumb(f.id)
        db.delete(f)
    audit(db, user.id, "faces_ignore", None, None, faces=len(faces))
    db.flush()
    refresh_covers(db)
    return {"deleted": len(faces)}


@router.get("/faces/{face_id}/thumb")
def face_thumb(face_id: int, _: User = Depends(current_user)):
    path = face_thumb_path(face_id)
    if not os.path.isfile(path):
        raise HTTPException(404, "No thumbnail")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/items/{item_id}/faces")
def item_faces(item_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    file_id = db.scalar(select(File.id).where(File.item_id == item_id, File.role == "primary"))
    rows = db.execute(
        select(Face, Person.name).outerjoin(Person, Person.id == Face.person_id).where(Face.file_id == file_id).order_by(Face.id)
    ).all() if file_id else []
    sugg = {p.id: p.name for p in db.scalars(select(Person).where(
        Person.id.in_([f.suggested_person_id for f, _ in rows if f.suggested_person_id])))}
    return [
        {"face_id": f.id, "bbox": f.bbox, "person_id": f.person_id, "name": name, "assignment": f.assignment,
         "suggested_id": f.suggested_person_id, "suggested_name": sugg.get(f.suggested_person_id)}
        for f, name in rows
    ]
