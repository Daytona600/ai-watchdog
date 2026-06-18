#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
REPORT="$BASE/reports/watchdog-master-$STAMP.md"

mkdir -p "$BASE/reports" "$BASE/logs"

echo "# AI Watchdog Master Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

run_and_capture() {
  local name="$1"
  local cmd="$2"
  local logfile="$BASE/logs/${name}-${STAMP}.log"

  echo "Running $name..."
  echo "## $name" >> "$REPORT"
  echo "" >> "$REPORT"

  output="$(bash -lc "$cmd" 2>&1)"
  echo "$output" > "$logfile"

  echo '```' >> "$REPORT"
  echo "$output" >> "$REPORT"
  echo '```' >> "$REPORT"
  echo "" >> "$REPORT"
}

run_and_capture "Combined Snapshot" "$BASE/scripts/watchdog_run_all_v1.sh"
run_and_capture "Storage-NAS Snapshot" "$BASE/scripts/watchdog_storage_v3.sh"
run_and_capture "Node-RED Snapshot" "$BASE/scripts/watchdog_nodered_v1.sh"
run_and_capture "Frigate Camera Snapshot" "$BASE/scripts/watchdog_frigate_v1.sh"
run_and_capture "Backup Validation" "$BASE/scripts/watchdog_backup_validation_v1.sh"
run_and_capture "HA Diff" "$BASE/scripts/watchdog_diff_v2.sh"
run_and_capture "Main Server Diff" "$BASE/scripts/watchdog_server_diff_v2.sh"
run_and_capture "Action Hints" "$BASE/scripts/watchdog_action_hints_v1.sh" "$REPORT"
run_and_capture "Publish Latest Report" "$BASE/scripts/watchdog_publish_latest.sh"
run_and_capture "Publish Runbooks" "$BASE/scripts/watchdog_publish_runbooks_v1.sh"
run_and_capture "Update Monitor" "$BASE/scripts/watchdog_update_monitor_v1.sh"
run_and_capture "Dependency Map" "$BASE/scripts/watchdog_dependencies_v1.py"
run_and_capture "Dashboard Page" "$BASE/scripts/watchdog_dashboard_v1.sh"
run_and_capture "History Page" "$BASE/scripts/watchdog_history_v1.py"
run_and_capture "Morning Brief" "$BASE/scripts/watchdog_morning_brief_v1.py"


LATEST_COMBINED="$(ls -t "$BASE"/reports/watchdog-combined-*.md 2>/dev/null | head -1)"
LATEST_HA_DIFF="$(ls -t "$BASE"/reports/watchdog-diff-*.md 2>/dev/null | head -1)"
LATEST_SERVER_DIFF="$(ls -t "$BASE"/reports/watchdog-server-diff-*.md 2>/dev/null | head -1)"
LATEST_STORAGE="$(ls -t "$BASE"/reports/watchdog-storage-*.md 2>/dev/null | head -1)"
LATEST_NODERED="$(ls -t "$BASE"/reports/watchdog-nodered-*.md 2>/dev/null | head -1)"
LATEST_FRIGATE="$(ls -t "$BASE"/reports/watchdog-frigate-*.md 2>/dev/null | head -1)"
LATEST_BACKUP_VALIDATION="$(ls -t "$BASE"/reports/watchdog-backup-validation-*.md 2>/dev/null | head -1)"
LATEST_UPDATES="$(ls -t "$BASE"/reports/watchdog-updates-*.md 2>/dev/null | head -1)"
LATEST_BRIEF="$(ls -t "$BASE"/reports/watchdog-brief-*.md 2>/dev/null | head -1)"

echo "## Report Links" >> "$REPORT"
echo "" >> "$REPORT"
echo "- Combined snapshot: \`${LATEST_COMBINED:-not found}\`" >> "$REPORT"
echo "- HA diff: \`${LATEST_HA_DIFF:-not found}\`" >> "$REPORT"
echo "- Main server diff: \`${LATEST_SERVER_DIFF:-not found}\`" >> "$REPORT"
echo "- Storage/NAS report: \`${LATEST_STORAGE:-not found}\`" >> "$REPORT"
echo "- Node-RED report: \`${LATEST_NODERED:-not found}\`" >> "$REPORT"
echo "- Frigate camera report: \`${LATEST_FRIGATE:-not found}\`" >> "$REPORT"
echo "- Backup validation report: \`${LATEST_BACKUP_VALIDATION:-not found}\`" >> "$REPORT"
echo "- Update monitor report: \`${LATEST_UPDATES:-not found}\`" >> "$REPORT"
echo "- Morning brief report: \`${LATEST_BRIEF:-not found}\`" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Final Summary" >> "$REPORT"
echo "" >> "$REPORT"

if [ -f "$LATEST_COMBINED" ]; then
  echo "### Main Server Attention Needed" >> "$REPORT"
  awk '/## Main Server Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_COMBINED" >> "$REPORT"
  echo "" >> "$REPORT"

  echo "### HA Critical Entity Problems" >> "$REPORT"
  awk '/## HA Critical Entity Problems/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_COMBINED" >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_HA_DIFF" ]; then
  echo "### HA Changes" >> "$REPORT"
  awk '/## New Unavailable Entities/{flag=1} /^## Critical Entity Problems Latest/{print; flag=1} flag{print}' "$LATEST_HA_DIFF" | head -120 >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_UPDATES" ]; then
  echo "### Update Monitor Attention Needed" >> "$REPORT"
  awk '/## Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_UPDATES" >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_BACKUP_VALIDATION" ]; then
  echo "### Backup / Export Validation Attention Needed" >> "$REPORT"
  awk '/## Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_BACKUP_VALIDATION" >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_FRIGATE" ]; then
  echo "### Frigate Camera Attention Needed" >> "$REPORT"
  awk '/## Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_FRIGATE" >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_NODERED" ]; then
  echo "### Node-RED Attention Needed" >> "$REPORT"
  awk '/## Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_NODERED" >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_STORAGE" ]; then
  echo "### Storage/NAS Attention Needed" >> "$REPORT"
  awk '/## Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$LATEST_STORAGE" >> "$REPORT"
  echo "" >> "$REPORT"
fi

if [ -f "$LATEST_SERVER_DIFF" ]; then
  echo "### Main Server Changes" >> "$REPORT"
  awk '/## New Containers/{flag=1} flag{print}' "$LATEST_SERVER_DIFF" | head -160 >> "$REPORT"
  echo "" >> "$REPORT"
fi

echo "## Retention Cleanup" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
WATCHDOG_RETENTION_DELETE=1 "$BASE/scripts/watchdog_retention_cleanup.sh" >> "$REPORT" 2>&1 || true
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "Done."
echo "Master report saved to: $REPORT"
