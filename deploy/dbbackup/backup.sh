#!/bin/sh
# Nightly database snapshot (the "dbbackup" service in docker-compose.yml).
#
# Writes a compressed pg_dump to /backups (DB_BACKUP_PATH on the host) every day at DB_BACKUP_TIME
# (container timezone, TZ) and keeps the newest DB_BACKUP_KEEP. A file-level copy of the running
# database's own folder isn't a reliable backup; these snapshots are, so point the NAS backup job
# (e.g. QNAP Hybrid Backup Sync) at this folder together with the photo library.
#
# Restore: see INSTALL.md -> "Backups".
#   Run one now:  docker compose exec dbbackup sh /backup.sh now
set -eu
DIR=/backups
TIME="${DB_BACKUP_TIME:-01:30}"
KEEP="${DB_BACKUP_KEEP:-7}"
umask 077 # the dump holds password hashes and share-link tokens: readable by the backup job (root) only

snapshot() {
  stamp="$(date +%Y-%m-%d_%H%M)"
  part="$DIR/.photoaudit-$stamp.dump.part"
  if pg_dump -h db -U photoaudit -d photoaudit -Fc -Z 6 -f "$part"; then
    mv "$part" "$DIR/photoaudit-$stamp.dump"
    echo "$(date '+%F %T') snapshot photoaudit-$stamp.dump ($(du -k "$DIR/photoaudit-$stamp.dump" | cut -f1) KB)"
    # Keep the newest $KEEP (names sort by date).
    ls -1 "$DIR"/photoaudit-*.dump 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
      rm -f "$old" && echo "$(date '+%F %T') removed old $(basename "$old")"
    done
    date '+%F %T' > "$DIR/LAST_SUCCESS"
  else
    rm -f "$part"
    echo "$(date '+%F %T') SNAPSHOT FAILED" >&2
    return 1
  fi
}

if [ "${1:-}" = "now" ]; then snapshot; exit $?; fi

case "$TIME" in [0-2][0-9]:[0-5][0-9]) ;; *) echo "DB_BACKUP_TIME must be HH:MM, got '$TIME'" >&2; exit 1 ;; esac
mkdir -p "$DIR"
echo "$(date '+%F %T') database snapshots daily at $TIME ($(date +%Z)), keeping $KEEP, in DB_BACKUP_PATH"
until pg_isready -q -h db -U photoaudit; do sleep 5; done
# Catch up at start if the newest snapshot is over a day old (NAS was off at backup time, first start).
if [ -z "$(find "$DIR" -maxdepth 1 -name 'photoaudit-*.dump' -mmin -1440 2>/dev/null)" ]; then snapshot || true; fi
done_day=""
while true; do
  if [ "$(date +%H:%M)" = "$TIME" ] && [ "$done_day" != "$(date +%F)" ]; then
    done_day="$(date +%F)"
    snapshot || true
  fi
  sleep 20
done
