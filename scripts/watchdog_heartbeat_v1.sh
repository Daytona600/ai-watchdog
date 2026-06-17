#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONF="$BASE/config/watchdog_heartbeat.conf"
HA_ENV="$BASE/config/ha_token.env"

PUBLIC="$BASE/public"
mkdir -p "$PUBLIC"

WATCHDOG_MAX_REPORT_AGE_HOURS="30"
WATCHDOG_TIMER_NAME="ai-watchdog.timer"
WATCHDOG_HEARTBEAT_NOTIFICATION_ID="ai_watchdog_heartbeat_problem"

[ -f "$CONF" ] && source "$CONF"

NOW_EPOCH="$(date +%s)"
LATEST_MASTER="$(ls -t "$BASE"/reports/watchdog-master-*.md 2>/dev/null | head -1 || true)"

STATUS="ok"
MESSAGE="Watchdog heartbeat OK."
DETAILS=""

if [ -z "${LATEST_MASTER:-}" ] || [ ! -f "$LATEST_MASTER" ]; then
  STATUS="attention"
  DETAILS="${DETAILS}- No watchdog master report found.\n"
  AGE_HOURS="unknown"
else
  MTIME="$(stat -c %Y "$LATEST_MASTER")"
  AGE_SEC="$((NOW_EPOCH - MTIME))"
  AGE_HOURS="$((AGE_SEC / 3600))"

  MAX_SEC="$((WATCHDOG_MAX_REPORT_AGE_HOURS * 3600))"
  if [ "$AGE_SEC" -gt "$MAX_SEC" ]; then
    STATUS="attention"
    DETAILS="${DETAILS}- Latest watchdog master report is ${AGE_HOURS} hours old: $LATEST_MASTER\n"
  fi
fi

TIMER_STATE="$(systemctl --user is-active "$WATCHDOG_TIMER_NAME" 2>/dev/null || true)"
TIMER_ENABLED="$(systemctl --user is-enabled "$WATCHDOG_TIMER_NAME" 2>/dev/null || true)"

if [ "$TIMER_STATE" != "active" ]; then
  STATUS="attention"
  DETAILS="${DETAILS}- User systemd timer is not active: $WATCHDOG_TIMER_NAME state=$TIMER_STATE\n"
fi

if [ "$TIMER_ENABLED" != "enabled" ]; then
  STATUS="attention"
  DETAILS="${DETAILS}- User systemd timer is not enabled: $WATCHDOG_TIMER_NAME enabled=$TIMER_ENABLED\n"
fi

if [ "$STATUS" = "attention" ]; then
  MESSAGE="AI Watchdog heartbeat problem detected."
else
  DETAILS="- Latest master report is ${AGE_HOURS:-unknown} hours old.\n- Timer $WATCHDOG_TIMER_NAME is active/enabled.\n"
fi

UPDATED="$(date -Iseconds)"

python3 - "$PUBLIC/watchdog-heartbeat.json" "$STATUS" "$UPDATED" "$MESSAGE" "${LATEST_MASTER:-}" "${AGE_HOURS:-unknown}" "$TIMER_STATE" "$TIMER_ENABLED" "$DETAILS" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = {
    "status": sys.argv[2],
    "updated": sys.argv[3],
    "message": sys.argv[4],
    "latest_master_report": sys.argv[5],
    "latest_master_age_hours": sys.argv[6],
    "timer_state": sys.argv[7],
    "timer_enabled": sys.argv[8],
    "details": sys.argv[9].replace("\\n", "\n").strip(),
}
path.write_text(json.dumps(data, indent=2) + "\n")
PY

cat > "$PUBLIC/watchdog-heartbeat.md" <<EOF
# AI Watchdog Heartbeat

Status: $STATUS  
Updated: $UPDATED  

$MESSAGE

## Details

$(printf "%b" "$DETAILS")
EOF

echo "Heartbeat status: $STATUS"
echo "Heartbeat JSON: $PUBLIC/watchdog-heartbeat.json"

# Optional HA notification.
if [ -f "$HA_ENV" ]; then
  source "$HA_ENV"

  if [ "$STATUS" = "attention" ]; then
    BODY="$(python3 - "$MESSAGE" "$DETAILS" <<'PY'
import json, sys
msg=sys.argv[1]
details=sys.argv[2].replace("\\n", "\n").strip()
print(json.dumps({
  "title": "AI Watchdog heartbeat problem",
  "message": msg + "\n\n" + details,
  "notification_id": "ai_watchdog_heartbeat_problem"
}))
PY
)"
    curl -s -X POST \
      -H "Authorization: Bearer $HA_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$BODY" \
      "$HA_BASE_URL/api/services/persistent_notification/create" >/dev/null || true
  else
    BODY='{"notification_id":"ai_watchdog_heartbeat_problem"}'
    curl -s -X POST \
      -H "Authorization: Bearer $HA_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$BODY" \
      "$HA_BASE_URL/api/services/persistent_notification/dismiss" >/dev/null || true
  fi
fi
