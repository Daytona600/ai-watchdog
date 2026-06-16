#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
TOKEN_FILE="$BASE/config/ha_token.env"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "Missing token file: $TOKEN_FILE"
  exit 1
fi

# shellcheck disable=SC1090
source "$TOKEN_FILE"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/ha/$STAMP"
REPORT="$BASE/reports/ha-report-$STAMP.md"

mkdir -p "$OUT"

auth_header="Authorization: Bearer $HA_TOKEN"
json_header="Content-Type: application/json"

echo "Collecting Home Assistant config..."
curl -s \
  -H "$auth_header" \
  -H "$json_header" \
  "$HA_BASE_URL/api/config" > "$OUT/ha-config.json"

echo "Collecting Home Assistant states..."
curl -s \
  -H "$auth_header" \
  -H "$json_header" \
  "$HA_BASE_URL/api/states" > "$OUT/ha-states.json"

echo "Collecting Home Assistant error log if available..."
curl -s \
  -H "$auth_header" \
  -H "$json_header" \
  "$HA_BASE_URL/api/error_log" > "$OUT/ha-error-log.txt" || true

echo "# Home Assistant Snapshot Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "HA Base URL: $HA_BASE_URL" >> "$REPORT"
echo "" >> "$REPORT"

if command -v jq >/dev/null 2>&1; then
  VERSION="$(jq -r '.version // "unknown"' "$OUT/ha-config.json")"
  STATE="$(jq -r '.state // "unknown"' "$OUT/ha-config.json")"
  TZ="$(jq -r '.time_zone // "unknown"' "$OUT/ha-config.json")"
  LOCATION="$(jq -r '.location_name // "unknown"' "$OUT/ha-config.json")"

  ENTITY_COUNT="$(jq 'length' "$OUT/ha-states.json")"
  UNAVAILABLE_COUNT="$(jq '[.[] | select(.state=="unavailable")] | length' "$OUT/ha-states.json")"
  UNKNOWN_COUNT="$(jq '[.[] | select(.state=="unknown")] | length' "$OUT/ha-states.json")"

  echo "## Summary" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- Version: $VERSION" >> "$REPORT"
  echo "- State: $STATE" >> "$REPORT"
  echo "- Time zone: $TZ" >> "$REPORT"
  echo "- Location name: $LOCATION" >> "$REPORT"
  echo "- Entity count: $ENTITY_COUNT" >> "$REPORT"
  echo "- Unavailable entities: $UNAVAILABLE_COUNT" >> "$REPORT"
  echo "- Unknown entities: $UNKNOWN_COUNT" >> "$REPORT"
  echo "" >> "$REPORT"

  echo "## Important Components Present" >> "$REPORT"
  echo "" >> "$REPORT"
  for c in mcp_server repairs frigate music_assistant nodered wyoming ollama watchman hacs zha zwave_js matter esphome mqtt; do
    if jq -e --arg c "$c" '.components | index($c)' "$OUT/ha-config.json" >/dev/null 2>&1; then
      echo "- $c: present" >> "$REPORT"
    else
      echo "- $c: not found" >> "$REPORT"
    fi
  done
  echo "" >> "$REPORT"

  echo "## Unavailable Entities" >> "$REPORT"
  echo "" >> "$REPORT"
  if [ "$UNAVAILABLE_COUNT" -gt 0 ]; then
    echo '```' >> "$REPORT"
    jq -r '.[] | select(.state=="unavailable") | .entity_id' "$OUT/ha-states.json" | sort >> "$REPORT"
    echo '```' >> "$REPORT"
  else
    echo "No unavailable entities found." >> "$REPORT"
  fi
  echo "" >> "$REPORT"

  echo "## Unknown Entities" >> "$REPORT"
  echo "" >> "$REPORT"
  if [ "$UNKNOWN_COUNT" -gt 0 ]; then
    echo '```' >> "$REPORT"
    jq -r '.[] | select(.state=="unknown") | .entity_id' "$OUT/ha-states.json" | sort >> "$REPORT"
    echo '```' >> "$REPORT"
  else
    echo "No unknown entities found." >> "$REPORT"
  fi
  echo "" >> "$REPORT"

  echo "## Entity Domain Counts" >> "$REPORT"
  echo "" >> "$REPORT"
  echo '```' >> "$REPORT"
  jq -r '.[].entity_id | split(".")[0]' "$OUT/ha-states.json" \
    | sort \
    | uniq -c \
    | sort -nr >> "$REPORT"
  echo '```' >> "$REPORT"
  echo "" >> "$REPORT"

  echo "## Recent HA Error Log Hints" >> "$REPORT"
  echo "" >> "$REPORT"
  if [ -s "$OUT/ha-error-log.txt" ]; then
    echo '```' >> "$REPORT"
    grep -Ei "error|failed|exception|traceback|warning|deprecated|repair|unavailable" "$OUT/ha-error-log.txt" | tail -100 >> "$REPORT" || echo "No obvious error hints found." >> "$REPORT"
    echo '```' >> "$REPORT"
  else
    echo "No error log returned or log was empty." >> "$REPORT"
  fi

else
  echo "jq is not installed. Raw files were saved but report summary could not be generated." >> "$REPORT"
fi

echo ""
echo "Done."
echo "HA snapshot saved to: $OUT"
echo "HA report saved to:   $REPORT"
