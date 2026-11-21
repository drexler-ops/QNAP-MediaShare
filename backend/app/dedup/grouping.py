"""Find duplicate clusters and sync them into dup_groups.

Two independent clusterings:
  * "strong": exact (same SHA-256) + re-encoded (pHash close, same aspect) + video (frame signatures)
  * "burst":  visually similar photos taken within a few seconds (one representative per strong cluster)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import ACTIVE_STATES, Decision, DupGroup, DupGroupMember, File, Item
from ..scanner.hashing import hamming
from ..scanner.livephoto import pair_live_photos
from ..scanner.video import signature_distance
from ..settings_store import get_settings
from .keeper import rank

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Rec:
    item_id: int
    kind: str
    sha256: str | None = None
    phash: str | None = None
    taken_at: datetime | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    video_sig: list[str] | None = None
    exif_score: int = 0

    @property
    def aspect(self) -> float | None:
        if not self.width or not self.height:
            return None
        w, h = sorted((self.width, self.height))
        return w / h


@dataclass
class Cluster:
    type: str  # exact | reencode | video | burst
    item_ids: list[int]
    confidence: float
    match_types: dict[int, str] = field(default_factory=dict)

    @property
    def category(self) -> str:
        return "burst" if self.type == "burst" else "strong"


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for x in self.parent:
            out[self.find(x)].append(x)
        return out


class BKTree:
    """Metric tree over 64-bit hashes for fast Hamming-radius queries."""

    def __init__(self) -> None:
        self.root: list | None = None  # [hash_int, key, {dist: child}]

    def add(self, h: int, key: Any) -> None:
        node = [h, key, {}]
        if self.root is None:
            self.root = node
            return
        cur = self.root
        while True:
            d = (cur[0] ^ h).bit_count()
            child = cur[2].get(d)
            if child is None:
                cur[2][d] = node
                return
            cur = child

    def query(self, h: int, radius: int) -> list[tuple[Any, int]]:
        if self.root is None:
            return []
        out: list[tuple[Any, int]] = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            d = (node[0] ^ h).bit_count()
            if d <= radius:
                out.append((node[1], d))
            for cd, child in node[2].items():
                if d - radius <= cd <= d + radius:
                    stack.append(child)
        return out


def _aspect_ok(a: Rec, b: Rec, tolerance: float = 0.03) -> bool:
    if a.aspect is None or b.aspect is None:
        return True
    return abs(a.aspect - b.aspect) <= tolerance


def _same_capture(a: Rec, b: Rec) -> bool:
    """False when both copies carry capture times that disagree -> different shots (e.g. a burst),
    even if they look nearly identical. Whole-hour differences are treated as timezone artefacts."""
    if not a.taken_at or not b.taken_at:
        return True
    diff = abs((a.taken_at - b.taken_at).total_seconds())
    if diff < 1:
        return True
    hours, rem = divmod(diff, 3600)
    return hours <= 14 and (rem < 1 or 3600 - rem < 1)


def compute_clusters(recs: Iterable[Rec], settings: dict[str, Any]) -> list[Cluster]:
    """Pure clustering over records (unit tested)."""
    recs = list(recs)
    by_id = {r.item_id: r for r in recs}
    strong = UnionFind()
    edge_dist: list[tuple[int, int, float]] = []

    # 1. Exact copies.
    by_sha: dict[str, list[int]] = defaultdict(list)
    for r in recs:
        if r.sha256:
            by_sha[r.sha256].append(r.item_id)
    for ids in by_sha.values():
        for other in ids[1:]:
            strong.union(ids[0], other)
            edge_dist.append((ids[0], other, 0))

    # 2. Re-encoded images: BK-tree over distinct pHash values.
    threshold = int(settings["reencode_threshold"])
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in recs:
        if r.kind == "image" and r.phash:
            buckets[r.phash].append(r.item_id)
    tree = BKTree()
    for h in buckets:
        tree.add(int(h, 16), h)
    for h, ids in buckets.items():
        rep = by_id[ids[0]]
        for other in ids[1:]:
            if _aspect_ok(rep, by_id[other]) and _same_capture(rep, by_id[other]):
                strong.union(rep.item_id, other)
                edge_dist.append((rep.item_id, other, 0))
        for h2, d in tree.query(int(h, 16), threshold):
            if h2 <= h:  # each unordered pair once (and skip self)
                continue
            rep2 = by_id[buckets[h2][0]]
            if _aspect_ok(rep, rep2) and _same_capture(rep, rep2):
                strong.union(rep.item_id, rep2.item_id)
                edge_dist.append((rep.item_id, rep2.item_id, d))

    # 3. Videos: similar duration + similar frame signatures.
    tol = float(settings["video_duration_tolerance"])
    vthreshold = float(settings["video_frame_threshold"])
    videos = sorted(
        (r for r in recs if r.kind == "video" and r.duration and r.video_sig), key=lambda r: r.duration or 0
    )
    for i, a in enumerate(videos):
        for b in videos[i + 1 :]:
            if (b.duration or 0) - (a.duration or 0) > tol:
                break
            if a.sha256 and a.sha256 == b.sha256:
                continue
            d = signature_distance(a.video_sig, b.video_sig)
            if d <= vthreshold:
                strong.union(a.item_id, b.item_id)
                edge_dist.append((a.item_id, b.item_id, d))

    clusters: list[Cluster] = []
    root_maxd: dict[int, float] = defaultdict(float)
    for a, _b, d in edge_dist:
        root = strong.find(a)
        root_maxd[root] = max(root_maxd[root], d)

    rep_of_root: dict[int, int] = {}
    for root, ids in strong.groups().items():
        if len(ids) < 2:
            continue
        ids.sort()
        members = [by_id[i] for i in ids]
        shas = {m.sha256 for m in members}
        sha_counts: dict[str | None, int] = defaultdict(int)
        for m in members:
            sha_counts[m.sha256] += 1
        is_video = all(m.kind == "video" for m in members)
        if len(shas) == 1 and None not in shas:
            ctype, confidence = "exact", 1.0
        else:
            ctype = "video" if is_video else "reencode"
            scale = vthreshold * 2 if is_video else 32
            confidence = round(max(0.3, 1 - root_maxd[root] / scale), 2)
        match_types = {
            m.item_id: ("exact" if m.sha256 and sha_counts[m.sha256] > 1 else ctype) for m in members
        }
        clusters.append(Cluster(ctype, ids, confidence, match_types))
        # Representative for burst detection: the copy most likely to be kept.
        rep = max(members, key=lambda m: (m.exif_score, (m.width or 0) * (m.height or 0), -m.item_id))
        rep_of_root[root] = rep.item_id

    # 4. Bursts / near-duplicates among photos with a capture time.
    burst_threshold = int(settings["burst_threshold"])
    window = float(settings["burst_window_seconds"])
    timed = []
    for r in recs:
        if r.kind != "image" or not r.phash or not r.taken_at:
            continue
        root = strong.find(r.item_id) if r.item_id in strong.parent else r.item_id
        if root in rep_of_root and rep_of_root[root] != r.item_id:
            continue  # only one copy of each strong cluster takes part
        timed.append((r, root))
    timed.sort(key=lambda pair: pair[0].taken_at)
    burst = UnionFind()
    burst_maxd: dict[tuple[int, int], int] = {}
    for i, (a, root_a) in enumerate(timed):
        for b, root_b in timed[i + 1 :]:
            if (b.taken_at - a.taken_at).total_seconds() > window:
                break
            if root_a == root_b:
                continue
            d = hamming(a.phash, b.phash)
            if d <= burst_threshold:
                burst.union(a.item_id, b.item_id)
                burst_maxd[(a.item_id, b.item_id)] = d
    burst_root_maxd: dict[int, int] = defaultdict(int)
    for (a_id, _b), d in burst_maxd.items():
        r = burst.find(a_id)
        burst_root_maxd[r] = max(burst_root_maxd[r], d)
    for root, ids in burst.groups().items():
        if len(ids) < 2:
            continue
        ids.sort()
        confidence = round(max(0.2, 1 - burst_root_maxd[root] / 64), 2)
        clusters.append(Cluster("burst", ids, confidence, {i: "burst" for i in ids}))

    return clusters


# --- DB sync -----------------------------------------------------------------------------


def _load_recs(session: Session) -> list[Rec]:
    rows = session.execute(
        select(
            File.item_id, File.kind, File.sha256, File.phash, File.taken_at, File.duration,
            File.width, File.height, File.video_sig, File.exif_score,
        ).where(
            File.role == "primary",
            File.status.in_(("active", "pending_delete")),
            File.process_state == "done",
            File.item_id.is_not(None),
        )
    )
    return [Rec(*row) for row in rows]


def _active_decision_items(session: Session, group_id: int) -> set[int]:
    return set(
        session.scalars(
            select(Decision.item_id).where(Decision.group_id == group_id, Decision.state.in_(ACTIVE_STATES))
        )
    )


def rescore_group(session: Session, group: DupGroup, weights: dict[str, float]) -> None:
    item_ids = [m.item_id for m in group.members]
    if not item_ids:
        return
    items = {
        it.id: it
        for it in session.scalars(select(Item).where(Item.id.in_(item_ids)).options(selectinload(Item.files)))
    }
    primaries = {iid: items[iid].primary for iid in item_ids if iid in items and items[iid].primary}
    ranked = rank(list(primaries.values()), weights)
    file_to_item = {f.id: iid for iid, f in primaries.items()}
    best_item = file_to_item[ranked[0][0].id] if ranked else None
    scores = {file_to_item[f.id]: s for f, s in ranked}
    for m in group.members:
        s = scores.get(m.item_id)
        m.suggested_keep = m.item_id == best_item
        m.score = s["score"] if s else 0.0
        m.score_breakdown = {"parts": s["breakdown"], "notes": s["notes"]} if s else None


def sync_groups(session: Session, clusters: list[Cluster], weights: dict[str, float]) -> dict[str, int]:
    stats = {"created": 0, "updated": 0, "merged": 0, "reopened": 0}
    groups = list(session.scalars(select(DupGroup).options(selectinload(DupGroup.members))))
    by_item: dict[tuple[str, int], set[int]] = defaultdict(set)
    gmap = {g.id: g for g in groups}
    for g in groups:
        for m in g.members:
            by_item[(g.category, m.item_id)].add(g.id)

    touched: set[int] = set()
    for c in clusters:
        overlap: dict[int, int] = defaultdict(int)
        for iid in c.item_ids:
            for gid in by_item.get((c.category, iid), ()):
                overlap[gid] += 1
        if not overlap:
            g = DupGroup(type=c.type, confidence=c.confidence, status="open")
            g.members = [DupGroupMember(item_id=i, match_type=c.match_types.get(i, c.type)) for i in c.item_ids]
            session.add(g)
            session.flush()
            gmap[g.id] = g
            for i in c.item_ids:
                by_item[(c.category, i)].add(g.id)
            touched.add(g.id)
            stats["created"] += 1
            continue

        target_id = max(overlap, key=lambda gid: (overlap[gid], -gid))
        g = gmap[target_id]
        # Fold other overlapping groups in, unless someone already made decisions there.
        for gid in overlap:
            if gid == target_id:
                continue
            other = gmap[gid]
            if other.status == "open" and not _active_decision_items(session, gid):
                for m in list(other.members):
                    by_item[(c.category, m.item_id)].discard(gid)
                session.delete(other)
                stats["merged"] += 1

        existing = {m.item_id: m for m in g.members}
        added = False
        for i in c.item_ids:
            if i not in existing:
                g.members.append(DupGroupMember(item_id=i, match_type=c.match_types.get(i, c.type)))
                by_item[(c.category, i)].add(g.id)
                added = True
            else:
                existing[i].match_type = c.match_types.get(i, c.type)
        if g.status == "open":
            decided = _active_decision_items(session, g.id)
            wanted = set(c.item_ids)
            for m in list(g.members):
                if m.item_id not in wanted and m.item_id not in decided:
                    g.members.remove(m)
                    by_item[(c.category, m.item_id)].discard(g.id)
            g.type = c.type
            g.confidence = c.confidence
        if added and g.status != "open":
            g.status = "open"
            stats["reopened"] += 1
        touched.add(g.id)
        stats["updated"] += 1

    session.flush()
    for gid in touched:
        g = session.get(DupGroup, gid)
        if g is not None:
            rescore_group(session, g, weights)
    return stats


def run_grouping(session: Session) -> dict[str, Any]:
    settings = get_settings(session)
    paired = pair_live_photos(session)
    recs = _load_recs(session)
    clusters = compute_clusters(recs, settings)
    stats = sync_groups(session, clusters, settings["keeper_weights"])
    session.commit()
    result = {"live_pairs": paired, "items": len(recs), "clusters": len(clusters), **stats}
    log.info("grouping: %s", result)
    return result
