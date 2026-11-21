"""Per-file processing: metadata, hashes, thumbnails. Runs in worker processes (NAS or PC)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import File
from ..paths import abs_path
from . import hashing, thumbs, video
from .metadata import parse_exif, run_exiftool

log = logging.getLogger(__name__)


def process_files(session: Session, file_ids: list[int]) -> dict[str, int]:
    files = list(session.scalars(select(File).where(File.id.in_(file_ids))))
    stats = {"done": 0, "error": 0}
    if not files:
        return stats

    paths = {f.id: abs_path(f.path) for f in files}
    try:
        tags_by_path = run_exiftool(list(paths.values()))
    except Exception as exc:  # exiftool missing / timeout: continue with hashing only
        log.warning("exiftool batch failed: %s", exc)
        tags_by_path = {}

    for f in files:
        full = paths[f.id]
        try:
            tags = tags_by_path.get(full)
            if tags:
                for key, value in parse_exif(tags).items():
                    if value is not None or key in ("gps_lat", "gps_lon", "taken_at", "live_photo_id"):
                        setattr(f, key, value)
            if f.kind == "image":
                _process_image(f, full)
            else:
                _process_video(f, full)
            f.process_state = "done"
            f.process_error = None
            stats["done"] += 1
        except FileNotFoundError:
            f.status = "missing"
            f.process_state = "error"
            f.process_error = "file not found"
            stats["error"] += 1
        except Exception as exc:
            log.warning("processing %s failed: %s", f.path, exc)
            f.process_state = "error"
            f.process_error = str(exc)[:1000]
            stats["error"] += 1
        session.commit()
    return stats


def _process_image(f: File, full: str) -> None:
    with open(full, "rb") as fh:
        data = fh.read()
    f.sha256 = hashing.sha256_bytes(data)
    img = hashing.open_image_bytes(data, f.ext, full)
    f.width, f.height = img.size
    f.phash = hashing.phash(img)
    f.dhash = hashing.dhash(img)
    thumbs.make_thumbs(img, f.id)
    thumbs.clear_derived(f.id)  # a changed file must not keep serving its old full-size rendition
    f.thumb_ready = True


def _process_video(f: File, full: str) -> None:
    thumbs.clear_derived(f.id)
    info = video.probe(full)
    f.duration = info.duration or f.duration
    f.width = info.width or f.width
    f.height = info.height or f.height
    f.codec = info.codec
    sig, poster = video.signature(full, f.duration)
    f.video_sig = sig or None
    f.phash = sig[len(sig) // 2] if sig else None
    if poster is not None:
        thumbs.make_thumbs(poster, f.id)
        f.thumb_ready = True


def hash_files(session: Session, file_ids: list[int]) -> int:
    """Compute SHA-256 for files that collide on size (videos are not hashed during processing)."""
    n = 0
    for f in session.scalars(select(File).where(File.id.in_(file_ids))):
        if f.sha256:
            continue
        try:
            f.sha256 = hashing.sha256_file(abs_path(f.path))
            n += 1
        except FileNotFoundError:
            f.status = "missing"
        except OSError as exc:
            log.warning("hashing %s failed: %s", f.path, exc)
            f.process_state = "error"  # excluded from future SHA passes (prevents a regroup loop)
            f.process_error = str(exc)[:1000]
        session.commit()
    return n
