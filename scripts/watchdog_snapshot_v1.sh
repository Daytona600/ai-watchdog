#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/main-server/$STAMP"
REPORT="$BASE/reports/health-report-$STAMP.md"

mkdir -p "$OUT"

echo "# AI Watchdog Health Report" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

echo "## System" >> "$REPORT"
{
  echo '```'
  hostnamectl 2>/dev/null || true
  echo
  uptime
  echo
  df -h
  echo '```'
} >> "$REPORT"

echo "Collecting Docker state..."
docker ps > "$OUT/docker-ps.txt" 2>&1
docker ps --format '{{json .}}' > "$OUT/docker-ps.jsonl" 2>&1
docker images > "$OUT/docker-images.txt" 2>&1

echo "## Docker Containers" >> "$REPORT"
{
  echo '```'
  docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  echo '```'
} >> "$REPORT"

echo "Collecting Docker health details..."
docker ps --format '{{.Names}}' > "$OUT/container-names.txt" 2>/dev/null || true

mkdir -p "$OUT/container-inspect"
while read -r c; do
  [ -z "$c" ] && continue
  docker inspect "$c" > "$OUT/container-inspect/$c.json" 2>/dev/null || true
done < "$OUT/container-names.txt"

echo "Collecting Ollama models..."
curl -s http://10.0.0.35:11434/api/tags > "$OUT/ollama-models.json" || true

echo "## Ollama" >> "$REPORT"
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
curl -s http://10.0.0.35:6333/collections > "$OUT/qdrant-collections.json" || true

echo "## Qdrant" >> "$REPORT"
{
  echo '```'
  cat "$OUT/qdrant-collections.json"
  echo '```'
} >> "$REPORT"

echo "Checking known service URLs..."
{
  echo "Node-RED http://10.0.0.35:1880"
  curl -I -s --max-time 5 http://10.0.0.35:1880 | head -5 || true
  echo

  echo "Open WebUI http://10.0.0.35:3000"
  curl -I -s --max-time 5 http://10.0.0.35:3000 | head -5 || true
  echo

  echo "Frigate http://10.0.0.35:5000"
  curl -I -s --max-time 5 http://10.0.0.35:5000 | head -5 || true
  echo

  echo "SearXNG http://10.0.0.35:8181"
  curl -I -s --max-time 5 http://10.0.0.35:8181 | head -5 || true
  echo

  echo "Qdrant http://10.0.0.35:6333"
  curl -I -s --max-time 5 http://10.0.0.35:6333 | head -5 || true
  echo
} > "$OUT/service-checks.txt"

echo "## Service Checks" >> "$REPORT"
{
  echo '```'
  cat "$OUT/service-checks.txt"
  echo '```'
} >> "$REPORT"

echo "Collecting GPU status..."
nvidia-smi > "$OUT/nvidia-smi.txt" 2>&1 || true

echo "## GPU Status" >> "$REPORT"
{
  echo '```'
  cat "$OUT/nvidia-smi.txt"
  echo '```'
} >> "$REPORT"

echo "Checking NAS reachability..."
{
  echo "NAS 10.0.0.100:"
  ping -c 2 -W 2 10.0.0.100 || true
  echo
  echo "NAS 10.0.0.6:"
  ping -c 2 -W 2 10.0.0.6 || true
} > "$OUT/nas-ping.txt"

echo "## NAS Reachability" >> "$REPORT"
{
  echo '```'
  cat "$OUT/nas-ping.txt"
  echo '```'
} >> "$REPORT"

echo "Collecting recent Docker logs summary..."
mkdir -p "$OUT/logs"
for c in nodered frigate ollama qdrant searxng parakeet-stt faster-whisper-gpu wyoming-piper-mary wyoming-piper-david local-mcp-agent mosquitto adguardhome caddy-ha; do
  docker logs --tail 200 "$c" > "$OUT/logs/$c.log" 2>&1 || true
done

echo "## Recent Error Hints" >> "$REPORT"
{
  echo '```'
  grep -RniE "error|failed|exception|traceback|unhealthy|timeout|refused|denied" "$OUT/logs" | head -100 || echo "No obvious recent errors found in selected logs."
  echo '```'
} >> "$REPORT"

echo "" >> "$REPORT"
echo "Snapshot folder:" >> "$REPORT"
echo "\`$OUT\`" >> "$REPORT"

echo "Done."
echo "Snapshot saved to: $OUT"
echo "Report saved to:   $REPORT"
