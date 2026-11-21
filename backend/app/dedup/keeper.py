"""Decide which copy in a duplicate group to suggest keeping.

Priority (per the requirements): EXIF/GPS presence, then resolution, then original format.
"""
from __future__ import annotations

import posixpath
import re
from datetime import datetime
from typing import Any, Protocol, Sequence

FORMAT_SCORES = {
    # images
    "dng": 1.0, "cr2": 1.0, "cr3": 1.0, "nef": 1.0, "arw": 1.0, "orf": 1.0, "rw2": 1.0, "raf": 1.0,
    "heic": 0.9, "heif": 0.9, "tif": 0.7, "tiff": 0.7, "jpg": 0.6, "jpeg": 0.6, "png": 0.5,
    "webp": 0.3, "bmp": 0.3, "gif": 0.2,
    # videos
    "mov": 0.9, "mp4": 0.7, "m4v": 0.6, "mts": 0.8, "m2ts": 0.8, "mkv": 0.5, "avi": 0.4,
    "3gp": 0.3, "wmv": 0.3, "webm": 0.4,
}
FORMAT_LABELS = {"heic": "HEIC (iPhone original)", "heif": "HEIF", "mov": "MOV (iPhone original)"}

# File names that suggest a re-shared / exported copy rather than a camera original.
COPY_NAME_RE = re.compile(
    r"(-WA\d{3,}|^received_|^FB_IMG|^IMG-\d{8}-WA|^signal-|^telegram|^Screenshot|^photo_\d|_edited$|"
    r"\(\d+\)$| - Copy$|^image\d*$|^export)",
    re.IGNORECASE,
)


class Scorable(Protocol):
    id: int
    path: str
    ext: str
    size: int
    exif_score: int
    width: int | None
    height: int | None
    gps_lat: float | None
    gps_lon: float | None
    first_seen_at: datetime | None


def format_score(path: str, ext: str) -> tuple[float, bool]:
    base = FORMAT_SCORES.get(ext.lower(), 0.4)
    stem = posixpath.splitext(posixpath.basename(path))[0]
    looks_copied = bool(COPY_NAME_RE.search(stem))
    return (base * 0.4 if looks_copied else base), looks_copied


def score_files(files: Sequence[Scorable], weights: dict[str, float]) -> dict[int, dict[str, Any]]:
    """Return {file.id: {"score": float, "breakdown": {...}, "notes": [...]}}."""
    max_px = max(((f.width or 0) * (f.height or 0) for f in files), default=0) or 1
    max_size = max((f.size or 0 for f in files), default=0) or 1
    out: dict[int, dict[str, Any]] = {}
    for f in files:
        px = (f.width or 0) * (f.height or 0)
        has_gps = f.gps_lat is not None and f.gps_lon is not None
        fmt, copied = format_score(f.path, f.ext)
        parts = {
            "exif": weights.get("exif", 0) * min(f.exif_score, 9) / 9,
            "gps": weights.get("gps", 0) * (1.0 if has_gps else 0.0),
            "resolution": weights.get("resolution", 0) * px / max_px,
            "format": weights.get("format", 0) * fmt,
            "size": weights.get("size", 0) * (f.size or 0) / max_size,
        }
        notes: list[str] = []
        if f.exif_score >= 5:
            notes.append("Rich EXIF metadata")
        elif f.exif_score == 0:
            notes.append("No EXIF metadata")
        if has_gps:
            notes.append("Has GPS location")
        if px and px == max_px:
            notes.append(f"Highest resolution ({f.width}×{f.height})")
        if fmt >= 0.9:
            notes.append(f"Original format: {FORMAT_LABELS.get(f.ext.lower(), f.ext.upper())}")
        if copied:
            notes.append("Filename looks like a shared/exported copy")
        out[f.id] = {
            "score": round(sum(parts.values()), 3),
            "breakdown": {k: round(v, 2) for k, v in parts.items()},
            "notes": notes,
        }
    return out


def rank(files: Sequence[Scorable], weights: dict[str, float]) -> list[tuple[Scorable, dict[str, Any]]]:
    """Files ordered best-first. Ties: earliest upload, then lowest id."""
    scores = score_files(files, weights)
    return sorted(
        ((f, scores[f.id]) for f in files),
        key=lambda pair: (
            -pair[1]["score"],
            pair[0].first_seen_at or datetime.max,
            pair[0].id,
        ),
    )
