"""Content and perceptual hashing (pHash / dHash) implemented with numpy + Pillow."""
from __future__ import annotations

import hashlib
import io
import subprocess
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps

from ..config import config
from ..paths import RAW_EXTS

try:  # HEIC/HEIF support
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 250_000_000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def hamming(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 64
    return (int(a, 16) ^ int(b, 16)).bit_count()


@lru_cache(maxsize=1)
def _dct_matrix(n: int = 32) -> np.ndarray:
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m[0, :] *= 1 / np.sqrt(2)
    return m * np.sqrt(2 / n)


def _bits_to_hex(bits: np.ndarray) -> str:
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"


def phash(img: Image.Image) -> str:
    """64-bit DCT perceptual hash (robust to re-encoding, resizing, mild colour changes)."""
    gray = img.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float64)
    d = _dct_matrix(32)
    dct = d @ pixels @ d.T
    low = dct[:8, :8]
    return _bits_to_hex(low > np.median(low))


def dhash(img: Image.Image) -> str:
    gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = np.asarray(gray, dtype=np.int16)
    return _bits_to_hex(px[:, 1:] > px[:, :-1])


def open_image_bytes(data: bytes, ext: str, path: str | None = None) -> Image.Image:
    """Decode an image (incl. HEIC) with EXIF orientation applied. RAW files use their embedded preview."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        if ext in RAW_EXTS and path:
            preview = extract_raw_preview(path)
            if preview:
                img = Image.open(io.BytesIO(preview))
                img.load()
            else:
                raise
        else:
            raise
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def extract_raw_preview(path: str) -> bytes | None:
    for tag in ("-JpgFromRaw", "-PreviewImage"):
        proc = subprocess.run([config.exiftool, "-b", tag, path], capture_output=True, timeout=120, check=False)
        if proc.returncode == 0 and len(proc.stdout) > 1000:
            return proc.stdout
    return None
