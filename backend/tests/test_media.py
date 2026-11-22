import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.main import create_app
from app.scanner.thumbs import clear_derived, full_path

from .test_api import login
from .test_workflow import make_file


def image_bytes(fmt: str, size=(64, 48)) -> bytes:
    if fmt == "HEIF":
        pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def client(session, users):
    with TestClient(create_app()) as c:
        login(c, "Alex")
        yield c


def test_full_serves_browser_formats_directly(client, session, users):
    data = image_bytes("JPEG")
    f = make_file(session, "Alex/a.jpg", users["Alex"], data)
    session.commit()
    r = client.get(f"/api/files/{f.id}/full")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == data  # untouched original bytes
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert not os.path.exists(full_path(f.id))  # nothing cached for native formats


def test_full_converts_heic_once_and_caches(client, session, users):
    f = make_file(session, "Alex/b.HEIC", users["Alex"], image_bytes("HEIF", (120, 80)))
    session.commit()
    r = client.get(f"/api/files/{f.id}/full")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (120, 80)  # full resolution, not a thumbnail
    assert os.path.isfile(full_path(f.id))
    clear_derived(f.id)  # what processing does when the source file changes
    assert not os.path.exists(full_path(f.id))


def test_original_is_always_a_download(client, session, users):
    f = make_file(session, "Alex/c.jpg", users["Alex"], image_bytes("JPEG"))
    session.commit()
    r = client.get(f"/api/files/{f.id}/original")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment") and "c.jpg" in r.headers["content-disposition"]


def test_full_refuses_purged_missing_and_videos(client, session, users):
    gone = make_file(session, "Alex/d.jpg", users["Alex"], image_bytes("JPEG"), status="purged")
    missing = make_file(session, "Alex/e.jpg", users["Alex"], image_bytes("JPEG"))
    vid = make_file(session, "Alex/f.mp4", users["Alex"], b"xx", kind="video")
    session.commit()
    os.remove(os.path.join(os.environ["LIBRARY_ROOT"], "Alex", "e.jpg"))
    assert client.get(f"/api/files/{gone.id}/full").status_code == 410
    assert client.get(f"/api/files/{missing.id}/full").status_code == 404
    assert client.get(f"/api/files/{vid.id}/full").status_code == 400


def test_media_requires_sign_in(session, users):
    f = make_file(session, "Alex/g.jpg", users["Alex"], image_bytes("JPEG"))
    session.commit()
    with TestClient(create_app()) as anon:
        assert anon.get(f"/api/files/{f.id}/full").status_code == 401
        assert anon.get(f"/api/files/{f.id}/original").status_code == 401


def test_video_preview_converts_in_the_web_process(tmp_path, monkeypatch):
    """Previews never wait in the job queue: first ask starts a conversion, then it's ready; failures explain."""
    import time

    from app import previews

    made = []

    def fake_make(src, out, **options):
        made.append((src, out))
        if "broken" in src:
            raise RuntimeError("Invalid data found when processing input")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(b"mp4")

    monkeypatch.setattr(previews, "make_preview", fake_make)
    monkeypatch.setattr(previews, "preview_path", lambda fid: str(tmp_path / f"{fid}_preview.mp4"))

    def wait(fid, rel):
        for _ in range(100):
            st = previews.request(fid, rel)
            if st["status"] != "processing":
                return st
            time.sleep(0.02)
        return st

    first = previews.request(901, "Alex/clip.mov")
    assert first["status"] == "processing" and first["stage"] in ("queued", "running")
    assert wait(901, "Alex/clip.mov") == {"status": "ready"} and len(made) == 1
    assert previews.request(901, "Alex/clip.mov") == {"status": "ready"} and len(made) == 1  # cached

    bad = wait(902, "Alex/broken.mov")
    assert bad["status"] == "failed" and "couldn't be converted" in bad["error"] and "Invalid data" in bad["error"]
    assert previews.request(902, "Alex/broken.mov")["status"] == "failed"  # remembered, not retried every poll
    assert previews.request(902, "Alex/broken.mov", retry=True)["status"] == "processing"  # explicit retry


