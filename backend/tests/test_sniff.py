import io
import os

from PIL import Image

from app.models import File
from app.paths import abs_path
from app.scanner.process import process_files, sniff_kind

from .test_workflow import make_file


def ftyp(brand: bytes) -> bytes:
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x00\x00"


def test_sniff_kinds():
    assert sniff_kind(ftyp(b"qt  ")) == "video"  # iPhone MOV (e.g. a Live Photo clip named .JPG)
    assert sniff_kind(ftyp(b"isom")) == "video"
    assert sniff_kind(ftyp(b"heic")) == "image"
    assert sniff_kind(b"\xff\xd8\xff\xe0" + b"\x00" * 12) == "image"
    assert sniff_kind(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image"
    assert sniff_kind(b'<?xml version="1.0"') == "not_media"  # iPhone .AAE edit data
    assert sniff_kind(b"GARBAGE-NOT-MEDIA") is None  # unknown: fall back to the extension
    assert sniff_kind(b"short") is None


def test_text_file_with_photo_extension_gets_clear_error(session, users):
    f = make_file(session, "Alex/IMG_2697.JPG", users["Alex"], b'<?xml version="1.0" encoding="UTF-8"?><plist/>')

    f.process_state = "pending"
    session.commit()
    process_files(session, [f.id])
    session.refresh(f)
    assert f.process_state == "error" and "Not a photo or video" in f.process_error
    assert f.face_state == "skip"  # never shows up as face work that's waiting


def test_damaged_image_gets_readable_error(session, users):
    buf = io.BytesIO()
    Image.new("RGB", (40, 30)).save(buf, "JPEG")
    f = make_file(session, "Alex/broken.jpg", users["Alex"], buf.getvalue()[:20])
    f.process_state = "pending"
    session.commit()
    process_files(session, [f.id])
    session.refresh(f)
    assert f.process_state == "error" and f.process_error.startswith("Damaged or unsupported image")
    assert f.face_state == "skip"


def test_fixed_file_becomes_eligible_for_faces_again(session, users):
    f = make_file(session, "Alex/later-fixed.jpg", users["Alex"], b"\xff\xd8\xff\xe0" + b"\x00" * 12)
    f.process_state = "pending"
    session.commit()
    process_files(session, [f.id])
    session.refresh(f)
    assert f.face_state == "skip"
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), "white").save(buf, "JPEG")
    with open(abs_path(f.path), "wb") as fh:
        fh.write(buf.getvalue())
    f.process_state = "pending"
    session.commit()
    process_files(session, [f.id])
    session.refresh(f)
    assert f.process_state == "done" and f.face_state == "pending"
