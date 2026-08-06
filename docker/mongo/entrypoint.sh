#!/bin/sh
# docker/mongo/entrypoint.sh
#
# Custom entrypoint for the PR-Agent MongoDB container.
#
# On container start:
#   1. Start mongod
#   2. If a host backup /backups/history_backup.json exists, restore it into
#      the prompting_agent database (upsert by session_id - idempotent, so a
#      restart never clobbers newer data). If it is absent, a fresh database
#      is created.
#   3. Touch /backups/.restored so the container healthcheck can gate the
#      prompting-agent service until history has been loaded.
#
# While running:
#   4. Every BACKUP_INTERVAL_SECONDS (default 60) export the sessions
#      collection to /backups/history_backup.json on the HOST filesystem.
#      The tmp-file + mv swap is atomic, so a crash never leaves a corrupt
#      backup - the host copy always survives container loss.
#   5. On SIGTERM/SIGINT (container shutdown) run one final export first.

set -e

DATABASE="${MONGO_DATABASE:-prompting_agent}"
COLLECTION="${MONGO_COLLECTION:-sessions}"
BACKUP_DIR="/backups"
BACKUP_FILE="${BACKUP_DIR}/history_backup.json"
BACKUP_TMP="${BACKUP_DIR}/history_backup.tmp.json"
INTERVAL="${BACKUP_INTERVAL_SECONDS:-60}"

log() { echo "[entrypoint] $*"; }

export_backup() {
    if ! mongoexport --db "$DATABASE" --collection "$COLLECTION" --out "$BACKUP_TMP" 2>/dev/null; then
        log "export failed (collection not ready) - keeping previous backup"
        return 1
    fi
    mv -f "$BACKUP_TMP" "$BACKUP_FILE"
    log "backup written to $BACKUP_FILE"
}

# --- 1. start mongod -------------------------------------------------------
log "starting mongod"
mongod --bind_ip_all --port 27017 &
MONGOD_PID=$!

trap 'log "shutdown requested - running final export"; export_backup || true; kill "$MONGOD_PID" 2>/dev/null || true; wait "$MONGOD_PID" 2>/dev/null || true; exit 0' TERM INT

# --- wait until MongoDB accepts connections --------------------------------
log "waiting for MongoDB to accept connections"
until mongosh --quiet --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1; do
    sleep 1
done
log "MongoDB is up"

# --- 2. restore backup if present -------------------------------------------
if [ -s "$BACKUP_FILE" ]; then
    log "restoring history from $BACKUP_FILE"
    mongoimport --db "$DATABASE" --collection "$COLLECTION" \
        --file "$BACKUP_FILE" --mode upsert --upsertFields session_id \
        || log "restore reported an issue (upsert is idempotent, continuing)"
else
    log "no backup found - creating fresh database '$DATABASE'"
fi

# Ensure the collection exists so the periodic export never fails.
mongosh --quiet --eval "db.getSiblingDB('$DATABASE').createCollection('$COLLECTION')" 2>/dev/null || true

# --- 3. mark restore complete for the healthcheck ---------------------------
touch "${BACKUP_DIR}/.restored"

# --- 4. periodic backup loop -------------------------------------------------
log "starting backup loop (every ${INTERVAL}s)"
while true; do
    sleep "$INTERVAL"
    export_backup || true
done

wait "$MONGOD_PID"
