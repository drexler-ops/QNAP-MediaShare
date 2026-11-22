"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("DB_USER", "photoaudit")
    password = os.environ.get("DB_PASSWORD", "photoaudit")
    host = os.environ.get("DB_HOST", "db")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "photoaudit")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{name}"


def _quarantine_dirname() -> str:
    name = os.environ.get("QUARANTINE_DIRNAME", "").strip().strip("/\\") or ".quarantine"
    if "/" in name or "\\" in name or name[0] not in ".@":
        raise ValueError("QUARANTINE_DIRNAME must be a single folder name starting with '.' or '@'")
    return name


@dataclass(frozen=True)
class Config:
    database_url: str = field(default_factory=_database_url)
    # Display name shown in the browser tab, sidebar and sign-in page.
    app_name: str = field(default_factory=lambda: os.environ.get("APP_NAME", "").strip() or "Photo Audit")
    # Map tiles. Point at a self-hosted tile server for offline use; set MAP_TILE_URL=off to hide the map.
    map_tile_url: str = field(
        default_factory=lambda: os.environ.get("MAP_TILE_URL", "").strip()
        or "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
    )
    map_attribution: str = field(
        default_factory=lambda: os.environ.get("MAP_ATTRIBUTION", "").strip() or "© OpenStreetMap contributors"
    )
    # Share-only mode: serve nothing but public album links (/s/<token>) and their media.
    # Used for the internet-facing container; the full app stays private.
    share_only: bool = field(default_factory=lambda: _bool(os.environ.get("SHARE_ONLY"), False))
    # Public base URL for share links (e.g. https://photos-share.example.ts.net). Empty = same as the app.
    public_share_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_SHARE_URL", "").strip().rstrip("/"))
    # Root of the media library inside the container (the NAS share is mounted here).
    library_root: str = field(default_factory=lambda: os.environ.get("LIBRARY_ROOT", "/library"))
    # Where thumbnails / video previews are written (shared between NAS + PC workers).
    thumbs_root: str = field(default_factory=lambda: os.environ.get("THUMBS_ROOT", "/thumbs"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", "change-me"))
    worker_concurrency: int = field(default_factory=lambda: int(os.environ.get("WORKER_CONCURRENCY", "2")))
    worker_name: str = field(default_factory=lambda: os.environ.get("WORKER_NAME", socket.gethostname()))
    # Only the NAS worker should run the scheduler (periodic scans, auto-purge).
    scheduler_enabled: bool = field(default_factory=lambda: _bool(os.environ.get("SCHEDULER_ENABLED"), True))
    # Seeded on first start if no admin exists. Without ADMIN_PASSWORD a random one is printed to the log.
    admin_username: str = field(default_factory=lambda: os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin")
    admin_password: str | None = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD") or None)
    # Folder (inside the library) that approved deletions are moved to. Must start with "." or "@"
    # so scans skip it.
    quarantine_dirname: str = field(default_factory=lambda: _quarantine_dirname())
    frontend_dist: str = field(default_factory=lambda: os.environ.get("FRONTEND_DIST", "/app/frontend_dist"))
    exiftool: str = field(default_factory=lambda: os.environ.get("EXIFTOOL", "exiftool"))
    ffmpeg: str = field(default_factory=lambda: os.environ.get("FFMPEG", "ffmpeg"))
    ffprobe: str = field(default_factory=lambda: os.environ.get("FFPROBE", "ffprobe"))
    run_migrations: bool = field(default_factory=lambda: _bool(os.environ.get("RUN_MIGRATIONS"), True))


config = Config()
