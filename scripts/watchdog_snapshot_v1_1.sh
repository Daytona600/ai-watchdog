#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONFIG="$BASE/config/watchdog_known_hosts.conf"

if [ -f "$CONFIG" ]; then
  # shellcheck disable=SC1090
  source "$CONFIG"
fi

MAIN_SERVER_IP="${MAIN_SERVER_IP:-10.0.0.35}"
NAS_PRIMARY="${NAS_PRIMARY:-10.0.0.100}"
NAS_SECONDARY="${NAS_SECONDARY:-10.0.0.6}"
BEDROOM_LUNA_IP="${BEDROOM_LUNA_IP:-10.0.0.214}"
FRIGATE_HOST_IP="${FRIGATE_HOST_IP:-10.0.0.85}"

ROOT_DISK_WARN_PERCENT="${ROOT_DISK_WARN_PERCENT:-80}"
NAS_WARN_PERCENT="${NAS_WARN_PERCENT:-80}"
GPU_VRAM_WARN_PERCENT="${GPU_VRAM_WARN_PERCENT:-90}"
SOLAR_RAW_STALE_MINUTES_WARN="${SOLAR_RAW_STALE_MINUTES_WARN:-30}"
SOLAR_ROLLUP_STALE_DAYS_WARN="${SOLAR_ROLLUP_STALE_DAYS_WARN:-2}"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/main-server/$STAMP"
REPORT="$BASE/reports/health-report-$STAMP.md"

mkdir -p "$OUT"/{logs-current,logs-tail}

echo "# AI Watchdog Health Report v1.1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "Snapshot folder: \`$OUT\`" >> "$REPORT"
echo "" >> "$REPORT"

attention_file="$OUT/attention-needed.txt"
touch "$attention_file"

add_attention() {
  echo "- $1" >> "$attention_file"
}

section() {
  echo "" >> "$REPORT"
  echo "## $1" >> "$REPORT"
  echo "" >> "$REPORT"
}

codeblock_file() {
  echo '```' >> "$REPORT"
  cat "$1" >> "$REPORT"
  echo '```' >> "$REPORT"
}

echo "Collecting system state..."
{
  hostnamectl 2>/dev/null || true
  echo
  uptime
  echo
  df -h
} > "$OUT/system.txt"

section "System"
codeblock_file "$OUT/system.txt"

root_use="$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')"
if [ "${root_use:-0}" -ge "$ROOT_DISK_WARN_PERCENT" ]; then
  add_attention "Root disk is ${root_use}% used. Warning threshold is ${ROOT_DISK_WARN_PERCENT}%."
fi

echo "Collecting Docker state..."
docker ps -a > "$OUT/docker-ps.txt" 2>&1
docker ps -a --format '{{json .}}' > "$OUT/docker-ps.jsonl" 2>&1
docker images > "$OUT/docker-images.txt" 2>&1
docker ps -a --format '{{.Names}}' > "$OUT/container-names.txt" 2>/dev/null || true

section "Docker Containers"
{
  echo '```'
  docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  echo '```'
} >> "$REPORT"

: > "$OUT/container-summary.txt"
while read -r c; do
  [ -z "$c" ] && continue

  # Safe docker inspect summary.
  # Do NOT save raw docker inspect JSON because it can include environment secrets.
  docker inspect \
    --format '{{.Name}} Image={{.Config.Image}} Running={{.State.Running}} RestartPolicy={{.HostConfig.RestartPolicy.Name}} StartedAt={{.State.StartedAt}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    "$c" >> "$OUT/container-summary.txt" 2>/dev/null || true
done < "$OUT/container-names.txt"

docker ps -a --format '{{.Names}} {{.Status}}' | grep -Ei 'unhealthy|restarting|exited|dead' > "$OUT/docker-problems.txt" || true
if [ -s "$OUT/docker-problems.txt" ]; then
  add_attention "One or more Docker containers may be unhealthy, restarting, exited, or dead."
fi

echo "Collecting Ollama models..."
curl -s --max-time 5 "http://$MAIN_SERVER_IP:11434/api/tags" > "$OUT/ollama-models.json" || true

section "Ollama Models"
{
  echo '```'
  if command -v jq >/dev/null 2>&1; then
    jq -r '.models[]?.name' "$OUT/ollama-models.json" 2>/dev/null || echo "Could not parse Ollama model list"
  else
    cat "$OUT/ollama-models.json"
  fi
  echo '```'
} >> "$REPORT"

echo "Checking service URLs..."
services_file="$OUT/service-checks.txt"
: > "$services_file"

