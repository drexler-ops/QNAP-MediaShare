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
from collections import defaultdict
from typing import Any

import numpy as np
from sqlalchemy import delete, func, select
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


def run_face_grouping(session: Session) -> dict[str, Any]:
    s = get_settings(session)
    auto_t, suggest_t = float(s["face_auto_threshold"]), float(s["face_suggest_threshold"])
    cluster_t, min_size = float(s["face_cluster_threshold"]), int(s["face_min_cluster"])

    faces = list(
        session.scalars(select(Face).join(File, File.id == Face.file_id).where(File.status.in_(LIVE)).order_by(Face.id))
    )
    vecs = vectors(faces)
    person_ids, cents = centroids(faces, vecs)
    named = {p.id for p in session.scalars(select(Person).where(Person.name.is_not(None)))}

    # 1. Match non-manual faces against known people.
    stats = {"faces": len(faces), "people": len(person_ids), "auto": 0, "suggested": 0, "clusters": 0}
    unassigned: list[int] = []
    for i, f in enumerate(faces):
        if f.assignment == "manual":
            continue
        f.person_id, f.assignment, f.similarity, f.suggested_person_id = None, "none", None, None
        if len(person_ids):
            sims = cents @ vecs[i]
            for rejected in f.rejected or []:
                if rejected in person_ids:
                    sims[person_ids.index(rejected)] = -1.0
            best = int(np.argmax(sims))
            sim = float(sims[best])
            need = auto_t + (0 if f.quality else LOW_QUALITY_MARGIN)
            if sim >= need:
                f.person_id, f.assignment, f.similarity = person_ids[best], "auto", round(sim, 4)
                stats["auto"] += 1
                continue
            if sim >= suggest_t and person_ids[best] in named:
                f.suggested_person_id, f.similarity = person_ids[best], round(sim, 4)
                stats["suggested"] += 1
        if f.quality and f.suggested_person_id is None:
            unassigned.append(i)

    # 2. Rebuild automatic clusters (unnamed, unhidden people without manual faces are recreated each run).
    keep = set(
        session.scalars(select(Face.person_id).where(Face.assignment == "manual", Face.person_id.is_not(None)))
    )
    stale = [
        p.id for p in session.scalars(select(Person).where(Person.name.is_(None), Person.hidden.is_(False)))
        if p.id not in keep
    ]
    session.flush()
    if stale:
        session.execute(delete(Person).where(Person.id.in_(stale)))
    for group in cluster(vecs[unassigned], cluster_t, min_size) if unassigned else []:
        person = Person(name=None)
        session.add(person)
        session.flush()
        for j in group:
            f = faces[unassigned[j]]
            f.person_id, f.assignment = person.id, "cluster"
        stats["clusters"] += 1

    session.flush()
    refresh_covers(session)
    session.commit()
    log.info("face grouping: %s", stats)
    return stats


def refresh_covers(session: Session) -> None:
    """Pick each person's best face (manual first, then largest, most confident) as their picture."""
    best: dict[int, tuple] = {}
    rows = session.execute(
        select(Face.id, Face.person_id, Face.assignment, Face.size_px, Face.score)
        .join(File, File.id == Face.file_id)
        .where(Face.person_id.is_not(None), File.status.in_(LIVE))
    )
    for fid, pid, assignment, size, score in rows:
        key = (assignment == "manual", size * score)
        if pid not in best or key > best[pid][0]:
            best[pid] = (key, fid)
    for p in session.scalars(select(Person)):
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
