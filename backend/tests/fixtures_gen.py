"""Build a small realistic fixture library (needs ffmpeg for the videos).

    python -m tests.fixtures_gen /path/to/library

Layout (owners = top-level folders):
  Jamie/IMG_0001.HEIC                 original: HEIC + EXIF + GPS          ─┐
  Sam/IMG_0001.HEIC                   byte-identical copy                   │ one "same photo" group
  Sam/Exports/IMG_0001.jpg            JPG export, no EXIF                   │ (keeper: a HEIC original)
  Alex/WhatsApp/IMG-20240704-WA0003.jpg  resized, recompressed            ─┘
  Alex/Screens/screenshot.png + Riley/screenshot copy.png   exact PNG copies
  Riley/Burst/IMG_1001..1003.JPG     3 shots taken seconds apart (similar, not identical)
  Jamie/Live/IMG_2001.HEIC + .MOV     a Live Photo pair
  Jamie/Videos/clip.mp4 + Sam/Videos/clip_small.mp4   same clip, re-encoded
  Riley/unique.jpg                   no duplicates
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass


def scene(seed: int, size=(2400, 1800), shift: int = 0, extra: int = 0) -> Image.Image:
    rnd = np.random.default_rng(seed)
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([(xx / w) * 120 + 60, (yy / h) * 100 + 80, np.full_like(xx, 150.0)], axis=-1).astype(np.uint8)
    img = Image.fromarray(base, "RGB")
    d = ImageDraw.Draw(img)
    for _ in range(14):
        x, y = int(rnd.integers(0, w - 400)), int(rnd.integers(0, h - 400))
        r = int(rnd.integers(120, 420))
        color = tuple(int(c) for c in rnd.integers(0, 255, 3))
        d.ellipse([x + shift, y, x + shift + r, y + r], fill=color)
    erng = np.random.default_rng(seed * 1000 + extra)
    for _ in range(extra):
        x, y = int(erng.integers(0, w - 500)), int(erng.integers(0, h - 500))
        color = tuple(int(c) for c in erng.integers(0, 255, 3))
        d.rectangle([x, y, x + 450, y + 350], fill=color)
    return img


def exif_for(dt: str, gps: bool = True) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Apple"
    exif[0x0110] = "iPhone 15 Pro"
    # Sub-IFDs must be assigned as dicts; edits via get_ifd() are not serialised by Pillow.
    exif[0x8769] = {0x9003: dt, 0x829A: 1 / 250}  # DateTimeOriginal, ExposureTime
    if gps:
        exif[0x8825] = {1: "N", 2: (41.0, 52.0, 48.0), 3: "W", 4: (87.0, 37.0, 12.0)}
    return exif.tobytes()


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def build(root: str) -> None:
    def p(rel: str) -> str:
        full = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    original = scene(1)
    original.save(p("Jamie/IMG_0001.HEIC"), format="HEIF", quality=90, exif=exif_for("2024:07:04 12:00:00"))
    shutil.copyfile(p("Jamie/IMG_0001.HEIC"), p("Sam/IMG_0001.HEIC"))
    original.save(p("Sam/Exports/IMG_0001.jpg"), "JPEG", quality=92)
    original.resize((1600, 1200)).save(p("Alex/WhatsApp/IMG-20240704-WA0003.jpg"), "JPEG", quality=60)

    shot = scene(7, size=(1280, 800))
    shot.save(p("Alex/Screens/screenshot.png"))
    shutil.copyfile(p("Alex/Screens/screenshot.png"), p("Riley/screenshot copy.png"))

    for i, sec in enumerate(("00", "01", "03")):
        burst = scene(3, extra=i)
        burst.save(p(f"Riley/Burst/IMG_100{i + 1}.JPG"), "JPEG", quality=90,
                   exif=exif_for(f"2024:08:01 10:00:{sec}", gps=False))

    scene(11).save(p("Jamie/Live/IMG_2001.HEIC"), format="HEIF", exif=exif_for("2024:09:09 09:09:09"))
    ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x480:rate=30", "-t", "2", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-f", "mov", p("Jamie/Live/IMG_2001.MOV"))

    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30", "-t", "6", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-crf", "20", p("Jamie/Videos/clip.mp4"))
    ffmpeg("-i", p("Jamie/Videos/clip.mp4"), "-vf", "scale=320:240", "-c:v", "libx264", "-crf", "34",
           p("Sam/Videos/clip_small.mp4"))

    scene(99).save(p("Riley/unique.jpg"), "JPEG", quality=88, exif=exif_for("2023:01:01 08:00:00", gps=False))


if __name__ == "__main__":
    build(sys.argv[1])
