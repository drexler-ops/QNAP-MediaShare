import io

import numpy as np
from PIL import Image, ImageDraw

from app.scanner.hashing import dhash, hamming, open_image_bytes, phash


def scene(seed: int, shift: int = 0) -> Image.Image:
    rnd = np.random.default_rng(seed)
    img = Image.new("RGB", (800, 600), (40, 90, 160))
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rnd.integers(0, 700), rnd.integers(0, 500)
        r = int(rnd.integers(20, 120))
        color = tuple(int(c) for c in rnd.integers(0, 255, 3))
        d.ellipse([x + shift, y, x + shift + r, y + r], fill=color)
    return img


def jpeg(img: Image.Image, quality=90, size=None) -> Image.Image:
    if size:
        img = img.resize(size)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return open_image_bytes(buf.getvalue(), "jpg")


def test_phash_survives_reencode_and_resize():
    original = scene(1)
    copy = jpeg(original, quality=40, size=(400, 300))
    assert hamming(phash(original), phash(copy)) <= 4
    assert hamming(dhash(original), dhash(copy)) <= 8


def test_different_photos_are_far_apart():
    assert hamming(phash(scene(1)), phash(scene(2))) > 16


def test_exif_orientation_applied():
    img = scene(3)
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW
    img.save(buf, "JPEG", exif=exif)
    opened = open_image_bytes(buf.getvalue(), "jpg")
    assert opened.size == (600, 800)
