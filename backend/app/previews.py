"""On-demand browser-playable video previews (HEVC, MJPEG, ... -> 720p H.264).

Made by the web process itself in a background thread instead of the job queue, so someone opening
a video never waits behind long background work (scans, face regrouping). One conversion at a time
keeps the NAS responsive; results are cached on disk, so each video is converted once.
"""
from __future__ import annotations

import logging
import os
import threading

from .paths import abs_path
from .scanner.thumbs import preview_path
from .scanner.video import make_preview

log = logging.getLogger(__name__)
PREVIEW_SECONDS = 120  # longer videos: the preview covers the start; Download gets the whole file

_lock = threading.Lock()
_slot = threading.Semaphore(1)
_state: dict[int, tuple[str, str | None]] = {}  # file_id -> ("queued" | "running" | "failed", error)


def _friendly(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    detail = text[-1][:200] if text else exc.__class__.__name__
    return f"This video couldn't be converted for playback ({detail}). Download it to watch it."


def _convert(file_id: int, rel_path: str) -> None:
    with _slot:
        with _lock:
            _state[file_id] = ("running", None)
        try:
            make_preview(abs_path(rel_path), preview_path(file_id), max_seconds=PREVIEW_SECONDS)
            with _lock:
                _state.pop(file_id, None)
        except Exception as exc:  # noqa: BLE001 - reported to the viewer
            log.warning("preview for file %s failed: %s", file_id, exc)
            with _lock:
                _state[file_id] = ("failed", _friendly(exc))


def request(file_id: int, rel_path: str, retry: bool = False) -> dict:
    """Status of a preview; starts converting on first ask (or on retry after a failure)."""
    if os.path.isfile(preview_path(file_id)):
        return {"status": "ready"}
    with _lock:
        st = _state.get(file_id)
        if st and st[0] == "failed" and not retry:
            return {"status": "failed", "error": st[1]}
        if st and st[0] in ("queued", "running"):
            ahead = sum(1 for k, (s, _) in _state.items() if k != file_id and s in ("queued", "running")) if st[0] == "queued" else 0
            return {"status": "processing", "stage": st[0], "ahead": ahead}
        _state[file_id] = ("queued", None)
    threading.Thread(target=_convert, args=(file_id, rel_path), daemon=True, name=f"preview-{file_id}").start()
    return {"status": "processing", "stage": "queued", "ahead": 0}
