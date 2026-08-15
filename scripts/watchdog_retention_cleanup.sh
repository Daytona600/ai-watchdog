#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"

REPORT_DAYS="${REPORT_DAYS:-30}"
LOG_DAYS="${LOG_DAYS:-14}"
SNAPSHOT_DAYS="${SNAPSHOT_DAYS:-14}"

# Default is dry-run.
# Set WATCHDOG_RETENTION_DELETE=1 to actually delete.
DELETE="${WATCHDOG_RETENTION_DELETE:-0}"

say_action() {
  if [ "$DELETE" = "1" ]; then
    echo "Deleting: $1"
  else
    echo "Would delete: $1"
  fi
}

delete_file_if_old() {
  local dir="$1"
  local days="$2"

  [ -d "$dir" ] || return 0

  find "$dir" -type f -mtime +"$days" | while read -r f; do
    say_action "$f"
    [ "$DELETE" = "1" ] && rm -f "$f"
  done
}

delete_snapshot_dirs_if_old() {
  local dir="$1"
  local days="$2"

  [ -d "$dir" ] || return 0

  find "$dir" -mindepth 1 -maxdepth 1 -type d -mtime +"$days" | while read -r d; do
    say_action "$d"
    [ "$DELETE" = "1" ] && rm -rf "$d"
  done
}

echo "AI Watchdog retention cleanup"
echo "Reports:   keep $REPORT_DAYS days"
echo "Logs:      keep $LOG_DAYS days"
echo "Snapshots: keep $SNAPSHOT_DAYS days"
echo "Delete mode: $DELETE"
echo ""

delete_file_if_old "$BASE/reports" "$REPORT_DAYS"
delete_file_if_old "$BASE/logs" "$LOG_DAYS"

delete_snapshot_dirs_if_old "$BASE/snapshots/main-server" "$SNAPSHOT_DAYS"
delete_snapshot_dirs_if_old "$BASE/snapshots/ha" "$SNAPSHOT_DAYS"

# Diff files are temporary comparison artifacts.
delete_file_if_old "$BASE/snapshots/diffs" "$SNAPSHOT_DAYS"

echo ""
echo "Retention cleanup complete."
