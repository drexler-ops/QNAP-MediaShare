from datetime import datetime

from app.scanner.metadata import parse_date, parse_exif


def test_iphone_heic_full_exif():
    tags = {
        "FileType": "HEIC", "MIMEType": "image/heic",
        "DateTimeOriginal": "2024:07:04 18:22:01", "Make": "Apple", "Model": "iPhone 15 Pro",
        "LensModel": "iPhone 15 Pro back triple camera", "ExposureTime": 0.004, "FNumber": 1.78,
        "GPSLatitude": 41.881, "GPSLongitude": -87.623, "ImageWidth": 4032, "ImageHeight": 3024,
        "Orientation": 6, "ContentIdentifier": "4F3C2A10-AAAA-BBBB-CCCC-1234567890AB",
    }
    out = parse_exif(tags)
    assert out["taken_at"] == datetime(2024, 7, 4, 18, 22, 1)
    assert out["has_exif"] is True
    assert out["exif_score"] == 9
    assert (out["gps_lat"], out["gps_lon"]) == (41.881, -87.623)
    # Orientation 6 = rotated 90°: displayed dimensions swap.
    assert (out["width"], out["height"]) == (3024, 4032)
    assert out["live_photo_id"] == "4F3C2A10-AAAA-BBBB-CCCC-1234567890AB"


def test_messaging_copy_has_no_exif():
    out = parse_exif({"FileType": "JPEG", "ImageWidth": 1600, "ImageHeight": 1200})
    assert out["has_exif"] is False
    assert out["exif_score"] == 0
    assert out["taken_at"] is None
    assert out["gps_lat"] is None


def test_quicktime_gps_coordinates_and_dates():
    out = parse_exif({
        "FileType": "MOV", "CreationDate": "2023:12:25 09:15:00-06:00",
        "GPSCoordinates": "41.8 -87.6 180.2", "Make": "Apple", "Duration": 2.9,
    })
    assert out["taken_at"] == datetime(2023, 12, 25, 9, 15)
    assert out["gps_lat"] == 41.8 and out["gps_lon"] == -87.6
    assert out["duration"] == 2.9
    assert out["has_exif"] is True


def test_bad_dates_and_null_island_rejected():
    assert parse_date("0000:00:00 00:00:00") is None
    assert parse_date("garbage") is None
    out = parse_exif({"GPSLatitude": 0, "GPSLongitude": 0})
    assert out["gps_lat"] is None
