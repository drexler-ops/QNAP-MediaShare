"""Google Takeout sidecars: `<photo>.supplemental-metadata.json` next to each exported photo/video.

Takeout keeps the date and place Google Photos knew for each item in a JSON file beside it. Exported
copies often lost that from the file itself (screenshots, WhatsApp images, scans, older uploads), so
when a photo's own EXIF has no date or no GPS we fill the gap from the sidecar. The photo's own
metadata always wins; the sidecar never overrides it.

Takeout naming quirks handled:
    IMG_1.jpg              -> IMG_1.jpg.supplemental-metadata.json   (current exports)
                           -> IMG_1.jpg.json                         (older exports)
    IMG_1(1).jpg           -> IMG_1.jpg.supplemental-metadata(1).json / IMG_1.jpg(1).json
    IMG_1-edited.jpg       -> the original's sidecar
    IMG_1.MP4 (Live Photo) -> IMG_1.HEIC's sidecar (Takeout writes one per photo, not per motion clip)
    long names             -> the JSON file name is cut short (51 chars), e.g. "...supplemental-metad.json"
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

SUFFIX = ".supplemental-metadata"
_DUP = re.compile(r"^(?P<base>.*)\((?P<n>\d+)\)$")
_EDITED = ("-edited", "-bearbeitet", "-modifié", "-editado", "-modificato", "-bewerkt")
MIN_TRUNCATED = 30  # don't prefix-match very short names (ambiguous)
LIVE_VIDEO_EXTS = {"mp4", "mov"}
LIVE_PHOTO_EXTS = {"heic", "heif", "jpg", "jpeg"}


def _candidates(name: str) -> list[str]:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    full = f"{stem}.{ext}" if ext else stem
    names = [f"{full}{SUFFIX}.json", f"{full}.json"]
    m = _DUP.match(stem)
    if m:  # IMG_1(1).jpg -> IMG_1.jpg.supplemental-metadata(1).json
        orig = f"{m['base']}.{ext}" if ext else m["base"]
        names += [f"{orig}{SUFFIX}({m['n']}).json", f"{orig}({m['n']}).json"]
    for tag in _EDITED:
        if stem.lower().endswith(tag):
            orig = stem[: -len(tag)]
            names += _candidates(f"{orig}.{ext}" if ext else orig)
            break
    return names


def json_listing(folder: str) -> set[str]:
    try:
        return {e for e in os.listdir(folder) if e.lower().endswith(".json")}
    except OSError:
        return set()


def find_sidecar(path: str, listing: set[str] | None = None) -> str | None:
    """Path of the Takeout JSON for a media file, or None. `listing`: the folder's .json names (cache)."""
    folder, name = os.path.split(path)
    if listing is None:
        listing = json_listing(folder)
    if not listing:
        return None
    for cand in _candidates(name):
        if cand in listing:
            return os.path.join(folder, cand)
    stem, _, ext = name.rpartition(".")
    if stem and ext.lower() in LIVE_VIDEO_EXTS:
        # Live Photo motion clip (IMG_1.MP4 beside IMG_1.HEIC): Takeout only writes the photo's sidecar.
        m = _DUP.match(stem)
        base, n = (m["base"], m["n"]) if m else (stem, None)
        for j in sorted(listing):
            if not j.startswith(base + "."):
                continue
            rest = j[len(base) + 1:]  # e.g. "HEIC.supplemental-metadata(1).json"
            if rest.split(".", 1)[0].lower() not in LIVE_PHOTO_EXTS:
                continue
            numbered = f"({n})" in rest if n else "(" not in rest
            if numbered:
                return os.path.join(folder, j)
    # Truncated JSON names: the sidecar's name (minus .json and any "(n)") is a prefix of the full one.
    wanted = [c[: -len(".json")] for c in _candidates(name) if SUFFIX in c]
    for j in listing:
        key = j[:-5]
        n = _DUP.match(key)
        bare = n["base"] if n else key
        if len(bare) < MIN_TRUNCATED:
            continue
        for w in wanted:
            wn = _DUP.match(w)
            if wn and n and wn["n"] != n["n"]:
                continue
            wbare = wn["base"] if wn else w
            if wbare.startswith(bare) and wbare != bare:
                return os.path.join(folder, j)
    return None


def _ts(block: Any) -> datetime | None:
    try:
        ts = int(block["timestamp"])
    except (TypeError, KeyError, ValueError):
        return None
    if ts <= 0:
        return None
    # Takeout times are UTC; dates in this app are local wall-clock time (like EXIF): convert with TZ.
    return datetime.fromtimestamp(ts).replace(microsecond=0)


def _geo(block: Any) -> tuple[float, float] | None:
    try:
        lat, lon = float(block["latitude"]), float(block["longitude"])
    except (TypeError, KeyError, ValueError):
        return None
    if (lat == 0 and lon == 0) or abs(lat) > 90 or abs(lon) > 180:
        return None  # Takeout writes 0,0 when it has no location
    return lat, lon


def read_sidecar(json_path: str) -> dict[str, Any]:
    """{"taken_at", "gps"(lat, lon), "description"} from a Takeout JSON; missing values are None."""
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    gps = _geo(data.get("geoDataExif")) or _geo(data.get("geoData"))
    desc = data.get("description")
    return {
        "taken_at": _ts(data.get("photoTakenTime")),
        "gps": gps,
        "description": desc.strip()[:500] if isinstance(desc, str) and desc.strip() else None,
    }


def apply_sidecar(f, full_path: str, listing: set[str] | None = None) -> list[str]:
    """Fill a processed File's missing date / location from its Takeout JSON. Returns what was filled."""
    if f.taken_at is not None and f.gps_lat is not None:
        return []
    side = find_sidecar(full_path, listing)
    if not side:
        return []
    meta = read_sidecar(side)
    filled: list[str] = []
    if f.taken_at is None and meta.get("taken_at"):
        f.taken_at = meta["taken_at"]
        filled.append("date")
    if f.gps_lat is None and meta.get("gps"):
        f.gps_lat, f.gps_lon = meta["gps"]
        filled.append("location")
    if filled:
        exif = dict(f.exif or {})
        exif["Google Takeout"] = f"{' and '.join(filled)} from {os.path.basename(side)}"
        if meta.get("description"):
            exif.setdefault("Description", meta["description"])
        f.exif = exif
    return filled


def backfill(session) -> dict[str, int]:
    """Apply sidecars to already-processed files still missing a date or location (e.g. the JSON
    files were copied in after the photos were indexed). Runs after every scan, so it stays cheap:
    reads only paths, lists each folder once, and loads full rows only in folders that hold .json."""
    from collections import defaultdict

    from sqlalchemy import or_, select

    from ..models import File
    from ..paths import abs_path

    rows = session.execute(select(File.id, File.path).where(
        File.status.in_(("active", "pending_delete")), File.process_state == "done",
        or_(File.taken_at.is_(None), File.gps_lat.is_(None)),
    )).all()
    by_folder: dict[str, list[int]] = defaultdict(list)
    for fid, path in rows:
        by_folder[os.path.dirname(abs_path(path))].append(fid)
    stats = {"checked": 0, "dates": 0, "locations": 0}
    for folder, ids in by_folder.items():
        listing = json_listing(folder)
        if not listing:
            continue
        for f in session.scalars(select(File).where(File.id.in_(ids))):
            stats["checked"] += 1
            filled = apply_sidecar(f, abs_path(f.path), listing)
            stats["dates"] += "date" in filled
            stats["locations"] += "location" in filled
        session.commit()
    return stats
