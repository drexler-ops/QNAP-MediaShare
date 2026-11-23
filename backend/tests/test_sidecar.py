"""Google Takeout .json sidecars: matching quirks, parsing, gap-filling only, scan backfill."""
import io
import json
import os
import shutil
from datetime import datetime

import pytest
from PIL import Image
from sqlalchemy import select

from app.models import File
from app.paths import abs_path
from app.scanner.sidecar import apply_sidecar, backfill, find_sidecar, read_sidecar

from .test_workflow import make_file

TS = 1100253187  # Nov 12, 2004 09:53:07 UTC


def side(folder, name, ts=TS, lat=41.88, lon=-87.63, exif_geo=None):
    data = {"title": "x", "photoTakenTime": {"timestamp": str(ts)},
            "geoData": {"latitude": lat, "longitude": lon, "altitude": 0.0}}
    if exif_geo:
        data["geoDataExif"] = {"latitude": exif_geo[0], "longitude": exif_geo[1]}
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w") as fh:
        json.dump(data, fh)


def test_finds_sidecars_for_takeout_naming(tmp_path):
    d = str(tmp_path)
    side(d, "IMG_1.jpg.supplemental-metadata.json")
    side(d, "IMG_2.HEIC.supplemental-metadata(1).json")
    side(d, "old.png.json")
    side(d, "IMG_3.jpg.supplemental-metadata.json")
    long_name = "Screenshot_20190405-201512_Samsung Internet.jpg"
    side(d, (long_name + ".supplemental-metadata")[:46] + ".json")  # Google cuts long names short

    assert find_sidecar(f"{d}/IMG_1.jpg").endswith("IMG_1.jpg.supplemental-metadata.json")
    assert find_sidecar(f"{d}/IMG_2(1).HEIC").endswith("IMG_2.HEIC.supplemental-metadata(1).json")
    assert find_sidecar(f"{d}/IMG_2.HEIC") is None  # the (1) sidecar belongs to the duplicate only
    assert find_sidecar(f"{d}/old.png").endswith("old.png.json")
    assert find_sidecar(f"{d}/IMG_3-edited.jpg").endswith("IMG_3.jpg.supplemental-metadata.json")
    assert find_sidecar(f"{d}/{long_name}") is not None
    assert find_sidecar(f"{d}/unrelated.jpg") is None
    # Live Photo motion clips share their photo's sidecar (also numbered duplicates, and dotted names)
    side(d, "IMG_5.HEIC.supplemental-metadata.json")
    side(d, "trip 2.2004 01.jpg.supplemental-metadata.json")
    assert find_sidecar(f"{d}/IMG_5.MP4").endswith("IMG_5.HEIC.supplemental-metadata.json")
    assert find_sidecar(f"{d}/IMG_2(1).MP4").endswith("IMG_2.HEIC.supplemental-metadata(1).json")
    assert find_sidecar(f"{d}/trip 2.2004 01.mov").endswith("trip 2.2004 01.jpg.supplemental-metadata.json")


def test_reads_date_and_place_ignoring_empty_location(tmp_path):
    d = str(tmp_path)
    side(d, "a.json", lat=0.0, lon=0.0)
    side(d, "b.json", lat=1.0, lon=2.0, exif_geo=(41.5, -87.5))
    a, b = read_sidecar(f"{d}/a.json"), read_sidecar(f"{d}/b.json")
    assert a["taken_at"] == datetime.fromtimestamp(TS) and a["gps"] is None  # 0,0 = no location
    assert b["gps"] == (41.5, -87.5)  # the camera's own location preferred over Google's estimate


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (20, 90, 160)).save(buf, "JPEG")
    return buf.getvalue()


def test_fills_only_missing_values(session, users):
    exif_date = datetime(2004, 11, 1, 8, 0, 0)
    f = make_file(session, "Alex/Takeout/IMG_9.jpg", users["Alex"], _jpeg(), taken_at=exif_date)
    side(os.path.dirname(abs_path(f.path)), "IMG_9.jpg.supplemental-metadata.json")
    assert apply_sidecar(f, abs_path(f.path)) == ["location"]
    assert f.taken_at == exif_date  # the photo's own date wins
    assert (f.gps_lat, f.gps_lon) == (41.88, -87.63)
    assert "location from IMG_9.jpg.supplemental-metadata.json" in f.exif["Google Takeout"]
    assert apply_sidecar(f, abs_path(f.path)) == []  # nothing left to fill


def test_scan_backfills_json_added_after_indexing(session, users):
    f = make_file(session, "Alex/Takeout/late.jpg", users["Alex"], _jpeg())
    g = make_file(session, "Alex/Other/plain.jpg", users["Alex"], _jpeg())
    session.commit()
    side(os.path.dirname(abs_path(f.path)), "late.jpg.supplemental-metadata.json")
    res = backfill(session)
    assert res == {"checked": 1, "dates": 1, "locations": 1}  # folders without .json are not touched
    session.refresh(f)
    session.refresh(g)
    assert f.taken_at == datetime.fromtimestamp(TS) and f.gps_lat == 41.88
    assert g.taken_at is None


@pytest.mark.skipif(not shutil.which("exiftool"), reason="needs exiftool")
def test_processing_uses_the_sidecar(session, users):
    from app.scanner.process import process_files
    from app.scanner.walker import scan

    folder = abs_path("Alex/Takeout/Photos from 2004")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "scan 015.jpg"), "wb") as fh:
        fh.write(_jpeg())  # no EXIF at all, like many Takeout exports
    side(folder, "scan 015.jpg.supplemental-metadata.json")
    res = scan(session)
    assert res.new == 1  # the .json itself is never indexed
    process_files(session, res.to_process)
    f = session.scalar(select(File))
    assert f.process_state == "done" and f.taken_at == datetime.fromtimestamp(TS) and f.gps_lat == 41.88
