#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONF="$BASE/config/watchdog_backup_validation.conf"
HA_ENV="$BASE/config/ha_token.env"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/backup-validation/$STAMP"
REPORT="$BASE/reports/watchdog-backup-validation-$STAMP.md"
EXPORT_BASE="$BASE/backup-exports"

mkdir -p "$OUT" "$BASE/reports" "$EXPORT_BASE/nodered" "$EXPORT_BASE/watchdog-config"

BACKUP_MAX_HA_SUCCESS_AGE_HOURS="36"
BACKUP_NODE_RED_CONTAINER="nodered"
BACKUP_EXPECTED_PUBLIC_FILES="
dashboard.html
dashboard.json
latest.html
latest.md
watchdog-heartbeat.json
action-hints.json
alert.json
"

[ -f "$CONF" ] && source "$CONF"

ATTENTION="$OUT/attention-needed.txt"
: > "$ATTENTION"

add_attention() {
  echo "- $1" >> "$ATTENTION"
}

echo "# AI Watchdog Backup / Export Validation v1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Git validation
# ------------------------------------------------------------
{
  echo "## Git / GitHub Sync"
  echo ""
  echo '```'
} >> "$REPORT"

if git -C "$BASE" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$BASE" status --short > "$OUT/git-status-short.txt" 2>&1 || true
  git -C "$BASE" status -sb > "$OUT/git-status-branch.txt" 2>&1 || true
  git -C "$BASE" rev-parse --short HEAD > "$OUT/git-head.txt" 2>&1 || true
  git -C "$BASE" remote -v > "$OUT/git-remotes.txt" 2>&1 || true

  cat "$OUT/git-status-branch.txt" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "HEAD: $(cat "$OUT/git-head.txt" 2>/dev/null)" >> "$REPORT"
  echo "" >> "$REPORT"

  if [ -s "$OUT/git-status-short.txt" ]; then
    add_attention "ai-watchdog Git repo has uncommitted changes."
    echo "Uncommitted changes:" >> "$REPORT"
    cat "$OUT/git-status-short.txt" >> "$REPORT"
  else
    echo "Working tree clean." >> "$REPORT"
  fi

  UPSTREAM="$(git -C "$BASE" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  echo "" >> "$REPORT"
  echo "Upstream: ${UPSTREAM:-none}" >> "$REPORT"

  if [ -z "$UPSTREAM" ]; then
    add_attention "ai-watchdog Git repo has no upstream branch configured."
  else
    git -C "$BASE" fetch --quiet --prune origin >/dev/null 2>"$OUT/git-fetch-error.txt" || true

    if [ -s "$OUT/git-fetch-error.txt" ]; then
      add_attention "Git fetch from origin failed during backup validation."
      echo "Fetch error:" >> "$REPORT"
      cat "$OUT/git-fetch-error.txt" >> "$REPORT"
    fi

    COUNTS="$(git -C "$BASE" rev-list --left-right --count HEAD..."$UPSTREAM" 2>/dev/null || echo "unknown unknown")"
    AHEAD="$(echo "$COUNTS" | awk '{print $1}')"
    BEHIND="$(echo "$COUNTS" | awk '{print $2}')"

    echo "Ahead of upstream: $AHEAD" >> "$REPORT"
    echo "Behind upstream: $BEHIND" >> "$REPORT"

    if [ "$AHEAD" != "0" ]; then
      add_attention "ai-watchdog Git repo has local commits not pushed to upstream: ahead=$AHEAD."
    fi
  fi
else
  add_attention "ai-watchdog path is not a Git repo."
  echo "Not a Git repo." >> "$REPORT"
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# HA backup sensor validation
# ------------------------------------------------------------
echo "## Home Assistant Backup Freshness" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

if [ -f "$HA_ENV" ]; then
  source "$HA_ENV"

  python3 - "$HA_BASE_URL" "$HA_TOKEN" "$BACKUP_MAX_HA_SUCCESS_AGE_HOURS" "$OUT/ha-backup-status.json" "$ATTENTION" <<'PY'
