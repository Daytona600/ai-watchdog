#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
DIFFDIR="$BASE/snapshots/diffs"
REPORT="$BASE/reports/watchdog-diff-$(date +'%Y-%m-%d_%H-%M-%S').md"

mkdir -p "$DIFFDIR" "$BASE/reports"

LATEST_HA="$(ls -td "$BASE"/snapshots/ha/* 2>/dev/null | head -1)"
PREV_HA="$(ls -td "$BASE"/snapshots/ha/* 2>/dev/null | head -2 | tail -1)"

echo "# AI Watchdog Diff Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "" >> "$REPORT"

if [ -z "${LATEST_HA:-}" ] || [ -z "${PREV_HA:-}" ] || [ "$LATEST_HA" = "$PREV_HA" ]; then
  echo "Not enough HA snapshots yet to compare." >> "$REPORT"
  echo "Run the combined watchdog again later, then rerun this diff." >> "$REPORT"
  echo "Diff report saved to: $REPORT"
  exit 0
fi

echo "Comparing:" >> "$REPORT"
echo "- Previous HA snapshot: \`$PREV_HA\`" >> "$REPORT"
echo "- Latest HA snapshot: \`$LATEST_HA\`" >> "$REPORT"
echo "" >> "$REPORT"

echo "## New Unavailable Entities" >> "$REPORT"
echo "" >> "$REPORT"
comm -13 \
  "$PREV_HA/unavailable-entities.txt" \
  "$LATEST_HA/unavailable-entities.txt" \
  > "$DIFFDIR/new-unavailable.txt" || true

if [ -s "$DIFFDIR/new-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$DIFFDIR/new-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No newly unavailable entities." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## Recovered Entities" >> "$REPORT"
echo "" >> "$REPORT"
comm -23 \
  "$PREV_HA/unavailable-entities.txt" \
  "$LATEST_HA/unavailable-entities.txt" \
  > "$DIFFDIR/recovered-unavailable.txt" || true

if [ -s "$DIFFDIR/recovered-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$DIFFDIR/recovered-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No unavailable entities recovered since previous snapshot." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## New Unknown Entities" >> "$REPORT"
echo "" >> "$REPORT"
comm -13 \
  "$PREV_HA/unknown-entities.txt" \
  "$LATEST_HA/unknown-entities.txt" \
  > "$DIFFDIR/new-unknown.txt" || true

if [ -s "$DIFFDIR/new-unknown.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$DIFFDIR/new-unknown.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No newly unknown entities." >> "$REPORT"
fi

echo "" >> "$REPORT"
echo "## Critical Entity Problems Latest" >> "$REPORT"
echo "" >> "$REPORT"
if [ -f "$LATEST_HA/critical-entity-bad.txt" ] && [ -s "$LATEST_HA/critical-entity-bad.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$LATEST_HA/critical-entity-bad.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No critical HA entity problems in latest snapshot." >> "$REPORT"
fi

echo ""
echo "Diff report saved to: $REPORT"
