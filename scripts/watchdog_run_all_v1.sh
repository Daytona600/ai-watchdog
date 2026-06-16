#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
REPORT="$BASE/reports/watchdog-combined-$STAMP.md"

mkdir -p "$BASE/reports"

echo "# AI Watchdog Combined Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

echo "Running main server watchdog..."
MAIN_OUTPUT="$($BASE/scripts/watchdog_snapshot_v1_1.sh 2>&1)"
echo "$MAIN_OUTPUT" > "$BASE/logs/watchdog-main-$STAMP.log"

MAIN_REPORT="$(echo "$MAIN_OUTPUT" | grep 'Report saved to:' | awk '{print $4}')"

echo "Running HA watchdog..."
HA_OUTPUT="$($BASE/scripts/ha_snapshot_v1_2.sh 2>&1)"
echo "$HA_OUTPUT" > "$BASE/logs/watchdog-ha-$STAMP.log"

HA_REPORT="$(echo "$HA_OUTPUT" | grep 'HA report saved to:' | awk '{print $5}')"

echo "## Run Summary" >> "$REPORT"
echo "" >> "$REPORT"
echo "- Main watchdog report: \`${MAIN_REPORT:-not found}\`" >> "$REPORT"
echo "- HA watchdog report: \`${HA_REPORT:-not found}\`" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Main Server Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"
if [ -n "${MAIN_REPORT:-}" ] && [ -f "$MAIN_REPORT" ]; then
  awk '/## Attention Needed/{flag=1; next} /^## /{flag=0} flag{print}' "$MAIN_REPORT" >> "$REPORT"
else
  echo "Main watchdog report not found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## HA Critical Entity Problems" >> "$REPORT"
echo "" >> "$REPORT"
if [ -n "${HA_REPORT:-}" ] && [ -f "$HA_REPORT" ]; then
  awk '/## Critical HA Entity Problems/{flag=1; next} /^## /{flag=0} flag{print}' "$HA_REPORT" >> "$REPORT"
else
  echo "HA watchdog report not found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## HA Summary" >> "$REPORT"
echo "" >> "$REPORT"
if [ -n "${HA_REPORT:-}" ] && [ -f "$HA_REPORT" ]; then
  awk '/## Summary/{flag=1; next} /^## /{flag=0} flag{print}' "$HA_REPORT" >> "$REPORT"
else
  echo "HA watchdog report not found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "Done."
echo "Combined report saved to: $REPORT"
