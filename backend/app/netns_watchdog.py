"""Watchdog for the share-only service in its sidecar layout.

The share service runs inside the tailscale-share container's network namespace. If Docker restarts
only that Tailscale container (e.g. after a crash), this container is left attached to the old, dead
namespace and silently stops answering. The watchdog notices (the database becomes unreachable),
and exits the process; Docker's restart policy then restarts it attached to the current namespace.

Enabled with NETNS_WATCHDOG=1 (set for the `share` service in docker-compose.yml).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time

log = logging.getLogger("netns-watchdog")


def _reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _loop(host: str, port: int, interval: float, failures_allowed: int) -> None:
    misses = 0
    while True:
        time.sleep(interval)
        if _reachable(host, port):
            misses = 0
            continue
        misses += 1
        log.warning("cannot reach %s:%s (%d/%d)", host, port, misses, failures_allowed)
        if misses >= failures_allowed:
            log.error("network lost (sidecar namespace likely gone); exiting so Docker restarts this container")
            os._exit(3)


def start() -> None:
    if os.environ.get("NETNS_WATCHDOG") != "1":
        return
    host = os.environ.get("DB_HOST", "db")
    port = int(os.environ.get("DB_PORT", "5432"))
    interval = float(os.environ.get("NETNS_WATCHDOG_INTERVAL", "15"))
    t = threading.Thread(target=_loop, args=(host, port, interval, 3), name="netns-watchdog", daemon=True)
    t.start()
    log.info("watching network via %s:%s every %ss", host, port, interval)
