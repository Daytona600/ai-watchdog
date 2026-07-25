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

echo "Collecting Home Assistant error log..."
curl -s \
  -H "$auth_header" \
  -H "$json_header" \
  "$HA_BASE_URL/api/error_log" > "$OUT/ha-error-log.txt" || true

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for HA snapshot v1.1"
  exit 1
fi

VERSION="$(jq -r '.version // "unknown"' "$OUT/ha-config.json")"
STATE="$(jq -r '.state // "unknown"' "$OUT/ha-config.json")"
TZ="$(jq -r '.time_zone // "unknown"' "$OUT/ha-config.json")"
ENTITY_COUNT="$(jq 'length' "$OUT/ha-states.json")"
UNAVAILABLE_COUNT="$(jq '[.[] | select(.state=="unavailable")] | length' "$OUT/ha-states.json")"
UNKNOWN_COUNT="$(jq '[.[] | select(.state=="unknown")] | length' "$OUT/ha-states.json")"

# Save clean machine-readable lists for future diff checks
jq -r '.[].entity_id' "$OUT/ha-states.json" | sort > "$OUT/all-entities.txt"
jq -r '.[] | select(.state=="unavailable") | .entity_id' "$OUT/ha-states.json" | sort > "$OUT/unavailable-entities.txt"
jq -r '.[] | select(.state=="unknown") | .entity_id' "$OUT/ha-states.json" | sort > "$OUT/unknown-entities.txt"

# Critical domains/devices for your system
CRITICAL_REGEX='^(light|switch|lock|climate|cover|media_player|assist_satellite|tts|stt|wake_word|camera|binary_sensor|sensor)\.'

# Known noisy/generated patterns
NOISE_REGEX='browser_mod|_identify$|_ping$|favorite_current_song|ptz_|guard_|_browser_|mass_|plex_|button\.|event\.|update\.|image\.|number\.|select\.|automation\.|script\.|input_text\.|input_select\.|input_boolean\.'

grep -E "$CRITICAL_REGEX" "$OUT/unavailable-entities.txt" | grep -Ev "$NOISE_REGEX" > "$OUT/critical-unavailable.txt" || true
grep -E "$CRITICAL_REGEX" "$OUT/unknown-entities.txt" | grep -Ev "$NOISE_REGEX" > "$OUT/critical-unknown.txt" || true

# Domain summaries
awk -F. '{print $1}' "$OUT/unavailable-entities.txt" | sort | uniq -c | sort -nr > "$OUT/unavailable-by-domain.txt"
awk -F. '{print $1}' "$OUT/unknown-entities.txt" | sort | uniq -c | sort -nr > "$OUT/unknown-by-domain.txt"
awk -F. '{print $1}' "$OUT/all-entities.txt" | sort | uniq -c | sort -nr > "$OUT/entities-by-domain.txt"

# Useful filtered groups
grep -E 'assist_satellite|wake_word|stt\.|tts\.|wyoming|respeaker|voice_|ha_respeaker|shaun|luna|satellite' "$OUT/unavailable-entities.txt" > "$OUT/voice-unavailable.txt" || true
grep -E 'frigate|front_driveway|front_door|solar_shed|rear_corner|camera|reolink|minicam' "$OUT/unavailable-entities.txt" > "$OUT/camera-unavailable.txt" || true
grep -E 'lock|front_door_lock|back_door_lock' "$OUT/unavailable-entities.txt" > "$OUT/lock-unavailable.txt" || true
grep -E 'kitchen_blind|cover\.' "$OUT/unavailable-entities.txt" > "$OUT/blind-unavailable.txt" || true
grep -E 'music_assistant|mass_|media_player\.voice|media_player\.ha_respeaker|media_player\.shaun|media_player\.frontroomsatellite' "$OUT/unavailable-entities.txt" > "$OUT/media-unavailable.txt" || true

# Error hints
grep -Ei "error|failed|exception|traceback|warning|deprecated|repair|unavailable" "$OUT/ha-error-log.txt" | tail -100 > "$OUT/ha-error-hints.txt" || true

echo "# Home Assistant Snapshot Report v1.1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "HA Base URL: $HA_BASE_URL" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Summary" >> "$REPORT"
echo "" >> "$REPORT"
echo "- Version: $VERSION" >> "$REPORT"
echo "- State: $STATE" >> "$REPORT"
echo "- Time zone: $TZ" >> "$REPORT"
echo "- Entity count: $ENTITY_COUNT" >> "$REPORT"
echo "- Unavailable entities: $UNAVAILABLE_COUNT" >> "$REPORT"
echo "- Unknown entities: $UNKNOWN_COUNT" >> "$REPORT"
echo "- Critical unavailable after noise filter: $(wc -l < "$OUT/critical-unavailable.txt")" >> "$REPORT"
echo "- Critical unknown after noise filter: $(wc -l < "$OUT/critical-unknown.txt")" >> "$REPORT"
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

echo "## Unavailable by Domain" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/unavailable-by-domain.txt" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Unknown by Domain" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/unknown-by-domain.txt" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Critical Unavailable - Filtered" >> "$REPORT"
if [ -s "$OUT/critical-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  head -200 "$OUT/critical-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No critical unavailable entities after filtering known noisy/generated entities." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Critical Unknown - Filtered" >> "$REPORT"
if [ -s "$OUT/critical-unknown.txt" ]; then
  echo '```' >> "$REPORT"
  head -200 "$OUT/critical-unknown.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No critical unknown entities after filtering known noisy/generated entities." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Voice/Satellite Unavailable" >> "$REPORT"
if [ -s "$OUT/voice-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/voice-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No voice/satellite unavailable entities found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Camera/Frigate/Reolink Unavailable" >> "$REPORT"
if [ -s "$OUT/camera-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  head -200 "$OUT/camera-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No camera/frigate/reolink unavailable entities found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Lock Unavailable" >> "$REPORT"
if [ -s "$OUT/lock-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/lock-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No lock unavailable entities found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Blind/Cover Unavailable" >> "$REPORT"
if [ -s "$OUT/blind-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/blind-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No blind/cover unavailable entities found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Media/Music Unavailable" >> "$REPORT"
if [ -s "$OUT/media-unavailable.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/media-unavailable.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No media/music unavailable entities found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Recent HA Error Log Hints" >> "$REPORT"
if [ -s "$OUT/ha-error-hints.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/ha-error-hints.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No recent HA error hints found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "HA snapshot saved to: $OUT"
echo "HA report saved to:   $REPORT"
