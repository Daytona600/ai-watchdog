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
run_and_capture "HA Diff" "$BASE/scripts/watchdog_diff_v2.sh"
run_and_capture "Main Server Diff" "$BASE/scripts/watchdog_server_diff_v2.sh"

LATEST_COMBINED="$(ls -t "$BASE"/reports/watchdog-combined-*.md 2>/dev/null | head -1)"
LATEST_HA_DIFF="$(ls -t "$BASE"/reports/watchdog-diff-*.md 2>/dev/null | head -1)"
LATEST_SERVER_DIFF="$(ls -t "$BASE"/reports/watchdog-server-diff-*.md 2>/dev/null | head -1)"

echo "## Report Links" >> "$REPORT"
echo "" >> "$REPORT"
echo "- Combined snapshot: \`${LATEST_COMBINED:-not found}\`" >> "$REPORT"
echo "- HA diff: \`${LATEST_HA_DIFF:-not found}\`" >> "$REPORT"
echo "- Main server diff: \`${LATEST_SERVER_DIFF:-not found}\`" >> "$REPORT"
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

if [ -f "$LATEST_SERVER_DIFF" ]; then
  echo "### Main Server Changes" >> "$REPORT"
  awk '/## New Containers/{flag=1} flag{print}' "$LATEST_SERVER_DIFF" | head -160 >> "$REPORT"
  echo "" >> "$REPORT"
fi

echo "Done."
echo "Master report saved to: $REPORT"