check_url() {
  local name="$1"
  local url="$2"
  echo "$name $url" >> "$services_file"
  code="$(curl -o /dev/null -s -w "%{http_code}" --max-time 5 "$url" || echo "000")"
  echo "HTTP $code" >> "$services_file"
  echo "" >> "$services_file"

  case "$code" in
    200|301|302|401|403|404)
      ;;
    *)
      add_attention "$name returned HTTP $code at $url."
      ;;
  esac
}

check_url "Node-RED" "http://$MAIN_SERVER_IP:1880"
check_url "Open WebUI" "http://$MAIN_SERVER_IP:3000"
check_url "Frigate" "http://$FRIGATE_HOST_IP:5000"
check_url "SearXNG" "http://$MAIN_SERVER_IP:8181"
check_url "Qdrant Collections" "http://$MAIN_SERVER_IP:6333/collections"
check_url "Ollama Tags" "http://$MAIN_SERVER_IP:11434/api/tags"
check_url "Local MCP Agent" "http://$MAIN_SERVER_IP:3997"
check_url "Memory Router" "http://$MAIN_SERVER_IP:3999"

section "Service Checks"
codeblock_file "$services_file"

echo "Collecting GPU status..."
nvidia-smi > "$OUT/nvidia-smi.txt" 2>&1 || true
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader,nounits > "$OUT/gpu-summary.csv" 2>/dev/null || true

section "GPU Status"
codeblock_file "$OUT/nvidia-smi.txt"

while IFS=',' read -r idx name mem_used mem_total util temp; do
  idx="$(echo "$idx" | xargs)"
  name="$(echo "$name" | xargs)"
  mem_used="$(echo "$mem_used" | xargs)"
  mem_total="$(echo "$mem_total" | xargs)"
  [ -z "$mem_used" ] && continue
  [ -z "$mem_total" ] && continue
  percent=$(( mem_used * 100 / mem_total ))
  if [ "$percent" -ge "$GPU_VRAM_WARN_PERCENT" ]; then
    add_attention "GPU $idx ($name) VRAM is ${percent}% used (${mem_used} MiB / ${mem_total} MiB)."
  fi
done < "$OUT/gpu-summary.csv"

echo "Checking NAS reachability and usage..."
nas_file="$OUT/nas-checks.txt"
: > "$nas_file"

check_nas() {
  local label="$1"
  local ip="$2"
  local mount_hint="$3"

  echo "$label $ip" >> "$nas_file"
  ping -c 2 -W 2 "$ip" >> "$nas_file" 2>&1 || add_attention "$label at $ip did not respond to ping."
  echo "" >> "$nas_file"

  if mount | grep -q "$ip"; then
    echo "Mounted paths:" >> "$nas_file"
    mount | grep "$ip" >> "$nas_file"
    echo "" >> "$nas_file"

    df -h | grep "$ip" >> "$nas_file" || true

    while read -r line; do
      usep="$(echo "$line" | awk '{gsub("%","",$5); print $5}')"
      mnt="$(echo "$line" | awk '{print $6}')"
      if [ "${usep:-0}" -ge "$NAS_WARN_PERCENT" ]; then
        add_attention "$label mount $mnt is ${usep}% used. Warning threshold is ${NAS_WARN_PERCENT}%."
      fi
    done < <(df -P | grep "$ip" || true)
  else
    add_attention "$label at $ip is reachable/pinged maybe, but no mounted filesystem was found."
  fi

  echo "----" >> "$nas_file"
}

check_nas "NAS primary/questionable" "$NAS_PRIMARY" "/mnt/frigate_nas"
check_nas "NAS secondary" "$NAS_SECONDARY" "/mnt/frigate_backup"

section "NAS Checks"
codeblock_file "$nas_file"

echo "Checking solar data pipeline freshness..."
solar_file="$OUT/solar-pipeline.txt"
: > "$solar_file"

# Data-freshness, not process-status: solar-collector/solar-rollups have
# both previously sat "active (running)" for days while silently ingesting
# or rolling up nothing (MQTT subscription and continuous-aggregate refresh
# can both fail without the service crashing or logging anything). Checking
# systemctl is-active here would have missed that exact incident, so this
# checks the actual data instead.
raw_age_min="$(docker exec postgres-main psql -U homelab -d homelab -tAc \
  "SELECT extract(epoch from (now() - max(time)))/60 FROM solar.readings;" 2>/dev/null | tr -d '[:space:]')"
rollup_age_days="$(docker exec postgres-main psql -U homelab -d homelab -tAc \
  "SELECT extract(epoch from (now() - max(day)))/86400 FROM solar.readings_daily;" 2>/dev/null | tr -d '[:space:]')"

