"""Thumbnail and video-preview storage (on a NAS share so every worker can write)."""
from __future__ import annotations

import os

from PIL import Image

from ..config import config

SIZES = {"s": 320, "l": 2048}


def _dir_for(file_id: int) -> str:
    return os.path.join(config.thumbs_root, f"{file_id // 1000:05d}")


def thumb_path(file_id: int, size: str) -> str:
    return os.path.join(_dir_for(file_id), f"{file_id}_{size}.webp")


def preview_path(file_id: int) -> str:
    return os.path.join(_dir_for(file_id), f"{file_id}_preview.mp4")


def make_thumbs(img: Image.Image, file_id: int) -> None:
    os.makedirs(_dir_for(file_id), exist_ok=True)
    for size, edge in SIZES.items():
        t = img.copy()
        t.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        if t.mode not in ("RGB", "L"):
            t = t.convert("RGB")
        tmp = thumb_path(file_id, size) + ".tmp"
        t.save(tmp, "WEBP", quality=82 if size == "l" else 75, method=4)
        os.replace(tmp, thumb_path(file_id, size))


def delete_thumbs(file_id: int) -> None:
    for p in (thumb_path(file_id, "s"), thumb_path(file_id, "l"), preview_path(file_id)):
        try:
            os.remove(p)
        except OSError:
            pass
