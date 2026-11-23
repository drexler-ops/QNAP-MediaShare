"""Recognise named people and group the remaining faces into unnamed clusters.

Rules:
  * Faces a user assigned ("manual") are never changed here; they define each person.
  * Other faces are matched to the nearest person (centroid of their manual faces):
      similarity >= auto threshold      -> assigned automatically ("auto")
      suggest <= similarity < auto       -> offered for review ("Is this Sue?")
    People a user said a face is NOT are skipped for that face.
  * Still-unassigned good-quality faces are grouped (similarity graph, connected components);
    groups of >= min size become unnamed people waiting for a name.
"""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from ..models import Face, File, Person
from ..settings_store import get_settings

log = logging.getLogger(__name__)
LIVE = ("active", "pending_delete")
LOW_QUALITY_MARGIN = 0.08  # small/blurry faces need a stronger match to be auto-assigned


def vectors(faces: list[Face]) -> np.ndarray:
    if not faces:
        return np.zeros((0, 512), dtype=np.float32)
    return np.stack([np.frombuffer(f.vector, dtype=np.float32) for f in faces])


def centroids(faces: list[Face], vecs: np.ndarray) -> tuple[list[int], np.ndarray]:
    groups: dict[int, list[int]] = defaultdict(list)
    for i, f in enumerate(faces):
        if f.assignment == "manual" and f.person_id is not None:
            groups[f.person_id].append(i)
    ids = sorted(groups)
    if not ids:
        return [], np.zeros((0, vecs.shape[1] if vecs.size else 512), dtype=np.float32)
    cents = np.stack([vecs[groups[p]].mean(0) for p in ids])
    cents /= np.linalg.norm(cents, axis=1, keepdims=True) + 1e-9
    return ids, cents


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def cluster(vecs: np.ndarray, threshold: float, min_size: int, chunk: int = 256) -> list[list[int]]:
    """Connected components of the 'similarity >= threshold' graph. Pure function (unit tested)."""
    n = len(vecs)
    uf = _UF(n)
    for start in range(0, n, chunk):
        sims = vecs[start:start + chunk] @ vecs.T
        rows, cols = np.nonzero(sims >= threshold)
        for r, c in zip(rows.tolist(), cols.tolist()):
            i = start + r
            if c > i:
                uf.union(i, c)
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return sorted((g for g in groups.values() if len(g) >= min_size), key=len, reverse=True)


LOCK_KEY = 74_311_001  # pg advisory lock: user edits to faces/people vs. the regroup job's write phase


