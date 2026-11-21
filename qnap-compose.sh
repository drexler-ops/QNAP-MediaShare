#!/bin/sh
# Run `docker compose` for this app on a QNAP NAS, from an SSH session or remotely:
#
#   ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh ps
#   ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh logs --tail 50 api
#   ssh <nas-admin>@<nas> /share/Container/photo-audit/app/qnap-compose.sh up -d --build
#
# Why: over SSH, QNAP doesn't put Container Station's docker on the PATH, and the Docker CLI
# can't create its config folder in the admin's home directory. This script fixes both.
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
for d in /share/CACHEDEV1_DATA/.qpkg/container-station/bin /share/*/.qpkg/container-station/bin; do
  if [ -x "$d/docker" ]; then DOCKER="$d/docker"; break; fi
done
if [ -z "$DOCKER" ]; then DOCKER="$(command -v docker || true)"; fi
if [ -z "$DOCKER" ]; then echo "docker not found: is Container Station installed?" >&2; exit 1; fi
export DOCKER_CONFIG="${DOCKER_CONFIG:-$APP_DIR/../.docker}"
mkdir -p "$DOCKER_CONFIG"
cd "$APP_DIR"
exec "$DOCKER" compose "$@"
