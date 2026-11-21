from datetime import datetime
from types import SimpleNamespace

from app.dedup.keeper import rank
from app.settings_store import DEFAULTS

W = DEFAULTS["keeper_weights"]


def f(id, path, ext, w, h, exif=0, gps=False, size=1_000_000, seen=None):
    return SimpleNamespace(
        id=id, path=path, ext=ext, width=w, height=h, exif_score=exif,
        gps_lat=41.0 if gps else None, gps_lon=-87.0 if gps else None, size=size,
        first_seen_at=seen or datetime(2024, 1, 1),
    )


def best(files):
    return rank(files, W)[0][0].id


def test_exif_beats_resolution():
    original = f(1, "Jamie/IMG_0001.HEIC", "heic", 4032, 3024, exif=9, gps=True, size=2_000_000)
    upscaled_no_exif = f(2, "Sam/IMG_0001.jpg", "jpg", 6000, 4500, exif=0, size=5_000_000)
    assert best([upscaled_no_exif, original]) == 1


def test_resolution_beats_format_when_exif_equal():
    small_heic = f(1, "a/IMG_1.HEIC", "heic", 1600, 1200, exif=9, gps=True)
    big_jpg = f(2, "a/IMG_1.jpg", "jpg", 4032, 3024, exif=9, gps=True)
    assert best([small_heic, big_jpg]) == 2


def test_original_format_breaks_ties_and_copy_names_penalised():
    heic = f(1, "a/IMG_1.HEIC", "heic", 4032, 3024, exif=5)
    jpg = f(2, "a/IMG_1.jpg", "jpg", 4032, 3024, exif=5)
    wa = f(3, "a/IMG-20240101-WA0001.jpg", "jpg", 4032, 3024, exif=5)
    ranked = [x[0].id for x in rank([wa, jpg, heic], W)]
    assert ranked == [1, 2, 3]


def test_tie_goes_to_first_upload():
    a = f(1, "Sam/x.jpg", "jpg", 100, 100, seen=datetime(2024, 5, 1))
    b = f(2, "Jamie/x.jpg", "jpg", 100, 100, seen=datetime(2024, 4, 1))
    assert best([a, b]) == 2


def test_notes_explain_choice():
    top = rank([f(1, "a/IMG_1.HEIC", "heic", 4032, 3024, exif=9, gps=True), f(2, "a/b.jpg", "jpg", 10, 10)], W)[0][1]
    assert "Has GPS location" in top["notes"]
    assert any(n.startswith("Highest resolution") for n in top["notes"])