{
  echo "Raw reading age (minutes): ${raw_age_min:-query failed}"
  echo "Rollup (readings_daily) age (days): ${rollup_age_days:-query failed}"
} > "$solar_file"

if [ -z "$raw_age_min" ] || ! awk "BEGIN{exit !($raw_age_min <= $SOLAR_RAW_STALE_MINUTES_WARN)}"; then
  add_attention "Solar data pipeline: no new rows in solar.readings for ${raw_age_min:-an unknown number of} minutes (warning threshold ${SOLAR_RAW_STALE_MINUTES_WARN}m). Check 'systemctl status solar-collector' - it can report active while silently not ingesting."
fi

if [ -z "$rollup_age_days" ] || ! awk "BEGIN{exit !($rollup_age_days <= $SOLAR_ROLLUP_STALE_DAYS_WARN)}"; then
  add_attention "Solar data pipeline: solar.readings_daily rollup hasn't advanced in ${rollup_age_days:-an unknown number of} days (warning threshold ${SOLAR_ROLLUP_STALE_DAYS_WARN}d). Check 'systemctl status solar-rollups' and force a continuous-aggregate refresh."
fi

section "Solar Data Pipeline"
codeblock_file "$solar_file"

echo "Checking voice re-seed after reboot..."
voice_file="$OUT/voice-reboot-check.txt"
: > "$voice_file"

# Node-RED's VOICE_OVERRIDES global is memory-only and gets re-seeded from
# input_select.david_voice/mary_voice on every Node-RED start (see
# vs_init_fn in the Home_AI_system flow, fixed 2026-08-20 to retry for ~2min
# instead of giving up after one 2s attempt, and to record the outcome to
# /data/voice_seed_status.json - docker logs on this instance rotate out
# within under an hour given the conversation volume, so a check that reads
# logs instead of this status file would be unreliable). This only matters
# right after a reboot, so: remember the last boot time we checked, and only
# actually verify anything when the current boot time differs from it.
VOICE_STATE_FILE="$BASE/state/voice_reboot_watch.json"
VOICE_SEED_STATUS_FILE="/home/davids/node-red/data/voice_seed_status.json"
CURRENT_BOOT_EPOCH="$(date -d "$(uptime -s 2>/dev/null)" +%s 2>/dev/null || echo "")"

LAST_BOOT_EPOCH=""
if [ -f "$VOICE_STATE_FILE" ]; then
  LAST_BOOT_EPOCH="$(python3 -c "
import json
try:
    print(json.load(open('$VOICE_STATE_FILE')).get('last_known_boot_epoch',''))
except Exception:
    print('')
" 2>/dev/null)"
fi

if [ -n "$CURRENT_BOOT_EPOCH" ] && [ -n "$LAST_BOOT_EPOCH" ] && [ "$CURRENT_BOOT_EPOCH" != "$LAST_BOOT_EPOCH" ]; then
  echo "Reboot detected: last checked boot epoch $LAST_BOOT_EPOCH, current $CURRENT_BOOT_EPOCH" >> "$voice_file"

  david_current=""
  if [ -f "$HOME/ai-watchdog/config/ha_token.env" ]; then
    source "$HOME/ai-watchdog/config/ha_token.env"
    david_current="$(curl -fsS --max-time 8 -H "Authorization: Bearer $HA_TOKEN" "$HA_BASE_URL/api/states/input_select.david_voice" 2>/dev/null | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('state', ''))
except Exception:
    print('')
" 2>/dev/null)"
  fi

  check_result="$(python3 -c "
import json
from datetime import datetime, timezone

status_path = '$VOICE_SEED_STATUS_FILE'
boot_epoch = $CURRENT_BOOT_EPOCH
david_current = '$david_current'

try:
    status = json.load(open(status_path))
except Exception as e:
    print(f'NO_STATUS_FILE|could not read {status_path}: {e}')
    raise SystemExit

seeded_at_raw = status.get('seeded_at', '')
try:
    seeded_epoch = datetime.fromisoformat(seeded_at_raw.replace('Z', '+00:00')).timestamp()
except Exception:
    seeded_epoch = 0

david_seeded = status.get('david', '')
fell_back = bool(status.get('fell_back_david'))

if seeded_epoch < boot_epoch:
    print(f'STALE|status file last seeded at {seeded_at_raw}, before this boot - the seed step may not have run yet or failed to write')
elif fell_back:
    print(f'FELL_BACK|seeded david={david_seeded} by falling back to a hardcoded default - HA was unreachable within the retry window')
elif david_current and david_seeded != david_current:
    print(f'MISMATCH|seeded david={david_seeded} but input_select.david_voice is now {david_current}')