def test_playability_rules_and_background_preview_queue(session, users):
    """H.264 in an MP4 container plays as-is even when misnamed; other videos get background conversion."""
    from app.jobs import enqueue_previews
    from app.models import Job
    from app.previews import direct_playable, hevc_original_ok, needs_preview

    mp4_head = b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 32
    avi_head = b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 32
    h264_jpg = make_file(session, "Alex/IMG_1.JPG", users["Alex"], mp4_head)   # Live Photo clip named .JPG
    h264_avi = make_file(session, "Alex/old.avi", users["Alex"], avi_head)
    hevc_mov = make_file(session, "Alex/IMG_2.MOV", users["Alex"], mp4_head)
    mjpeg = make_file(session, "Alex/cam.avi", users["Alex"], avi_head)
    for f, codec in ((h264_jpg, "h264"), (h264_avi, "h264"), (hevc_mov, "hevc"), (mjpeg, "mjpeg")):
        f.kind, f.codec, f.process_state = "video", codec, "done"
    hevc_mov.role = "live_video"
    session.commit()

    assert direct_playable(h264_jpg) and not direct_playable(h264_avi)
    assert hevc_original_ok(hevc_mov) and not hevc_original_ok(mjpeg)
    assert not needs_preview(h264_jpg) and needs_preview(h264_avi) and needs_preview(mjpeg)

    assert enqueue_previews(session) == 2  # live clip batch first (higher priority), then the rest
    jobs = session.scalars(select(Job).where(Job.type == "preview").order_by(Job.priority.desc())).all()
    assert jobs[0].payload["ids"] == [hevc_mov.id] and jobs[0].priority > jobs[1].priority
    assert sorted(jobs[1].payload["ids"]) == sorted([h264_avi.id, mjpeg.id])


def test_heavy_videos_stream_a_lighter_full_length_copy(session, users, tmp_path, monkeypatch):
    """4K / very high-bitrate originals: play the original until a full-length 1080p copy exists, then the copy."""
    from app import previews
    from app.api import media

    monkeypatch.setattr(media, "preview_path", lambda fid: str(tmp_path / f"{fid}_preview.mp4"))
    monkeypatch.setattr(previews, "preview_path", lambda fid: str(tmp_path / f"{fid}_preview.mp4"))
    mp4_head = b"\x00\x00\x00\x18ftypqt  " + b"\x00" * 32
    big = make_file(session, "Alex/4k.mp4", users["Alex"], mp4_head)
    small = make_file(session, "Alex/phone.mp4", users["Alex"], mp4_head)
    hevc4k = make_file(session, "Alex/4k.mov", users["Alex"], mp4_head)
    for f, codec, (w, h) in ((big, "h264", (3840, 2160)), (small, "h264", (1920, 1080)), (hevc4k, "hevc", (2160, 3840))):
        f.kind, f.codec, f.process_state, f.width, f.height, f.duration = "video", codec, "done", w, h, 458.0
    small.size = int(10e6 / 8 * 458)  # 10 Mbit/s
    session.commit()

    assert previews.heavy(big) and previews.heavy(hevc4k) and not previews.heavy(small)
    assert previews.needs_preview(big) and not previews.needs_preview(small)
    assert not previews.hevc_original_ok(hevc4k)  # converted copy instead of the 4K original
    assert previews.preview_options(big) == {"max_seconds": None, "short_side": 1080, "duration": 458.0}
    assert previews.preview_options(small)["max_seconds"] == previews.PREVIEW_SECONDS

    assert media.preview_state(session, big) == {"status": "ready"}  # no capped-preview note: plays in full
    src = media.serve_preview(big)
    assert src.path.endswith("4k.mp4")  # original until the copy exists
    open(tmp_path / f"{big.id}_preview.mp4", "wb").write(b"mp4")
    assert media.serve_preview(big).path == str(tmp_path / f"{big.id}_preview.mp4")
    assert not previews.needs_preview(big)