import json
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

base_url = sys.argv[1].rstrip("/")
token = sys.argv[2]
max_age_hours = float(sys.argv[3])
out_path = sys.argv[4]
attention_path = sys.argv[5]

entities = [
    "sensor.backup_backup_manager_state",
    "sensor.backup_last_successful_automatic_backup",
    "sensor.backup_last_attempted_automatic_backup",
    "sensor.backup_next_scheduled_automatic_backup",
]

def add_attention(msg):
    with open(attention_path, "a") as f:
        f.write(f"- {msg}\n")

def get_state(entity_id):
    req = Request(
        f"{base_url}/api/states/{entity_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))

result = {
    "ok": True,
    "entities": {},
    "last_success_age_hours": None,
    "max_age_hours": max_age_hours,
    "errors": [],
}

for ent in entities:
    try:
        data = get_state(ent)
        result["entities"][ent] = {
            "state": data.get("state"),
            "last_changed": data.get("last_changed"),
            "last_updated": data.get("last_updated"),
            "friendly_name": data.get("attributes", {}).get("friendly_name"),
        }
    except Exception as e:
        result["ok"] = False
        result["errors"].append(f"{ent}: {repr(e)}")
        add_attention(f"Could not read HA backup sensor: {ent}")

last_success = result["entities"].get("sensor.backup_last_successful_automatic_backup", {}).get("state")

if not last_success or last_success in ("unknown", "unavailable", "none", "None"):
    result["ok"] = False
    add_attention("HA last successful automatic backup sensor is unavailable/unknown.")
else:
    try:
        dt = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600
        result["last_success_age_hours"] = round(age_hours, 2)
        if age_hours > max_age_hours:
            result["ok"] = False
            add_attention(f"HA last successful automatic backup is too old: {age_hours:.1f} hours.")
    except Exception as e:
        result["ok"] = False
        result["errors"].append(f"Could not parse last_success {last_success}: {repr(e)}")
        add_attention("Could not parse HA last successful automatic backup timestamp.")

with open(out_path, "w") as f:
    json.dump(result, f, indent=2)

print(json.dumps(result, indent=2))
PY

  cat "$OUT/ha-backup-status.json" >> "$REPORT" 2>/dev/null
else
  add_attention "HA token env file missing; cannot validate HA backup sensors."
  echo "HA token env file missing: $HA_ENV" >> "$REPORT"
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Node-RED sanitized export validation
# ------------------------------------------------------------
echo "## Node-RED Sanitized Flow Export" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

FLOW_FILE="$(
  docker exec "$BACKUP_NODE_RED_CONTAINER" sh -c '
    ls -1t /data/flows*.json 2>/dev/null | grep -v "_cred" | head -1
  ' 2>/dev/null || true
)"

echo "Node-RED container: $BACKUP_NODE_RED_CONTAINER" >> "$REPORT"
echo "Flow file: ${FLOW_FILE:-not found}" >> "$REPORT"

if [ -z "${FLOW_FILE:-}" ]; then
  add_attention "Could not find Node-RED flow file inside container."
else
  docker cp "$BACKUP_NODE_RED_CONTAINER:$FLOW_FILE" "$OUT/nodered-flows.raw.json" 2>"$OUT/nodered-docker-cp-error.txt" || true

  if [ ! -s "$OUT/nodered-flows.raw.json" ]; then
    add_attention "Could not copy Node-RED flow file for backup export."
    cat "$OUT/nodered-docker-cp-error.txt" >> "$REPORT" 2>/dev/null
  else
    python3 - "$OUT/nodered-flows.raw.json" "$OUT/nodered-flows.sanitized.json" "$OUT/nodered-tabs.txt" "$ATTENTION" <<'PY'
import json
import sys
from pathlib import Path

raw_path = Path(sys.argv[1])
san_path = Path(sys.argv[2])
tabs_path = Path(sys.argv[3])
attention_path = Path(sys.argv[4])

