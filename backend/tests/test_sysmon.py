"""Settings -> NAS resources: host readings (Linux /proc) and admin-only access."""
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .test_api import login

pytestmark = pytest.mark.skipif(not os.path.exists("/proc/stat"), reason="needs Linux /proc")


def test_snapshot_reads_host_counters(library):
    from app import sysmon

    snap = sysmon.snapshot()
    assert 0 <= snap["cpu"]["busy"] <= 100 and len(snap["cpu"]["cores"]) >= 1 and len(snap["cpu"]["load"]) == 3
    m = snap["memory"]
    assert m["total"] > 0 and 0 < m["available"] <= m["total"] and m["used"] == m["total"] - m["available"]
    # library and app data live on the same test volume: listed once, both labels
    assert len(snap["storage"]) == 1 and snap["storage"][0]["labels"] == ["Media library", "App data"]
    assert snap["storage"][0]["total"] > 0


def test_worker_heartbeats(library):
    from app import sysmon

    sysmon.write_heartbeat("nas", 2)
    sysmon.write_heartbeat("pc", 8)
    old = os.path.join(sysmon.config.thumbs_root, sysmon.STATUS_DIR, "worker-pc.json")
    t = time.time() - 600
    os.utime(old, (t, t))  # mtime is not used: freshness comes from the timestamp inside
    ws = {w["name"]: w for w in sysmon.workers()}
    assert ws["nas"]["online"] and ws["nas"]["processes"] == 2 and ws["pc"]["processes"] == 8


def test_helper_with_a_slow_clock_still_shows_online(library, monkeypatch):
    """The NAS and a helper PC clocks can differ by minutes: a heartbeat that keeps changing is online."""
    from app import sysmon

    real = time.time
    monkeypatch.setattr(sysmon.time, "time", lambda: real() - 1800)  # helper clock 30 min behind
    sysmon.write_heartbeat("slowpc", 4)
    monkeypatch.setattr(sysmon.time, "time", real)
    assert not {w["name"]: w for w in sysmon.workers()}["slowpc"]["online"]  # looks stale at first
    monkeypatch.setattr(sysmon.time, "time", lambda: real() - 1770)
    sysmon.write_heartbeat("slowpc", 4)  # next beat 30 s later
    monkeypatch.setattr(sysmon.time, "time", real)
    assert {w["name"]: w for w in sysmon.workers()}["slowpc"]["online"]


def test_system_endpoint_is_admin_only(session, users):
    with TestClient(create_app()) as c:
        login(c, "Alex")
        assert c.get("/api/admin/system").status_code == 403
        login(c, "admin")
        r = c.get("/api/admin/system")
        assert r.status_code == 200
        body = r.json()
        assert {"cpu", "memory", "storage", "workers", "jobs", "api"} <= body.keys()
