"""End-to-end: scan -> exiftool/hash/thumbs -> SHA pass -> grouping, on a generated library.

Needs exiftool + ffmpeg (present in the Docker image; skipped elsewhere).
"""
import os
import shutil

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dedup.grouping import run_grouping
from app.jobs import _sha_needed
from app.models import DupGroup, File, Item
from app.scanner.process import hash_files, process_files
from app.scanner.thumbs import thumb_path
from app.scanner.walker import scan

pytestmark = pytest.mark.skipif(
    not (shutil.which("exiftool") and shutil.which("ffmpeg")), reason="needs exiftool and ffmpeg"
)


def _groups(session):
    out = {}
    for g in session.scalars(select(DupGroup).options(selectinload(DupGroup.members))):
        paths = []
        for m in g.members:
            item = session.scalar(select(Item).where(Item.id == m.item_id).options(selectinload(Item.files)))
            paths.append((item.primary.path, m.suggested_keep))
        out.setdefault(g.type, []).append(sorted(paths))
    return out


def test_full_pipeline(session, users, library):
    from .fixtures_gen import build

    build(library)
    res = scan(session)
    assert res.new == 14
    process_files(session, res.to_process)

    files = {f.path: f for f in session.scalars(select(File))}
    errors = {p: f.process_error for p, f in files.items() if f.process_state != "done"}
    assert not errors, errors

    heic = files["Jamie/IMG_0001.HEIC"]
    assert heic.has_exif and heic.gps_lat and round(heic.gps_lat, 2) == 41.88
    # Tests seed only Alex + Sam, so Jamie's folder is unassigned.
    assert files["Sam/IMG_0001.HEIC"].owner_id == users["Sam"].id and heic.owner_id is None
    assert (heic.width, heic.height) == (2400, 1800)
    assert os.path.isfile(thumb_path(heic.id, "s")) and os.path.isfile(thumb_path(heic.id, "l"))
    assert not files["Alex/WhatsApp/IMG-20240704-WA0003.jpg"].has_exif
    clip = files["Jamie/Videos/clip.mp4"]
    assert clip.kind == "video" and clip.codec == "h264" and abs(clip.duration - 6) < 0.2
    assert clip.video_sig and len(clip.video_sig) == 10 and clip.thumb_ready

    # Videos are only SHA-hashed when their size collides with another file.
    need = _sha_needed(session)
    hash_files(session, need)
    assert _sha_needed(session) == []

    result = run_grouping(session)
    assert result["live_pairs"] == 1
    live = files["Jamie/Live/IMG_2001.MOV"]
    session.refresh(live)
    assert live.role == "live_video" and live.item_id == files["Jamie/Live/IMG_2001.HEIC"].item_id

    groups = _groups(session)
    same_photo = groups["reencode"]
    assert len(same_photo) == 1
    members = dict(same_photo[0])
    assert set(members) == {
        "Jamie/IMG_0001.HEIC", "Sam/IMG_0001.HEIC", "Sam/Exports/IMG_0001.jpg",
        "Alex/WhatsApp/IMG-20240704-WA0003.jpg",
    }
    keeper = [p for p, k in members.items() if k]
    assert len(keeper) == 1 and keeper[0].endswith("IMG_0001.HEIC")

    (exact,) = groups["exact"]
    assert {p for p, _ in exact} == {"Riley/screenshot copy.png", "Alex/Screens/screenshot.png"}
    assert sum(keep for _, keep in exact) == 1  # identical files: exactly one suggested keeper
    assert [sorted(p for p, _ in g) for g in groups["burst"]] == [
        ["Riley/Burst/IMG_1001.JPG", "Riley/Burst/IMG_1002.JPG", "Riley/Burst/IMG_1003.JPG"]
    ]
    assert [sorted(p for p, _ in g) for g in groups["video"]] == [["Jamie/Videos/clip.mp4", "Sam/Videos/clip_small.mp4"]]
    # Higher-resolution video is the suggested keeper.
    assert dict(groups["video"][0])["Jamie/Videos/clip.mp4"] is True

    # Re-running is idempotent (no duplicate groups).
    again = run_grouping(session)
    assert again["created"] == 0
    assert sum(len(v) for v in _groups(session).values()) == 4

    # Rescan after deleting a file marks it missing.
    os.remove(os.path.join(library, "Riley", "unique.jpg"))
    assert scan(session).missing == 1


def test_live_photo_clip_saved_with_jpg_extension(session, users, library):
    """Some iPhone exports name the Live Photo motion clip IMG_1234.JPG: it must be handled as a video."""
    import subprocess

    from PIL import Image

    folder = os.path.join(library, "Sam", "Iphone XR photos")
    os.makedirs(folder)
    Image.new("RGB", (400, 300), (30, 90, 160)).save(os.path.join(folder, "IMG_2705.HEIC"), format="HEIF")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30", "-t", "2",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-f", "mov", os.path.join(folder, "IMG_2705.JPG")],
                   check=True)
    res = scan(session)
    process_files(session, res.to_process)
    clip = session.scalar(select(File).where(File.path == "Sam/Iphone XR photos/IMG_2705.JPG"))
    assert clip.process_state == "done" and clip.kind == "video" and clip.codec == "h264"
    assert run_grouping(session)["live_pairs"] == 1  # paired with its still, not left as a broken "photo"
    still = session.scalar(select(File).where(File.path == "Sam/Iphone XR photos/IMG_2705.HEIC"))
    session.refresh(clip)
    assert clip.role == "live_video" and clip.item_id == still.item_id
