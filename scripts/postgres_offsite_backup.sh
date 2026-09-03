#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/home/davids/backups/postgres"
RETENTION_DAYS=7
BWLIMIT_KBPS=300
SSH_KEY="/root/.ssh/vps_backup_ed25519"
VPS_HOST="66.179.252.241"
VPS_PORT=31

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="$BACKUP_DIR/homelab_${TIMESTAMP}.dump"

docker exec postgres-main pg_dump -U homelab -d homelab -Fc > "$DUMP_FILE"

find "$BACKUP_DIR" -name 'homelab_*.dump' -mtime "+${RETENTION_DAYS}" -delete

rsync -a --delete --bwlimit="$BWLIMIT_KBPS" \
  -e "ssh -p $VPS_PORT -i $SSH_KEY -o StrictHostKeyChecking=accept-new" \
  "$BACKUP_DIR/" "root@${VPS_HOST}:"