def add_attention(msg):
    with open(attention_path, "a") as f:
        f.write(f"- {msg}\n")

data = json.loads(raw_path.read_text(errors="replace"))

if not isinstance(data, list):
    add_attention("Node-RED flow file is not a JSON array.")
    data = []

sensitive_keys = {
    "credentials",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
    "authorization",
}

def sanitize(obj):
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            if k.lower() in sensitive_keys or any(word in k.lower() for word in ["token", "secret", "password", "passwd"]):
                clean[k] = "[REDACTED]"
            else:
                clean[k] = sanitize(v)
        return clean
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    return obj

sanitized = sanitize(data)
san_path.write_text(json.dumps(sanitized, indent=2, sort_keys=True) + "\n")

tabs = sorted([n.get("label", "") for n in data if isinstance(n, dict) and n.get("type") == "tab"])
tabs_path.write_text("\n".join(tabs) + ("\n" if tabs else ""))

if not tabs:
    add_attention("Node-RED sanitized export found zero tabs.")
PY

    cp "$OUT/nodered-flows.sanitized.json" "$EXPORT_BASE/nodered/flows.sanitized.latest.json"
    cp "$OUT/nodered-tabs.txt" "$EXPORT_BASE/nodered/tabs.latest.txt"

    sha256sum "$EXPORT_BASE/nodered/flows.sanitized.latest.json" > "$EXPORT_BASE/nodered/flows.sanitized.latest.sha256"

    echo "Sanitized export: $EXPORT_BASE/nodered/flows.sanitized.latest.json" >> "$REPORT"
    echo "SHA256:" >> "$REPORT"
    cat "$EXPORT_BASE/nodered/flows.sanitized.latest.sha256" >> "$REPORT"
    echo "" >> "$REPORT"
    echo "Tabs:" >> "$REPORT"
    cat "$OUT/nodered-tabs.txt" >> "$REPORT"

    # Remove raw local copy after sanitizing.
    rm -f "$OUT/nodered-flows.raw.json"
  fi
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Safe watchdog config snapshot
# ------------------------------------------------------------
echo "## Safe Watchdog Config Snapshot" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

SAFE_TAR="$EXPORT_BASE/watchdog-config/watchdog-safe-config-latest.tar.gz"

tar -C "$BASE" \
  --exclude='config/ha_token.env' \
  --exclude='config/*token*' \
  --exclude='config/*secret*' \
  --exclude='config/*.env' \
  --exclude='backup-exports' \
  --exclude='reports' \
  --exclude='snapshots' \
  --exclude='logs' \
  --exclude='public' \
  --exclude='.git' \
  -czf "$SAFE_TAR" \
  config scripts .gitignore README.md 2>"$OUT/tar-error.txt" || true

if [ ! -s "$SAFE_TAR" ]; then
  add_attention "Safe watchdog config tarball was not created."
  echo "Safe tarball failed." >> "$REPORT"
  cat "$OUT/tar-error.txt" >> "$REPORT" 2>/dev/null
else
  sha256sum "$SAFE_TAR" > "$SAFE_TAR.sha256"
  echo "Safe config tarball: $SAFE_TAR" >> "$REPORT"
  echo "SHA256:" >> "$REPORT"
  cat "$SAFE_TAR.sha256" >> "$REPORT"
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Public/dashboard artifact validation
# ------------------------------------------------------------
echo "## Public Dashboard Artifacts" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

for f in $BACKUP_EXPECTED_PUBLIC_FILES; do
  path="$BASE/public/$f"
  if [ -s "$path" ]; then
    echo "OK: $f" >> "$REPORT"
  else
    echo "MISSING: $f" >> "$REPORT"
    add_attention "Expected public watchdog artifact missing or empty: $f"
  fi
done

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Attention
# ------------------------------------------------------------
echo "## Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"

if [ -s "$ATTENTION" ]; then
  cat "$ATTENTION" >> "$REPORT"
else
  echo "No backup/export validation attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Backup validation snapshot saved to: $OUT"
echo "Backup validation report saved to:   $REPORT"
