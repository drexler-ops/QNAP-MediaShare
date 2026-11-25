"""Apple Photos / iPhoto XMP sidecars ("Export Unmodified Originals" with "Export IPTC as XMP").

Photos exports the untouched original (with the camera's own EXIF) plus `IMG_1234.xmp` holding what
the owner set in Photos: corrected date/time, added or moved location, title, caption, keywords. Those
are deliberate corrections (wrong camera clock, scanned prints, a location added afterwards), so unlike
Google Takeout's JSON they *override* the file's own date and location. Title, caption and keywords
are shown in the photo's details.

Matching: `IMG_1234.xmp` (Photos), or `IMG_1234.HEIC.xmp` (some other tools). A Live Photo's
`IMG_1234.MOV` shares `IMG_1234.xmp` with its photo.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Any

from ..config import config

log = logging.getLogger(__name__)

MARK = "Apple Photos"  # key in File.exif: the sidecar was applied (and what it changed)
TAGS = [
    "-XMP:DateTimeOriginal", "-XMP:DateCreated", "-XMP:CreateDate",
    "-XMP:GPSLatitude", "-XMP:GPSLongitude",
    "-XMP:Title", "-XMP:Description", "-XMP:Subject",
]
# Wall-clock date and time, any timezone offset ignored (dates in this app are local, like EXIF).
_DATE = re.compile(r"^(\d{4})[:-](\d{2})[:-](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
MIN_YEAR = 1826  # deliberate dates can be old (scanned prints); only reject impossible ones


def find_xmp(path: str, listing: set[str]) -> str | None:
    """The .xmp beside a media file; `listing` holds the folder's sidecar names."""
    folder, name = os.path.split(path)
    lower = {n.lower(): n for n in listing if n.lower().endswith(".xmp")}
    if not lower:
        return None
    stem = name.rsplit(".", 1)[0]
    for cand in (f"{stem}.xmp", f"{name}.xmp"):
        if cand.lower() in lower:
            return os.path.join(folder, lower[cand.lower()])
    return None


def parse_xmp_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    m = _DATE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) if g else 0 for g in m.groups())
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    return dt if dt.year >= MIN_YEAR else None


def _text(value: Any, limit: int = 500) -> str | None:
    if isinstance(value, list):
        value = ", ".join(str(v).strip() for v in value if str(v).strip())
    if value is None:
        return None
    s = str(value).strip()
    return s[:limit] or None


def parse_xmp(tags: dict[str, Any]) -> dict[str, Any]:
    """Map exiftool's reading of an .xmp to {"taken_at", "gps", "title", "description", "keywords"}."""
    taken = None
    for key in ("DateTimeOriginal", "DateCreated", "CreateDate"):
        taken = parse_xmp_date(tags.get(key))
        if taken:
            break
    gps = None
    try:
        lat, lon = float(tags["GPSLatitude"]), float(tags["GPSLongitude"])
        if not (lat == 0 and lon == 0) and abs(lat) <= 90 and abs(lon) <= 180:
            gps = (lat, lon)
    except (KeyError, TypeError, ValueError):
        pass
    return {
        "taken_at": taken,
        "gps": gps,
        "title": _text(tags.get("Title"), 200),
        "description": _text(tags.get("Description")),
        "keywords": _text(tags.get("Subject")),
    }


def read_xmp_batch(paths: list[str]) -> dict[str, dict[str, Any]]:
    """{xmp path: parsed} for many sidecars in one exiftool run."""
    paths = sorted(set(paths))
    if not paths:
        return {}
    cmd = [config.exiftool, "-json", "-n", "-q", "-q", "-charset", "filename=utf8", *TAGS, "-@", "-"]
    try:
        proc = subprocess.run(cmd, input="\n".join(paths).encode("utf-8"), capture_output=True, timeout=600, check=False)
        rows = json.loads(proc.stdout.decode("utf-8", errors="replace") or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        log.warning("reading .xmp sidecars failed: %s", exc)
        return {}
    return {r["SourceFile"]: parse_xmp(r) for r in rows if r.get("SourceFile")}


def apply_xmp(f, meta: dict[str, Any] | None, xmp_name: str) -> list[str]:
    """Apply an Apple sidecar to a processed File. Returns what changed ("date", "location")."""
    if not meta:
        return []
    changed: list[str] = []
    if meta.get("taken_at") and meta["taken_at"] != f.taken_at:
        changed.append("date corrected" if f.taken_at else "date")
        f.taken_at = meta["taken_at"]
    gps = meta.get("gps")
    if gps and (f.gps_lat is None or abs(f.gps_lat - gps[0]) > 1e-5 or abs(f.gps_lon - gps[1]) > 1e-5):
        changed.append("location corrected" if f.gps_lat is not None else "location")
        f.gps_lat, f.gps_lon = gps
    exif = dict(f.exif or {})
    for key, label in (("title", "Title"), ("description", "Description"), ("keywords", "Keywords")):
        if meta.get(key):
            exif[label] = meta[key]
    # Always mark it applied (so scans don't re-read it), and say what it changed.
    exif[MARK] = f"{' and '.join(changed)} from {xmp_name}" if changed else f"read {xmp_name}"
    f.exif = exif
    return changed
