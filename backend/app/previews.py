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

BROWSER_SAFE_VIDEO = {"h264"}
LIVE = ("active", "pending_delete")


def is_mp4_container(path: str) -> bool:
    """MP4/MOV/3GP family (ISO BMFF), whatever the extension says (some Live Photo clips are named .JPG)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(12)
    except OSError:
        return False
    return len(head) >= 12 and head[4:8] == b"ftyp"


def direct_playable(f) -> bool:
    """Browsers play H.264 in an MP4/MOV container as-is: no conversion needed."""
    if f.kind != "video" or f.codec not in BROWSER_SAFE_VIDEO:
        return False
    return f.ext in ("mp4", "m4v", "mov") or is_mp4_container(abs_path(f.path))


def hevc_original_ok(f) -> bool:
    """HEVC in an MP4/MOV container: devices with HEVC support (iPhone, Mac, most Android) can play the original."""
    return f.kind == "video" and f.codec == "hevc" and is_mp4_container(abs_path(f.path))


def needs_preview(f) -> bool:
    return (
        f.kind == "video" and f.status in LIVE and f.process_state == "done"
        and not direct_playable(f) and not os.path.isfile(preview_path(f.id))
    )


def convert_now(f) -> None:
    """Background-job path (worker / helper PC). Same output as the on-demand path."""
    make_preview(abs_path(f.path), preview_path(f.id), max_seconds=PREVIEW_SECONDS)


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


def request(file_id: int, rel_path: str, retry: bool = False, start: bool = True) -> dict:
    """Status of a preview; starts converting on first ask (or on retry after a failure).

    start=False only reports (the viewer asks first, then may play an HEVC original instead).
    """
    if os.path.isfile(preview_path(file_id)):
        return {"status": "ready"}
    with _lock:
        st = _state.get(file_id)
        if not start and not (st and st[0] in ("queued", "running")):
            return {"status": "missing"}
        if st and st[0] == "failed" and not retry:
            return {"status": "failed", "error": st[1]}
        if st and st[0] in ("queued", "running"):
            ahead = sum(1 for k, (s, _) in _state.items() if k != file_id and s in ("queued", "running")) if st[0] == "queued" else 0
            return {"status": "processing", "stage": st[0], "ahead": ahead}
        _state[file_id] = ("queued", None)
    threading.Thread(target=_convert, args=(file_id, rel_path), daemon=True, name=f"preview-{file_id}").start()
    return {"status": "processing", "stage": "queued", "ahead": 0}
