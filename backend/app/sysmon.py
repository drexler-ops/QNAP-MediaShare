"""Host resource readings for the admin System monitor (CPU, memory, storage, app workers).

Everything comes from /proc and statvfs: Docker containers see the host's CPU and memory counters,
so no privileged access or extra packages are needed. Each reading costs microseconds; nothing runs
unless an admin has the monitor open.

Workers (NAS and any helper PC) drop a small heartbeat file into the shared thumbs folder, which is
how the web process can show their memory without access to the Docker socket.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import config

log = logging.getLogger(__name__)

STATUS_DIR = ".status"  # inside THUMBS_ROOT; skipped by nothing else (thumb buckets are 00000-style)
HEARTBEAT_SECONDS = 30


# --- CPU -----------------------------------------------------------------------------------------

def _read_stat() -> dict[str, tuple[int, int, int, int, int]]:
    """{cpu|cpu0..: (total, idle, iowait, user, system)} jiffies since boot."""
    out = {}
    with open("/proc/stat") as fh:
        for line in fh:
            if not line.startswith("cpu"):
                break
            name, *vals = line.split()
            v = [int(x) for x in vals[:8]] + [0] * max(0, 8 - len(vals))
            user, nice, system, idle, iowait, irq, softirq, steal = v[:8]
            total = user + nice + system + idle + iowait + irq + softirq + steal
            out[name] = (total, idle, iowait, user + nice, system + irq + softirq)
    return out


_cpu_lock = threading.Lock()
_cpu_last: tuple[float, dict] | None = None


def _pct(a: tuple, b: tuple) -> dict:
    dt = max(b[0] - a[0], 1)
    return {
        "busy": round(100 * (1 - (b[1] - a[1] + b[2] - a[2]) / dt), 1),
        "user": round(100 * (b[3] - a[3]) / dt, 1),
        "system": round(100 * (b[4] - a[4]) / dt, 1),
        "iowait": round(100 * (b[2] - a[2]) / dt, 1),
    }


def cpu() -> dict:
    """Usage since the previous reading (the monitor polls every few seconds); first call samples 0.3 s."""
    global _cpu_last
    with _cpu_lock:
        now = time.monotonic()
        if _cpu_last is None or now - _cpu_last[0] > 60:
            prev = _read_stat()
            time.sleep(0.3)
            now = time.monotonic()
        else:
            prev = _cpu_last[1]
        cur = _read_stat()
        _cpu_last = (now, cur)
    total = _pct(prev["cpu"], cur["cpu"])
    cores = [_pct(prev[k], cur[k])["busy"] for k in sorted((k for k in cur if k != "cpu"), key=lambda k: int(k[3:])) if k in prev]
    with open("/proc/loadavg") as fh:
        l1, l5, l15 = (float(x) for x in fh.read().split()[:3])
    return {**total, "cores": cores, "load": [l1, l5, l15]}


# --- memory / uptime -----------------------------------------------------------------------------

def memory() -> dict:
    kb: dict[str, int] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            kb[k] = int(v.split()[0])
    total = kb.get("MemTotal", 0) * 1024
    available = kb.get("MemAvailable", kb.get("MemFree", 0)) * 1024
    cache = (kb.get("Cached", 0) + kb.get("Buffers", 0) + kb.get("SReclaimable", 0)) * 1024
    return {
        "total": total,
        "available": available,
        "used": max(total - available, 0),
        "cache": cache,
        "swap_total": kb.get("SwapTotal", 0) * 1024,
        "swap_used": (kb.get("SwapTotal", 0) - kb.get("SwapFree", 0)) * 1024,
    }


def uptime() -> float | None:
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0])
    except OSError:
        return None


# --- storage -------------------------------------------------------------------------------------

def _mounts() -> list[tuple[str, str]]:
    mounts = [("Media library", config.library_root), ("App data", config.thumbs_root)]
    if os.environ.get("EXTRA_VOLUME"):
        mounts.append((os.environ.get("EXTRA_VOLUME_LABEL") or "Second volume", "/volumes/extra"))
    return mounts


def storage() -> list[dict]:
    """Used / free per volume; folders on the same volume are listed together."""
    vols: dict[int, dict] = {}
    for label, path in _mounts():
        try:
            st = os.statvfs(path)
            dev = os.stat(path).st_dev
        except OSError:
            continue
        if dev in vols:
            vols[dev]["labels"].append(label)
            continue
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        vols[dev] = {"labels": [label], "total": total, "free": free, "used": max(total - st.f_bfree * st.f_frsize, 0)}
    return list(vols.values())


_size_lock = threading.Lock()
_size: dict = {"bytes": None, "at": 0.0, "running": False}
SIZE_TTL = 1800


def _measure(path: str) -> None:
    total = 0
    stack = [path]
    try:
        while stack:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        else:
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        with _size_lock:
            _size.update(bytes=total, at=time.time())
    except OSError as exc:
        log.warning("app data size: %s", exc)
    finally:
        with _size_lock:
            _size["running"] = False


def app_data_size() -> int | None:
    """Size of thumbnails + playable copies; walked in the background at most every 30 min."""
    with _size_lock:
        stale = time.time() - _size["at"] > SIZE_TTL
        if stale and not _size["running"]:
            _size["running"] = True
            threading.Thread(target=_measure, args=(config.thumbs_root,), daemon=True, name="app-data-size").start()
        return _size["bytes"]


# --- container memory + worker heartbeats --------------------------------------------------------

def _read_int(path: str) -> int | None:
    try:
        with open(path) as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def container_memory() -> dict:
    """This container's memory use and limit (cgroup v2, falling back to v1)."""
    used = _read_int("/sys/fs/cgroup/memory.current") or _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    limit = _read_int("/sys/fs/cgroup/memory.max") or _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if limit and limit > 1 << 50:  # "unlimited" in v1
        limit = None
    return {"used": used, "limit": limit}


def write_heartbeat(name: str, processes: int) -> None:
    """Called by each worker's main loop: lets the monitor show worker memory and whether helpers are online."""
    d = os.path.join(config.thumbs_root, STATUS_DIR)
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"worker-{name}.json")
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"name": name, "processes": processes, "at": time.time(), **container_memory()}, fh)
        os.replace(tmp, path)
    except OSError as exc:
        log.debug("heartbeat: %s", exc)


def workers() -> list[dict]:
    d = os.path.join(config.thumbs_root, STATUS_DIR)
    out = []
    try:
        names = sorted(n for n in os.listdir(d) if n.startswith("worker-") and n.endswith(".json"))
    except OSError:
        return out
    now = time.time()
    for n in names:
        try:
            with open(os.path.join(d, n)) as fh:
                hb = json.load(fh)
        except (OSError, ValueError):
            continue
        age = now - float(hb.get("at", 0))
        if age > 24 * 3600:
            continue  # long gone (e.g. a helper PC that was shut down yesterday)
        hb["online"] = age < HEARTBEAT_SECONDS * 4
        hb["seen_seconds_ago"] = round(age)
        out.append(hb)
    return out


def snapshot() -> dict:
    return {
        "cpu": cpu(),
        "memory": memory(),
        "uptime": uptime(),
        "storage": storage(),
        "app_data_bytes": app_data_size(),
        "api": container_memory(),
        "workers": workers(),
    }