else:
    print(f'OK|seeded david={david_seeded} at {seeded_at_raw}')
" 2>&1)"

  {
    echo "David voice per HA:   ${david_current:-unknown}"
    echo "Check result:         ${check_result}"
  } >> "$voice_file"

  case "$check_result" in
    OK\|*) : ;;
    NO_STATUS_FILE\|*) add_attention "Voice reboot check: ${check_result#*|}" ;;
    STALE\|*) add_attention "Voice reboot check: ${check_result#*|}" ;;
    FELL_BACK\|*) add_attention "Voice reboot check: ${check_result#*|}" ;;
    MISMATCH\|*) add_attention "Voice reboot check: ${check_result#*|}" ;;
    *) add_attention "Voice reboot check: could not evaluate seed status (${check_result})" ;;
  esac
else
  echo "No new reboot since the last check." >> "$voice_file"
fi

if [ -n "$CURRENT_BOOT_EPOCH" ]; then
  python3 -c "
import json
json.dump({'last_known_boot_epoch': '$CURRENT_BOOT_EPOCH', 'last_checked': '$(date -Iseconds)'}, open('$VOICE_STATE_FILE', 'w'), indent=2)
" 2>/dev/null
fi

section "Voice Reboot Check"
codeblock_file "$voice_file"

echo "Collecting recent Docker logs..."
important_containers="
nodered
frigate
ollama
searxng
parakeet-stt
kokoro-tts
wyoming-openwakeword
local-mcp-agent
cedalo_platform-mosquitto-1
adguardhome
caddy-ha
memory-router
command-parser
ai-planner
"

for c in $important_containers; do
  docker logs --since 5m --tail 300 "$c" > "$OUT/logs-current/$c.log" 2>&1 || true
  docker logs --tail 300 "$c" > "$OUT/logs-tail/$c.log" 2>&1 || true
done

error_regex='error|failed|exception|traceback|unhealthy|timeout|refused|denied|critical|fatal'

grep -RniE "$error_regex" "$OUT/logs-current" > "$OUT/current-error-lines.txt" || true
grep -RhiE "$error_regex" "$OUT/logs-current" \
  | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:.+-]+/<TIME>/g' \
  | sed -E 's/[0-9]{2}:[0-9]{2}:[0-9]{2}/<TIME>/g' \
  | sed -E 's/[0-9]+(\.[0-9]+)? ms/<MS>/g' \
  | sed -E 's/[0-9]+(\.[0-9]+)?s/<SECONDS>/g' \
  | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<IP>/g' \
  | sort | uniq -c | sort -nr | head -50 > "$OUT/current-error-summary.txt" || true

grep -RniE "$error_regex" "$OUT/logs-tail" > "$OUT/tail-error-lines.txt" || true
grep -RhiE "$error_regex" "$OUT/logs-tail" \
  | sed -E 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:.+-]+/<TIME>/g' \
  | sed -E 's/[0-9]{2}:[0-9]{2}:[0-9]{2}/<TIME>/g' \
  | sed -E 's/[0-9]+(\.[0-9]+)? ms/<MS>/g' \
  | sed -E 's/[0-9]+(\.[0-9]+)?s/<SECONDS>/g' \
  | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<IP>/g' \
  | sort | uniq -c | sort -nr | head -50 > "$OUT/tail-error-summary.txt" || true

if [ -s "$OUT/current-error-lines.txt" ]; then
  add_attention "Current error hints found in Docker logs from the last 15 minutes."
fi

if grep -R "10.0.0.123:10800" "$OUT/logs-current" >/dev/null 2>&1; then
  add_attention "Recent stale bedroom Luna/ThinkPad Node-RED connection to 10.0.0.123:10800 found within the watchdog log window. If no entries are newer than the fix time, this can be ignored. Expected bedroom Luna OpenWakeWord is 10.0.0.123:10400."
fi

section "Recent Error Summary - Last 5 Minutes"
if [ -s "$OUT/current-error-summary.txt" ]; then
  codeblock_file "$OUT/current-error-summary.txt"
else
  echo "No recent error hints found in the last 5 minutes." >> "$REPORT"
fi

section "Older Tail Error Summary"
if [ -s "$OUT/tail-error-summary.txt" ]; then
  codeblock_file "$OUT/tail-error-summary.txt"
else
  echo "No error hints found in the selected tail logs." >> "$REPORT"
fi

section "Attention Needed"
if [ -s "$attention_file" ]; then
  cat "$attention_file" >> "$REPORT"
else
  echo "No immediate attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Snapshot saved to: $OUT"
echo "Report saved to:   $REPORT"
