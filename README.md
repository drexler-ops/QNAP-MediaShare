# QNAP-MediaShare — Photo Audit

A self-hosted web app for a shared NAS media library (built and tested for QNAP, works on any Docker host). It finds duplicate photos and videos uploaded from phones and PCs, suggests which copy to keep, and runs a **manual, two-stage, audited** deletion process.

**Install:** see [INSTALL.md](INSTALL.md).

## What it does

- **Accounts.** An admin is seeded on first start and creates the other users. A new user, or one whose password an admin reset, chooses a password at their next sign-in. Admins only add user management; otherwise everyone has the same rights.
- **Indexes** the photo library. Each user's top-level folder (set by the admin) defines who owns a file. It reads EXIF/GPS with exiftool, makes thumbnails (HEIC included), computes perceptual hashes, and pairs iOS **Live Photos** (HEIC + MOV) so the two halves are always handled together.
- **Finds duplicates:**
  - *Same photo*: byte-identical copies plus re-encoded, resized or converted copies (HEIC→JPG, WhatsApp), matched by pHash with a BK-tree. Shots whose EXIF capture times differ are never treated as copies of each other.
  - *Videos*: the same clip in different encodings or sizes, matched by frame signatures.
  - *Similar shots*: bursts taken seconds apart. Nothing is suggested for deletion here by default.
- **Suggests a keeper.** The score favours EXIF richness and GPS first, then resolution, then original format (HEIC/RAW/MOV over JPG; copies named like messaging-app files are penalised). The UI explains the choice and warns when a decision would delete the only copy with GPS, EXIF, a capture date, Live Photo motion or the highest resolution.
- **Deletion workflow.** A person marks files, the **owner** approves, and the file moves to the quarantine folder (`QUARANTINE_DIRNAME`) on the same volume. From there it can be restored, or purged after N days. The server refuses to delete the last copy in a group. Every action goes in the audit log.
- **Other pages:** Browse (filters for owner, date, camera, EXIF, GPS, Live Photos), Map (Leaflet clusters), Settings (thresholds, keeper weights, users and folders (admin), scan schedule, system status).
- **v2 (not built yet):** face recognition. The schema already has `people`/`faces` placeholders.

## Configuration

Everything deployment-specific is an environment variable in `.env` (template: [.env.example](.env.example)); nothing about a particular NAS is hard-coded.

| Variable | Default | Purpose |
|---|---|---|
| `DB_PASSWORD` | — (required) | Postgres password |
| `SECRET_KEY` | — (required) | Signs session cookies |
| `LIBRARY_PATH` | — (required) | Host path of the media share; one top-level folder per user |
| `THUMBS_PATH` | — (required) | Host path for thumbnails / video previews |
| `ADMIN_USERNAME` | `admin` | Admin account created on first start (if no admin exists) |
| `ADMIN_PASSWORD` | random, printed in log | Initial admin password |
| `APP_NAME` | `Photo Audit` | Name in the browser tab, sidebar and sign-in page |
| `APP_PORT` | `8090` | Web port on the host (8080 is avoided because QNAP's admin page uses it) |
| `NAS_WORKER_CONCURRENCY` | `2` | Processing processes on the NAS |
| `QUARANTINE_DIRNAME` | `.quarantine` | Folder inside the library for approved deletions (must start with `.` or `@`) |
| `MAP_TILE_URL` | OpenStreetMap | Leaflet tile URL template; your own tile server for offline use; `off` hides the Map page |
| `MAP_ATTRIBUTION` | `© OpenStreetMap contributors` | Attribution shown on the map |
| `TZ` | `UTC` | Container timezone (schedules, logs) |
| `DB_PUBLISH_PORT` | `5432` | Database port exposed on the LAN (for the helper-PC worker) |
| `DB_SHARED_BUFFERS`, `DB_MEM_LIMIT`, `API_MEM_LIMIT`, `WORKER_MEM_LIMIT` | `512MB`, `1g`, `768m`, `2g` | Resource tuning |

Helper-PC worker ([.env.pc-worker.example](.env.pc-worker.example)): `NAS_IP`, `DB_PORT`, `DB_PASSWORD`, `SMB_LIBRARY_SHARE`, `SMB_THUMBS_PATH`, `SMB_USER`, `SMB_PASSWORD`, `SMB_VERSION`, `QUARANTINE_DIRNAME`, `PC_WORKER_CONCURRENCY`.

Duplicate-detection thresholds, keeper weights, scan interval and purge policy are edited in the app (**Settings**) and stored in the database.

## Architecture

```
            browser ──► api (FastAPI + built React app, host :8090) ──┐
                                                                 ├─► PostgreSQL (jobs, files, groups, decisions, audit)
 NAS worker (scheduler + 2 procs) ─┐                             │
 PC worker (temporary, N procs)  ──┴─ claim jobs (SKIP LOCKED) ──┘
        │  scan → process (exiftool, hashes, thumbs, ffmpeg) → sha pass → group
        └─ /library = LIBRARY_PATH (media share)     /thumbs = THUMBS_PATH
```

| Path | Purpose |
|---|---|
| `backend/app/scanner/` | walk, metadata, hashing, thumbnails, video, Live Photo pairing |
| `backend/app/dedup/` | clustering (`grouping.py`) and keeper scoring (`keeper.py`) |
| `backend/app/deletion/` | mark → approve → quarantine → restore/purge state machine |
| `backend/app/api/` | REST endpoints |
| `backend/app/jobs.py`, `worker.py` | Postgres job queue and worker processes |
| `frontend/src/pages/Review.tsx` | side-by-side review screen |

## Development

```sh
# Backend tests. The pipeline test needs exiftool + ffmpeg, so run the full suite in Docker:
docker build --target test .

# Local stack against a generated sample library
docker run --rm -v "$PWD/backend:/app/backend" -v "$PWD/data/library:/fixture" photo-audit:latest python -m tests.fixtures_gen /fixture
cp .env.example .env   # set LIBRARY_PATH=./data/library, THUMBS_PATH=./data/thumbs (and APP_PORT / DB_PUBLISH_PORT if taken)
docker compose up -d --build

# Frontend dev server with hot reload (proxies /api to :8080)
cd frontend && npm install && npm run dev
```
