"""Helpers for converting between library-relative paths and real filesystem paths."""
from __future__ import annotations

import os
import posixpath

from .config import config

IMAGE_EXTS = {
    "jpg", "jpeg", "heic", "heif", "png", "gif", "webp", "tif", "tiff", "bmp",
    "dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf",
}
RAW_EXTS = {"dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf"}
VIDEO_EXTS = {"mov", "mp4", "m4v", "avi", "mkv", "3gp", "mts", "m2ts", "wmv", "webm"}


def ext_of(path: str) -> str:
    return posixpath.splitext(path)[1].lstrip(".").lower()


def kind_of(path: str) -> str | None:
    ext = ext_of(path)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def safe_join(root: str, rel: str) -> str:
    """Join a library-relative path onto root, refusing anything that escapes root."""
    rel = rel.replace("\\", "/").lstrip("/")
    full = os.path.normpath(os.path.join(root, *rel.split("/")))
    root_norm = os.path.normpath(root)
    if os.path.commonpath([full, root_norm]) != root_norm:
        raise ValueError(f"path escapes library root: {rel!r}")
    return full


def abs_path(rel: str, root: str | None = None) -> str:
    return safe_join(root or config.library_root, rel)


def owner_folder(rel: str) -> str:
    """Top-level folder of a library-relative path ('David/2024/IMG_1.HEIC' -> 'David')."""
    return rel.split("/", 1)[0] if "/" in rel else ""
