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
BEDROOM_LUNA_IP="${BEDROOM_LUNA_IP:-10.0.0.123}"

ROOT_DISK_WARN_PERCENT="${ROOT_DISK_WARN_PERCENT:-80}"
NAS_WARN_PERCENT="${NAS_WARN_PERCENT:-80}"
GPU_VRAM_WARN_PERCENT="${GPU_VRAM_WARN_PERCENT:-90}"

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
docker ps > "$OUT/docker-ps.txt" 2>&1
docker ps --format '{{json .}}' > "$OUT/docker-ps.jsonl" 2>&1
docker images > "$OUT/docker-images.txt" 2>&1
docker ps --format '{{.Names}}' > "$OUT/container-names.txt" 2>/dev/null || true

section "Docker Containers"
{
  echo '```'
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
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

docker ps --format '{{.Names}} {{.Status}}' | grep -Ei 'unhealthy|restarting|exited|dead' > "$OUT/docker-problems.txt" || true
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

echo "Collecting Qdrant collections..."
curl -s --max-time 5 "http://$MAIN_SERVER_IP:6333/collections" > "$OUT/qdrant-collections.json" || true

section "Qdrant Collections"
{
  echo '```'
  if command -v jq >/dev/null 2>&1; then
    jq -r '.result.collections[]?.name' "$OUT/qdrant-collections.json" 2>/dev/null || cat "$OUT/qdrant-collections.json"
  else
    cat "$OUT/qdrant-collections.json"
  fi
  echo '```'
} >> "$REPORT"

if ! grep -q '"status":"ok"' "$OUT/qdrant-collections.json"; then
  add_attention "Qdrant /collections check did not return expected OK status."
fi

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
check_url "Frigate" "http://$MAIN_SERVER_IP:5000"
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

echo "Collecting recent Docker logs..."
important_containers="
nodered
frigate
ollama
qdrant
searxng
parakeet-stt
faster-whisper-gpu
wyoming-openwakeword
wyoming-piper-mary
wyoming-piper-david
local-mcp-agent
mosquitto
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
