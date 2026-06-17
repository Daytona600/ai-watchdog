#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
HA_ENV="$BASE/config/ha_token.env"
ALERT_CONF="$BASE/config/watchdog_alerts.conf"
IGNORE_FILE="$BASE/config/watchdog_alert_ignore_patterns.txt"
STATE_DIR="$BASE/logs/alert-state"
PUBLIC="$BASE/public"

mkdir -p "$STATE_DIR" "$PUBLIC"

WATCHDOG_ALERTS_ENABLED="1"
WATCHDOG_NOTIFICATION_ID="ai_watchdog_attention"
WATCHDOG_ALERT_ON_CHANGE_ONLY="1"

[ -f "$ALERT_CONF" ] && source "$ALERT_CONF"

ALERT_TXT="$STATE_DIR/current_attention.txt"
ALERT_HASH_FILE="$STATE_DIR/last_attention_hash.txt"
PUBLIC_TXT="$PUBLIC/alert.txt"
PUBLIC_JSON="$PUBLIC/alert.json"

LATEST_MASTER="$(ls -t "$BASE"/reports/watchdog-master-*.md 2>/dev/null | head -1)"

if [ -z "${LATEST_MASTER:-}" ] || [ ! -f "$LATEST_MASTER" ]; then
  echo "No master report found for alert check."
  exit 0
fi

python3 - "$LATEST_MASTER" "$ALERT_TXT" "$IGNORE_FILE" <<'PY'
from pathlib import Path
import re
import sys

report = Path(sys.argv[1])
out = Path(sys.argv[2])
ignore_file = Path(sys.argv[3])

txt = report.read_text(errors="replace")

ignore_patterns = []
if ignore_file.exists():
    for raw in ignore_file.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ignore_patterns.append(re.compile(line, re.I))

section_keys = (
    "attention needed",
    "critical ha entity problems",
)

clean_phrases = (
    "no attention",
    "no storage/nas attention",
    "no critical ha entity problems",
    "no main server attention",
    "no ha critical",
    "no storage",
    "no problems",
    "no watchdog attention",
)

problem_words = (
    "missing",
    "unavailable",
    "unknown",
    "failed",
    "failure",
    "error",
    "stale",
    "critically",
    "getting full",
    "vram",
    "not-mounted",
    "warning threshold",
    "root disk",
)

sections = []
current_title = None
current_lines = []

def flush():
    global current_title, current_lines
    if current_title and current_lines:
        sections.append((current_title, current_lines))
    current_title = None
    current_lines = []

for raw in txt.splitlines():
    line = raw.rstrip()
    heading = re.match(r"^#{2,4}\s+(.+?)\s*$", line)

    if heading:
        flush()
        title = heading.group(1).strip()
        low_title = title.lower()
        if any(key in low_title for key in section_keys):
            current_title = title
            current_lines = []
        continue

    if not current_title:
        continue

    stripped = line.strip()

    if not stripped or stripped == "```":
        continue

    low = stripped.lower()

    if any(phrase in low for phrase in clean_phrases):
        continue

    if any(rx.search(stripped) for rx in ignore_patterns):
        continue

    is_problem = stripped.startswith("- ") or any(word in low for word in problem_words)

    if is_problem:
        current_lines.append(stripped)

flush()

output = []
for title, lines in sections:
    if not lines:
        continue
    output.append(f"{title}:")
    output.extend(lines[:40])
    output.append("")

final = "\n".join(output).strip()
out.write_text(final + ("\n" if final else ""))
PY

if [ ! -s "$ALERT_TXT" ]; then
  echo "OK: no watchdog attention items." | tee "$PUBLIC_TXT" >/dev/null

  python3 - "$PUBLIC_JSON" <<'PY'
from pathlib import Path
import json
import datetime
import sys

Path(sys.argv[1]).write_text(json.dumps({
    "status": "ok",
    "attention": False,
    "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    "message": "No watchdog attention items."
}, indent=2))
PY

  if [ -f "$HA_ENV" ]; then
    source "$HA_ENV"
    if [ -n "${HA_BASE_URL:-}" ] && [ -n "${HA_TOKEN:-}" ]; then
      curl -fsS -X POST \
        -H "Authorization: Bearer $HA_TOKEN" \
        -H "Content-Type: application/json" \
        "$HA_BASE_URL/api/services/persistent_notification/dismiss" \
        -d "{\"notification_id\":\"$WATCHDOG_NOTIFICATION_ID\"}" >/dev/null 2>&1 || true
    fi
  fi

  rm -f "$ALERT_HASH_FILE"
  echo "No alert sent."
  exit 0
fi

ATTENTION_TEXT="$(cat "$ALERT_TXT")"
ATTENTION_HASH="$(sha256sum "$ALERT_TXT" | awk '{print $1}')"
LAST_HASH="$(cat "$ALERT_HASH_FILE" 2>/dev/null || true)"

cat "$ALERT_TXT" > "$PUBLIC_TXT"

python3 - "$PUBLIC_JSON" "$ALERT_TXT" "$LATEST_MASTER" <<'PY'
from pathlib import Path
import json
import datetime
import sys

json_path = Path(sys.argv[1])
alert_path = Path(sys.argv[2])
report_path = Path(sys.argv[3])

json_path.write_text(json.dumps({
    "status": "attention",
    "attention": True,
    "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    "report": str(report_path),
    "message": alert_path.read_text(errors="replace")
}, indent=2))
PY

if [ "$WATCHDOG_ALERTS_ENABLED" != "1" ]; then
  echo "Attention found, but alerts are disabled."
  exit 0
fi

if [ "$WATCHDOG_ALERT_ON_CHANGE_ONLY" = "1" ] && [ "$ATTENTION_HASH" = "$LAST_HASH" ]; then
  echo "Attention unchanged; not sending duplicate HA notification."
  exit 0
fi

if [ ! -f "$HA_ENV" ]; then
  echo "HA env file not found: $HA_ENV"
  exit 0
fi

source "$HA_ENV"

if [ -z "${HA_BASE_URL:-}" ] || [ -z "${HA_TOKEN:-}" ]; then
  echo "HA_BASE_URL or HA_TOKEN missing."
  exit 0
fi

PAYLOAD="$(
  python3 - "$WATCHDOG_NOTIFICATION_ID" "$ATTENTION_TEXT" <<'PY'
import json
import sys

print(json.dumps({
    "notification_id": sys.argv[1],
    "title": "AI Watchdog Attention Needed",
    "message": sys.argv[2]
}))
PY
)"

curl -fsS -X POST \
  -H "Authorization: Bearer $HA_TOKEN" \
  -H "Content-Type: application/json" \
  "$HA_BASE_URL/api/services/persistent_notification/create" \
  -d "$PAYLOAD" >/dev/null

echo "$ATTENTION_HASH" > "$ALERT_HASH_FILE"

echo "HA watchdog notification sent."
echo ""
cat "$ALERT_TXT"
