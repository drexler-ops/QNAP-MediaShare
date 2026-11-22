"""Tunable app settings stored in the DB (editable from the Settings page)."""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

DEFAULTS: dict[str, Any] = {
    # Duplicate detection
    "reencode_threshold": 6,  # max pHash Hamming distance for "same photo, re-encoded"
    "burst_threshold": 14,  # max pHash distance for near-duplicates/bursts
    "burst_window_seconds": 10,  # max time between shots to be considered a burst
    "video_duration_tolerance": 2.0,  # seconds
    "video_frame_threshold": 10,  # mean per-frame pHash distance for video match
    # Keeper scoring weights (relative)
    "keeper_weights": {"exif": 40, "gps": 15, "resolution": 25, "format": 15, "size": 5},
    # Scanning
    "scan_interval_minutes": 30,
    "scans_paused": False,
    # Face recognition (InsightFace ArcFace cosine similarity)
    "faces_enabled": False,
    "face_auto_threshold": 0.5,  # recognised automatically as a named person
    "face_suggest_threshold": 0.38,  # offered for review ("Is this Sue?")
    "face_cluster_threshold": 0.5,  # grouped together as the same unnamed person
    "face_min_cluster": 3,  # smallest unnamed group worth showing
    # Quarantine
    "purge_days": 30,
    "auto_purge": False,
}


def get_settings(session: Session) -> dict[str, Any]:
    values = copy.deepcopy(DEFAULTS)
    for row in session.query(Setting).all():
        if row.key in values:
            if isinstance(values[row.key], dict) and isinstance(row.value, dict):
                values[row.key].update(row.value)
            else:
                values[row.key] = row.value
    return values


def update_settings(session: Session, changes: dict[str, Any]) -> dict[str, Any]:
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        expected = type(DEFAULTS[key])
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if expected is int and isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ValueError(f"{key} must be {expected.__name__}")
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    session.flush()
    return get_settings(session)
