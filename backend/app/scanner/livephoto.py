"""Pair iOS Live Photos (still HEIC/JPG + short MOV) into a single Item."""
from __future__ import annotations

import posixpath
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import Decision, DupGroupMember, File, Item

LIVE_MAX_SECONDS = 5.0


@dataclass(frozen=True)
class Candidate:
    id: int
    path: str
    live_photo_id: str | None
    duration: float | None = None


def match_pairs(stills: list[Candidate], videos: list[Candidate]) -> list[tuple[int, int]]:
    """Return (still_id, video_id) pairs. Pure function (unit tested).

    1. Same folder + same Apple ContentIdentifier (the reliable signal).
    2. Fallback: same folder + same file stem (IMG_1234.HEIC + IMG_1234.MOV) and a short video.
    """
    pairs: list[tuple[int, int]] = []
    used_stills: set[int] = set()
    used_videos: set[int] = set()

    by_cid: dict[tuple[str, str], Candidate] = {}
    for s in stills:
        if s.live_photo_id:
            by_cid.setdefault((posixpath.dirname(s.path), s.live_photo_id), s)
    for v in videos:
        if not v.live_photo_id:
            continue
        s = by_cid.get((posixpath.dirname(v.path), v.live_photo_id))
        if s and s.id not in used_stills:
            pairs.append((s.id, v.id))
            used_stills.add(s.id)
            used_videos.add(v.id)

    def stem(p: str) -> tuple[str, str]:
        return posixpath.dirname(p), posixpath.splitext(posixpath.basename(p))[0].lower()

    by_stem: dict[tuple[str, str], Candidate] = {}
    for s in stills:
        if s.id not in used_stills:
            by_stem.setdefault(stem(s.path), s)
    for v in videos:
        if v.id in used_videos:
            continue
        if v.duration is not None and v.duration > LIVE_MAX_SECONDS:
            continue
        s = by_stem.get(stem(v.path))
        if s and s.id not in used_stills:
            pairs.append((s.id, v.id))
            used_stills.add(s.id)
            used_videos.add(v.id)
    return pairs


def pair_live_photos(session: Session) -> int:
    """Merge unpaired Live Photo videos into their still's Item. Returns the number of new pairs."""
    live_states = ("active", "pending_delete")
    stills = [
        Candidate(f.id, f.path, f.live_photo_id)
        for f in session.execute(
            select(File.id, File.path, File.live_photo_id).where(
                File.kind == "image", File.status.in_(live_states), File.process_state == "done"
            )
        )
    ]
    # Only videos that are still standalone (role primary, alone in their item).
    solo_items = select(File.item_id).group_by(File.item_id).having(func.count(File.id) == 1)
    videos = [
        Candidate(f.id, f.path, f.live_photo_id, f.duration)
        for f in session.execute(
            select(File.id, File.path, File.live_photo_id, File.duration).where(
                File.kind == "video",
                File.role == "primary",
                File.status.in_(live_states),
                File.process_state == "done",
                File.item_id.in_(solo_items),
            )
        )
    ]
    # Stills that already have a companion video are not candidates.
    paired_items = set(session.scalars(select(File.item_id).where(File.role == "live_video")))
    still_item = {f.id: f.item_id for f in session.execute(select(File.id, File.item_id).where(File.kind == "image"))}
    stills = [s for s in stills if still_item.get(s.id) not in paired_items]

    count = 0
    for still_id, video_id in match_pairs(stills, videos):
        still = session.get(File, still_id)
        video = session.get(File, video_id)
        if still is None or video is None or still.item_id is None:
            continue
        old_item_id = video.item_id
        has_history = session.scalar(select(func.count(Decision.id)).where(Decision.item_id == old_item_id))
        if has_history:
            continue  # someone already made decisions about the video on its own; leave it alone
        video.item_id = still.item_id
        video.role = "live_video"
        session.flush()
        session.execute(delete(DupGroupMember).where(DupGroupMember.item_id == old_item_id))
        session.execute(delete(Item).where(Item.id == old_item_id))
        count += 1
    session.flush()
    return count
