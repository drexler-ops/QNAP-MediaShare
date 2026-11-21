"""Metadata extraction via exiftool (handles JPEG/HEIC/RAW/MOV/MP4 uniformly)."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime
from typing import Any

from ..config import config

log = logging.getLogger(__name__)

TAGS = [
    "-FileType", "-MIMEType", "-DateTimeOriginal", "-CreateDate", "-CreationDate", "-MediaCreateDate",
    "-Make", "-Model", "-LensModel", "-ExposureTime", "-FNumber", "-ISO", "-FocalLength",
    "-GPSLatitude", "-GPSLongitude", "-GPSCoordinates", "-ImageWidth", "-ImageHeight", "-Orientation",
    "-ContentIdentifier", "-Duration", "-Software",
]

_DATE_RE = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def run_exiftool(paths: list[str]) -> dict[str, dict[str, Any]]:
    """Return {absolute_path: tags} for a batch of files. Missing/unreadable files are omitted."""
    if not paths:
        return {}
    cmd = [
        config.exiftool, "-json", "-n", "-q", "-q",
        "-api", "QuickTimeUTC=1", "-api", "LargeFileSupport=1",
        "-charset", "filename=utf8", *TAGS, "-@", "-",
    ]
    proc = subprocess.run(
        cmd, input="\n".join(paths).encode("utf-8"), capture_output=True, timeout=600, check=False
    )
    if not proc.stdout.strip():
        if proc.returncode not in (0, 1):
            log.warning("exiftool failed (%s): %s", proc.returncode, proc.stderr.decode(errors="replace")[:500])
        return {}
    try:
        rows = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        log.warning("exiftool returned invalid JSON")
        return {}
    return {row.get("SourceFile"): row for row in rows if row.get("SourceFile")}


def parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        dt = datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None
    if dt.year < 1980:  # "0000:00:00" placeholders and broken camera clocks
        return None
    return dt


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().strip("\x00")
    return s[:128] or None


def _float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f


def parse_exif(tags: dict[str, Any]) -> dict[str, Any]:
    """Map raw exiftool output to `File` columns. Pure function (unit tested)."""
    dto = parse_date(tags.get("DateTimeOriginal"))
    taken = (
        dto
        or parse_date(tags.get("CreationDate"))
        or parse_date(tags.get("CreateDate"))
        or parse_date(tags.get("MediaCreateDate"))
    )

    lat, lon = _float(tags.get("GPSLatitude")), _float(tags.get("GPSLongitude"))
    if (lat is None or lon is None) and isinstance(tags.get("GPSCoordinates"), str):
        parts = tags["GPSCoordinates"].replace(",", " ").split()
        if len(parts) >= 2:
            lat, lon = _float(parts[0]), _float(parts[1])
    if lat is not None and lon is not None and (abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0)):
        lat = lon = None

    make, model = _clean_str(tags.get("Make")), _clean_str(tags.get("Model"))
    lens = _clean_str(tags.get("LensModel"))
    has_exposure = any(tags.get(k) is not None for k in ("ExposureTime", "FNumber", "ISO"))
    has_gps = lat is not None and lon is not None

    score = 0
    if dto:
        score += 3
    if make or model:
        score += 2
    if has_gps:
        score += 3
    if lens or has_exposure:
        score += 1

    width, height = tags.get("ImageWidth"), tags.get("ImageHeight")
    orientation = tags.get("Orientation")
    if isinstance(width, int) and isinstance(height, int) and orientation in (5, 6, 7, 8):
        width, height = height, width

    content_id = _clean_str(tags.get("ContentIdentifier"))

    display = {
        k: tags[k]
        for k in (
            "FileType", "DateTimeOriginal", "CreateDate", "Make", "Model", "LensModel", "ExposureTime",
            "FNumber", "ISO", "FocalLength", "Software", "Orientation",
        )
        if tags.get(k) not in (None, "")
    }

    return {
        "mime": _clean_str(tags.get("MIMEType")),
        "taken_at": taken,
        "make": make,
        "model": model,
        "lens": lens,
        "gps_lat": lat if has_gps else None,
        "gps_lon": lon if has_gps else None,
        "has_exif": bool(dto or make or model),
        "exif_score": score,
        "width": width if isinstance(width, int) else None,
        "height": height if isinstance(height, int) else None,
        "duration": _float(tags.get("Duration")),
        "live_photo_id": content_id[:64] if content_id else None,
        "exif": display or None,
    }
