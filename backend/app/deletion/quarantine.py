"""Moving files into / out of the quarantine folder (same volume, so a move is an instant rename)."""
from __future__ import annotations

import os
import posixpath
import shutil
from datetime import datetime

from ..config import config
from ..paths import safe_join


def quarantine_rel(rel: str, when: datetime) -> str:
    return posixpath.join(config.quarantine_dirname, when.strftime("%Y-%m-%d"), rel)


def _unique(root: str, rel: str) -> str:
    candidate = rel
    stem, ext = posixpath.splitext(rel)
    n = 1
    while os.path.exists(safe_join(root, candidate)):
        candidate = f"{stem}__{n}{ext}"
        n += 1
    return candidate


def move_to_quarantine(rel: str, when: datetime, root: str | None = None) -> str:
    """Move library file `rel` into quarantine. Returns the new library-relative path."""
    root = root or config.library_root
    src = safe_join(root, rel)
    if not os.path.isfile(src):
        raise FileNotFoundError(rel)
    qrel = _unique(root, quarantine_rel(rel, when))
    dst = safe_join(root, qrel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    return qrel


def move_back(qrel: str, original_rel: str, root: str | None = None) -> None:
    root = root or config.library_root
    src = safe_join(root, qrel)
    dst = safe_join(root, original_rel)
    if not os.path.isfile(src):
        raise FileNotFoundError(qrel)
    if os.path.exists(dst):
        raise FileExistsError(f"a file already exists at {original_rel}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)


def purge_file(qrel: str, root: str | None = None) -> None:
    root = root or config.library_root
    path = safe_join(root, qrel)
    if not qrel.startswith(config.quarantine_dirname + "/"):
        raise ValueError("refusing to purge a file outside quarantine")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    # Tidy empty folders inside quarantine.
    qroot = safe_join(root, config.quarantine_dirname)
    parent = os.path.dirname(path)
    while parent.startswith(qroot) and parent != qroot:
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)
