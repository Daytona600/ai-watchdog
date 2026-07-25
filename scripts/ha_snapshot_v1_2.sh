#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
TOKEN_FILE="$BASE/config/ha_token.env"
CRITICAL_FILE="$BASE/config/ha_critical_entities.txt"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "Missing token file: $TOKEN_FILE"
  exit 1
fi

if [ ! -f "$CRITICAL_FILE" ]; then
  echo "Missing critical entity file: $CRITICAL_FILE"
  exit 1
fi

source "$TOKEN_FILE"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/ha/$STAMP"
REPORT="$BASE/reports/ha-report-$STAMP.md"

mkdir -p "$OUT"

auth_header="Authorization: Bearer $HA_TOKEN"
json_header="Content-Type: application/json"

echo "Collecting Home Assistant config..."
curl -s -H "$auth_header" -H "$json_header" \
  "$HA_BASE_URL/api/config" > "$OUT/ha-config.json"

echo "Collecting Home Assistant states..."
curl -s -H "$auth_header" -H "$json_header" \
  "$HA_BASE_URL/api/states" > "$OUT/ha-states.json"

echo "Collecting Home Assistant error log..."
curl -s -H "$auth_header" -H "$json_header" \
  "$HA_BASE_URL/api/error_log" > "$OUT/ha-error-log.txt" || true

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required."
  exit 1
fi

VERSION="$(jq -r '.version // "unknown"' "$OUT/ha-config.json")"
STATE="$(jq -r '.state // "unknown"' "$OUT/ha-config.json")"
ENTITY_COUNT="$(jq 'length' "$OUT/ha-states.json")"
UNAVAILABLE_COUNT="$(jq '[.[] | select(.state=="unavailable")] | length' "$OUT/ha-states.json")"
UNKNOWN_COUNT="$(jq '[.[] | select(.state=="unknown")] | length' "$OUT/ha-states.json")"

jq -r '.[].entity_id' "$OUT/ha-states.json" | sort > "$OUT/all-entities.txt"
jq -r '.[] | select(.state=="unavailable") | .entity_id' "$OUT/ha-states.json" | sort > "$OUT/unavailable-entities.txt"
jq -r '.[] | select(.state=="unknown") | .entity_id' "$OUT/ha-states.json" | sort > "$OUT/unknown-entities.txt"

awk -F. '{print $1}' "$OUT/unavailable-entities.txt" | sort | uniq -c | sort -nr > "$OUT/unavailable-by-domain.txt"
awk -F. '{print $1}' "$OUT/unknown-entities.txt" | sort | uniq -c | sort -nr > "$OUT/unknown-by-domain.txt"

CRITICAL_RESULTS="$OUT/critical-entity-results.txt"
CRITICAL_BAD="$OUT/critical-entity-bad.txt"
: > "$CRITICAL_RESULTS"
: > "$CRITICAL_BAD"

echo "Checking critical HA entities..."
while read -r entity; do
  [[ -z "$entity" || "$entity" =~ ^# ]] && continue

  result="$(jq -r --arg e "$entity" '.[] | select(.entity_id==$e) | .state' "$OUT/ha-states.json")"

  if [ -z "$result" ]; then
    result="missing"
  fi

  printf "%-60s %s\n" "$entity" "$result" >> "$CRITICAL_RESULTS"

  case "$result" in
    missing|unavailable|unknown)
      printf "%-60s %s\n" "$entity" "$result" >> "$CRITICAL_BAD"
      ;;
  esac
done < "$CRITICAL_FILE"


# HA backup freshness check
BACKUP_STATUS="$OUT/ha-backup-status.txt"
: > "$BACKUP_STATUS"

backup_manager_state="$(jq -r '.[] | select(.entity_id=="sensor.backup_backup_manager_state") | .state // "missing"' "$OUT/ha-states.json")"
last_success="$(jq -r '.[] | select(.entity_id=="sensor.backup_last_successful_automatic_backup") | .state // "missing"' "$OUT/ha-states.json")"
last_attempt="$(jq -r '.[] | select(.entity_id=="sensor.backup_last_attempted_automatic_backup") | .state // "missing"' "$OUT/ha-states.json")"
next_backup="$(jq -r '.[] | select(.entity_id=="sensor.backup_next_scheduled_automatic_backup") | .state // "missing"' "$OUT/ha-states.json")"

echo "Backup manager state: $backup_manager_state" >> "$BACKUP_STATUS"
echo "Last successful automatic backup: $last_success" >> "$BACKUP_STATUS"
echo "Last attempted automatic backup: $last_attempt" >> "$BACKUP_STATUS"
echo "Next scheduled automatic backup: $next_backup" >> "$BACKUP_STATUS"

if [ "$last_success" = "missing" ] || [ "$last_success" = "unknown" ] || [ "$last_success" = "unavailable" ]; then
  echo "sensor.backup_last_successful_automatic_backup  $last_success" >> "$CRITICAL_BAD"
else
  last_success_epoch="$(date -d "$last_success" +%s 2>/dev/null || echo 0)"
  now_epoch="$(date +%s)"
  backup_age_hours=$(( (now_epoch - last_success_epoch) / 3600 ))
  echo "Last successful backup age hours: $backup_age_hours" >> "$BACKUP_STATUS"

  if [ "$backup_age_hours" -gt 48 ]; then
    echo "sensor.backup_last_successful_automatic_backup  stale-${backup_age_hours}h" >> "$CRITICAL_BAD"
  fi
fi

grep -Ei "error|failed|exception|traceback|warning|deprecated|repair|unavailable" "$OUT/ha-error-log.txt" | tail -100 > "$OUT/ha-error-hints.txt" || true

echo "# Home Assistant Snapshot Report v1.2" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "HA Base URL: $HA_BASE_URL" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Summary" >> "$REPORT"
echo "" >> "$REPORT"
echo "- Version: $VERSION" >> "$REPORT"
echo "- State: $STATE" >> "$REPORT"
echo "- Entity count: $ENTITY_COUNT" >> "$REPORT"
echo "- Unavailable entities total: $UNAVAILABLE_COUNT" >> "$REPORT"
echo "- Unknown entities total: $UNKNOWN_COUNT" >> "$REPORT"
echo "- Critical entity problems: $(wc -l < "$CRITICAL_BAD")" >> "$REPORT"
echo "" >> "$REPORT"

echo "## Critical HA Entity Check" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$CRITICAL_RESULTS" >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Critical HA Entity Problems" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$CRITICAL_BAD" ]; then
  echo '```' >> "$REPORT"
  cat "$CRITICAL_BAD" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No critical HA entity problems found." >> "$REPORT"
fi
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


echo "## HA Backup Status" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$BACKUP_STATUS" ]; then
  echo '```' >> "$REPORT"
  cat "$BACKUP_STATUS" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No HA backup status collected." >> "$REPORT"
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
