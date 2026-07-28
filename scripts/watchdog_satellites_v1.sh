#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONFIG="$BASE/config/watchdog_known_hosts.conf"

if [ -f "$CONFIG" ]; then
  # shellcheck disable=SC1090
  source "$CONFIG"
fi

LIVING_ROOM_IP="${LIVING_ROOM_IP:-10.0.0.71}"
BEDROOM_LUNA_IP="${BEDROOM_LUNA_IP:-10.0.0.123}"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/satellites/$STAMP"
REPORT="$BASE/reports/watchdog-satellites-$STAMP.md"

mkdir -p "$OUT" "$BASE/reports"

ATTENTION="$OUT/attention-needed.txt"
: > "$ATTENTION"

add_attention() {
  echo "- $1" >> "$ATTENTION"
}

echo "# AI Watchdog Satellites Report v1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

# SSH options for the dedicated watchdog-to-satellite key.
SSH_OPTS="-i $HOME/.ssh/watchdog_satellite_ed25519 -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no"

check_satellite() {
  local label="$1"
  local ip="$2"
  local out_file="$OUT/${label// /_}.txt"

  echo "## $label ($ip)" >> "$REPORT"
  echo "" >> "$REPORT"

  if ! ssh $SSH_OPTS "david@$ip" "echo reachable" > /dev/null 2>&1; then
    add_attention "$label ($ip) is not reachable over SSH."
    echo "SSH to $ip failed." >> "$REPORT"
    echo "" >> "$REPORT"
    return
  fi

  {
    echo "--- docker ps ---"
    ssh $SSH_OPTS "david@$ip" "docker ps --format 'table {{.Names}}\t{{.Status}}'" 2>&1
    echo ""
    echo "--- wakeword-control.service ---"
    ssh $SSH_OPTS "david@$ip" "systemctl is-active wakeword-control 2>&1"
    echo ""
    echo "--- audio-stream.service (user) ---"
    ssh $SSH_OPTS "david@$ip" "systemctl --user is-active audio-stream 2>&1"
  } > "$out_file" 2>&1

  echo '```' >> "$REPORT"
  cat "$out_file" >> "$REPORT"
  echo '```' >> "$REPORT"
  echo "" >> "$REPORT"

  # Flag problems.
  if ! grep -qE '^satellite\s+Up' "$out_file"; then
    add_attention "$label: 'satellite' Docker container is not Up."
  fi
  if ! grep -qE '^openwakeword\s+Up' "$out_file"; then
    add_attention "$label: 'openwakeword' Docker container is not Up."
  fi
  if grep -qEi 'restarting|exited|dead|unhealthy' "$out_file"; then
    add_attention "$label: one or more Docker containers show restarting/exited/dead/unhealthy status."
  fi
  if ! grep -q "^active$" <(ssh $SSH_OPTS "david@$ip" "systemctl is-active wakeword-control 2>&1"); then
    add_attention "$label: wakeword-control.service is not active."
  fi
  if ! grep -q "^active$" <(ssh $SSH_OPTS "david@$ip" "systemctl --user is-active audio-stream 2>&1"); then
    add_attention "$label: audio-stream.service (user) is not active."
  fi
}

echo "Checking living room satellite..."
check_satellite "Living Room" "$LIVING_ROOM_IP"

echo "Checking bedroom satellite..."
check_satellite "Bedroom" "$BEDROOM_LUNA_IP"

echo "## Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$ATTENTION" ]; then
  cat "$ATTENTION" >> "$REPORT"
else
  echo "No satellite attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Satellites snapshot saved to: $OUT"
echo "Satellites report saved to:   $REPORT"
