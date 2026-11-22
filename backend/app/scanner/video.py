"""Video probing, frame signatures and browser-friendly previews via ffmpeg."""
from __future__ import annotations

import io
import json
import os
import subprocess
import threading
from dataclasses import dataclass

from PIL import Image

from ..config import config
from .hashing import phash

SIGNATURE_POINTS = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95]


@dataclass
class VideoInfo:
    duration: float | None
    width: int | None
    height: int | None
    codec: str | None


def probe(path: str) -> VideoInfo:
    proc = subprocess.run(
        [config.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, timeout=120, check=False,
    )
    data = json.loads(proc.stdout or b"{}")
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    width, height = stream.get("width"), stream.get("height")
    rotation = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"]))
    if str((stream.get("tags") or {}).get("rotate", "0")) in ("90", "270"):
        rotation = 90
    if rotation in (90, 270) and width and height:
        width, height = height, width
    duration = data.get("format", {}).get("duration") or stream.get("duration")
    return VideoInfo(
        duration=float(duration) if duration else None,
        width=width,
        height=height,
        codec=stream.get("codec_name"),
    )


def frame_at(path: str, seconds: float, width: int = 640) -> Image.Image | None:
    proc = subprocess.run(
        [
            config.ffmpeg, "-v", "error", "-ss", f"{max(seconds, 0):.3f}", "-i", path,
            "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-",
        ],
        capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    img = Image.open(io.BytesIO(proc.stdout))
    img.load()
    return img.convert("RGB")


def signature(path: str, duration: float | None) -> tuple[list[str], Image.Image | None]:
    """pHash of frames at fixed percentages of the duration, plus a poster frame for thumbnails."""
    if not duration or duration <= 0:
        poster = frame_at(path, 0)
        return ([phash(poster)] if poster else []), poster
    hashes: list[str] = []
    poster: Image.Image | None = None
    for pct in SIGNATURE_POINTS:
        frame = frame_at(path, duration * pct, width=320 if pct != 0.15 else 1280)
        if frame is None:
            continue
        if pct == 0.15:
            poster = frame
        hashes.append(phash(frame))
    return hashes, poster


def signature_distance(a: list[str] | None, b: list[str] | None) -> float:
    from .hashing import hamming

    if not a or not b:
        return 64.0
    n = min(len(a), len(b))
    if n == 0:
        return 64.0
    return sum(hamming(a[i], b[i]) for i in range(n)) / n


def make_preview(path: str, out_path: str, max_seconds: int | None = 120, short_side: int = 720,
                 duration: float | None = None) -> None:
    """H.264/AAC MP4 every browser can play (HEVC .MOV files don't play on Windows Chrome).

    short_side caps the smaller dimension (720 -> 1280x720 landscape / 720x1280 portrait); never upscales.
    max_seconds=None converts the whole video.
    """
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = f"{out_path}.{os.getpid()}-{threading.get_ident()}.tmp.mp4"  # unique: web + workers may race
    n = short_side
    scale = f"scale='if(gt(iw,ih),-2,min({n},iw))':'if(gt(iw,ih),min({n},ih),-2)'"
    limit = ["-t", str(max_seconds)] if max_seconds else []
    crf = "26" if n <= 720 else "24"
    # Long 4K sources decode slowly on the NAS: allow ~6x the video's length.
    timeout = max(1800, int((duration or 0) * 6))
    proc = subprocess.run(
        [
            # Thread caps keep a 4K decode + encode around 500 MB (uncapped it scales with core count).
            config.ffmpeg, "-v", "error", "-y", "-threads", "2", "-i", path, *limit,
            "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
            "-threads", "3", "-x264-params", "rc-lookahead=20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", tmp,
        ],
        capture_output=True, timeout=timeout, check=False,
    )
    if proc.returncode != 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(proc.stderr.decode(errors="replace")[-500:])
    os.replace(tmp, out_path)