def faces_lock(session: Session) -> None:
    """Take the faces/people write lock until this transaction ends (Postgres; no-op on SQLite).

    User edits take it before changing anything; the regroup job takes it only for its short write
    phase, after checking nothing changed while it was computing. Edits never get overwritten.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("select pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})


def _user_state(session: Session, face_ids: list[int] | None = None) -> tuple[dict, dict]:
    """What users decide: manual assignments, 'not this person' lists, names, hidden flags."""
    # Read every face and filter here: an IN (...) list of all face ids breaks past 65,535 faces
    # (PostgreSQL's limit on query parameters), and this table scan is cheap anyway.
    wanted = set(face_ids) if face_ids is not None else None
    faces = {
        fid: (pid if a == "manual" else None, a == "manual", tuple(sorted(rej or ())))
        for fid, a, pid, rej in session.execute(select(Face.id, Face.assignment, Face.person_id, Face.rejected))
        if wanted is None or fid in wanted
    }
    people = {pid: (name, hidden) for pid, name, hidden in session.execute(select(Person.id, Person.name, Person.hidden))}
    return faces, people


def run_face_grouping(session: Session, attempts: int = 5) -> dict[str, Any]:
    for attempt in range(attempts):
        stats = _group_once(session)
        if stats is not None:
            return stats
        log.info("face grouping: names/faces changed while computing; recomputing (attempt %s)", attempt + 2)
        time.sleep(0.5)
    return {"skipped": "faces kept changing; the next regroup will catch up"}


def _group_once(session: Session) -> dict[str, Any] | None:
    s = get_settings(session)
    auto_t, suggest_t = float(s["face_auto_threshold"]), float(s["face_suggest_threshold"])
    cluster_t, min_size = float(s["face_cluster_threshold"]), int(s["face_min_cluster"])

    # ---- 1. compute (no lock; plain rows, nothing written) ----
    rows = session.execute(
        select(Face.id, Face.vector, Face.quality, Face.assignment, Face.person_id, Face.rejected,
               Face.similarity, Face.suggested_person_id)
        .join(File, File.id == Face.file_id).where(File.status.in_(LIVE)).order_by(Face.id)
    ).all()
    before_faces, before_people = _user_state(session, [r.id for r in rows])
    session.commit()  # end the read transaction; the write phase re-checks under the lock

    n = len(rows)
    vecs = np.stack([np.frombuffer(r.vector, dtype=np.float32) for r in rows]) if n else np.zeros((0, 512), np.float32)
    manual: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r.assignment == "manual" and r.person_id is not None and r.person_id in before_people:
            manual[r.person_id].append(i)
    person_ids = sorted(manual)
    cents = np.stack([vecs[manual[p]].mean(0) for p in person_ids]) if person_ids else np.zeros((0, 512), np.float32)
    if person_ids:
        cents /= np.linalg.norm(cents, axis=1, keepdims=True) + 1e-9
    named = {pid for pid, (name, _) in before_people.items() if name}

    stats = {"faces": n, "people": len(person_ids), "auto": 0, "suggested": 0, "clusters": 0}
    new: dict[int, tuple] = {}  # row index -> (person_id, assignment, similarity, suggested_person_id)
    unassigned: list[int] = []
    for i, r in enumerate(rows):
        if r.assignment == "manual":
            continue
        result = (None, "none", None, None)
        if person_ids:
            sims = cents @ vecs[i]
            for rejected in r.rejected or []:
                if rejected in manual:
                    sims[person_ids.index(rejected)] = -1.0
            best = int(np.argmax(sims))
            sim = float(sims[best])
            need = auto_t + (0 if r.quality else LOW_QUALITY_MARGIN)
            if sim >= need:
                result = (person_ids[best], "auto", round(sim, 4), None)
                stats["auto"] += 1
            elif sim >= suggest_t and person_ids[best] in named:
                result = (None, "none", round(sim, 4), person_ids[best])
                stats["suggested"] += 1
        new[i] = result
        if r.quality and result[1] == "none" and result[3] is None:
            unassigned.append(i)

    # Unnamed, unhidden groups without confirmed faces are rebuilt each run, but keep their ids when
    # the new group is mostly the same faces, so pages and links stay valid while people name groups.
    confirmed = set(session.scalars(select(Face.person_id).where(Face.assignment == "manual", Face.person_id.is_not(None))))
    session.commit()
    reusable = {pid for pid, (name, hidden) in before_people.items() if not name and not hidden and pid not in confirmed}
    groups = cluster(vecs[unassigned], cluster_t, min_size) if unassigned else []
    planned: list[tuple[int | None, list[int]]] = []
    for g in groups:  # largest first
        members = [unassigned[j] for j in g]
        votes = Counter(rows[i].person_id for i in members if rows[i].assignment == "cluster" and rows[i].person_id in reusable)
        pid = votes.most_common(1)[0][0] if votes else None
        if pid is not None:
            reusable.discard(pid)
        planned.append((pid, members))
    stale = reusable  # old groups nobody inherited

    # ---- 2. write (under the lock, only if nobody changed names/faces meanwhile) ----
    faces_lock(session)
    after_faces, after_people = _user_state(session, [r.id for r in rows])
    if after_faces != before_faces or after_people != before_people:
        session.rollback()
        return None

    for pid, members in planned:
        if pid is None:
            p = Person(name=None)
            session.add(p)
            session.flush()
            pid = p.id
        for i in members:
            new[i] = (pid, "cluster", None, None)
        stats["clusters"] += 1

    updates = []
    for i, (pid, assignment, sim, sugg) in new.items():
        r = rows[i]
        if (r.person_id, r.assignment, r.similarity, r.suggested_person_id) != (pid, assignment, sim, sugg):
            updates.append({"id": r.id, "person_id": pid, "assignment": assignment, "similarity": sim, "suggested_person_id": sugg})
    if updates:
        session.execute(update(Face), updates)
    if stale:
        # Faces added since step 1 never point at these (new faces start unassigned).
        session.execute(update(Face).where(Face.person_id.in_(stale)).values(person_id=None, assignment="none"), execution_options={"synchronize_session": False})
        session.execute(delete(Person).where(Person.id.in_(stale)))
    session.flush()
    refresh_covers(session)
    session.commit()
    stats["changed"] = len(updates)
    log.info("face grouping: %s", stats)
    return stats


def refresh_covers(session: Session) -> None:
    """Pick each person's best face (manual first, then largest, most confident) as their picture.

    A cover an admin chose (cover_pinned) is kept while that face is still this person's, in a live photo;
    otherwise it is released and the automatic pick takes over.
    """
    best: dict[int, tuple] = {}
    owner: dict[int, int] = {}  # face -> person, live faces only
    rows = session.execute(
        select(Face.id, Face.person_id, Face.assignment, Face.size_px, Face.score)
        .join(File, File.id == Face.file_id)
        .where(Face.person_id.is_not(None), File.status.in_(LIVE))
    )
    for fid, pid, assignment, size, score in rows:
        owner[fid] = pid
        key = (assignment == "manual", size * score)
        if pid not in best or key > best[pid][0]:
            best[pid] = (key, fid)
    for p in session.scalars(select(Person)):
        if p.cover_pinned and p.cover_face_id is not None and owner.get(p.cover_face_id) == p.id:
            continue
        p.cover_pinned = False
        p.cover_face_id = best[p.id][1] if p.id in best else None


def person_counts(session: Session) -> dict[int, tuple[int, int]]:
    """person_id -> (faces, distinct photos) for live files."""
    rows = session.execute(
        select(Face.person_id, func.count(Face.id), func.count(func.distinct(Face.file_id)))
        .join(File, File.id == Face.file_id)
        .where(Face.person_id.is_not(None), File.status.in_(LIVE))
        .group_by(Face.person_id)
    )
    return {pid: (n, photos) for pid, n, photos in rows}
