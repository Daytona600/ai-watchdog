#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
REPORT="$BASE/reports/watchdog-server-diff-$(date +'%Y-%m-%d_%H-%M-%S').md"
DIFFDIR="$BASE/snapshots/diffs"

mkdir -p "$BASE/reports" "$DIFFDIR"

LATEST="$(ls -td "$BASE"/snapshots/main-server/* 2>/dev/null | head -1)"
PREV="$(ls -td "$BASE"/snapshots/main-server/* 2>/dev/null | head -2 | tail -1)"

echo "# AI Watchdog Main Server Diff Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "" >> "$REPORT"

if [ -z "${LATEST:-}" ] || [ -z "${PREV:-}" ] || [ "$LATEST" = "$PREV" ]; then
  echo "Not enough main-server snapshots yet to compare." >> "$REPORT"
  echo "Server diff report saved to: $REPORT"
  exit 0
fi

echo "Comparing:" >> "$REPORT"
echo "- Previous main-server snapshot: \`$PREV\`" >> "$REPORT"
echo "- Latest main-server snapshot: \`$LATEST\`" >> "$REPORT"
echo "" >> "$REPORT"

prev_containers="$DIFFDIR/prev-containers.txt"
latest_containers="$DIFFDIR/latest-containers.txt"

awk '{print $1}' "$PREV/docker-ps.txt" | tail -n +2 | sort > "$prev_containers" || true
awk '{print $1}' "$LATEST/docker-ps.txt" | tail -n +2 | sort > "$latest_containers" || true

echo "## New Containers" >> "$REPORT"
echo "" >> "$REPORT"
comm -13 "$prev_containers" "$latest_containers" > "$DIFFDIR/new-containers.txt" || true
if [ -s "$DIFFDIR/new-containers.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$DIFFDIR/new-containers.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No new containers." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## Missing Containers" >> "$REPORT"
echo "" >> "$REPORT"
comm -23 "$prev_containers" "$latest_containers" > "$DIFFDIR/missing-containers.txt" || true
if [ -s "$DIFFDIR/missing-containers.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$DIFFDIR/missing-containers.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No missing containers." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## Container Status Problems Latest" >> "$REPORT"
echo "" >> "$REPORT"
if [ -f "$LATEST/docker-problems.txt" ] && [ -s "$LATEST/docker-problems.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$LATEST/docker-problems.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No unhealthy/restarting/exited/dead containers found in latest snapshot." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## Latest Service Checks" >> "$REPORT"
echo "" >> "$REPORT"
if [ -f "$LATEST/service-checks.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$LATEST/service-checks.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No latest service-checks.txt found." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## Latest Current Error Summary" >> "$REPORT"
echo "" >> "$REPORT"
if [ -f "$LATEST/current-error-summary.txt" ] && [ -s "$LATEST/current-error-summary.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$LATEST/current-error-summary.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No current error summary found, or no recent current errors." >> "$REPORT"
fi

echo ""
echo "Server diff report saved to: $REPORT"
