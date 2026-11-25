"""Per-file processing: metadata, hashes, thumbnails. Runs in worker processes (NAS or PC)."""
from __future__ import annotations

import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import File
from ..paths import abs_path
from . import hashing, thumbs, video
from .metadata import parse_exif, run_exiftool

log = logging.getLogger(__name__)

# ISO-BMFF "ftyp" brands: still-image containers vs. video containers.
_IMAGE_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}
_VIDEO_BRANDS = {b"qt  ", b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"M4V ", b"M4VP",
                 b"avc1", b"3gp4", b"3gp5", b"3gp6", b"3g2a", b"dash", b"f4v ", b"MSNV", b"XAVC"}


class NotMedia(Exception):
    pass


def sniff_kind(head: bytes) -> str | None:
    """What a file really is, from its first bytes: "image", "video", "not_media", or None (unknown).

    File extensions lie: some iPhone export tools save Live Photo motion clips as IMG_1234.JPG.
    """
    if len(head) < 12:
        return None
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in _IMAGE_BRANDS:
            return "image"
        if brand in _VIDEO_BRANDS:
            return "video"
        return None
    if head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if head[:4] in (b"GIF8", b"II*\x00", b"MM\x00*") or head[:2] == b"BM":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if (head[:4] == b"RIFF" and head[8:12] == b"AVI ") or head[:4] == b"\x1a\x45\xdf\xa3":
        return "video"
    stripped = head.lstrip()
    if stripped[:1] in (b"<", b"{"):
        return "not_media"
    return None


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

    from .sidecar import apply_all, sidecar_listing
    from .xmp import find_xmp, read_xmp_batch

    # Sidecars (Google Takeout .json, Apple Photos .xmp): each folder listed once, all .xmp read in one go.
    listings: dict[str, set[str]] = {}
    for full in paths.values():
        folder = os.path.dirname(full)
        if folder not in listings:
            listings[folder] = sidecar_listing(folder)
    xmps = [find_xmp(full, listings[os.path.dirname(full)]) for full in paths.values()]
    xmp_meta = read_xmp_batch([x for x in xmps if x])
    for f in files:
        full = paths[f.id]
        try:
            with open(full, "rb") as fh:
                actual = sniff_kind(fh.read(16))
            if actual == "not_media":
                raise NotMedia("Not a photo or video (looks like a text/XML file, e.g. an iPhone .AAE edit file)")
            if actual and actual != f.kind:
                log.info("%s: content is %s despite the .%s extension", f.path, actual, f.ext)
                f.kind = actual
            tags = tags_by_path.get(full)
            if tags:
                for key, value in parse_exif(tags).items():
                    if value is not None or key in ("gps_lat", "gps_lon", "taken_at", "live_photo_id"):
                        setattr(f, key, value)
            # Apple .xmp corrections override; Takeout .json fills what's still missing.
            apply_all(f, full, listings[os.path.dirname(full)], xmp_meta)
            if f.kind == "image":
                _process_image(f, full)
            else:
                _process_video(f, full)
            f.process_state = "done"
            f.process_error = None
            if f.face_state == "skip":
                f.face_state = "pending"  # readable now (fixed/replaced): eligible for face detection again
            stats["done"] += 1
        except FileNotFoundError:
            f.status = "missing"
            f.process_state = "error"
            f.process_error = "file not found"
            stats["error"] += 1
        except Exception as exc:
            log.warning("processing %s failed: %s", f.path, exc)
            msg = str(exc)
            if "cannot identify image file" in msg:
                msg = "Damaged or unsupported image file (the image data couldn't be read)"
            f.process_state = "error"
            f.process_error = msg[:1000]
            f.face_state = "skip"  # no preview to detect faces on; don't count it as waiting
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
