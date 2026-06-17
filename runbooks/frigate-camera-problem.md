# Frigate Camera Problem

Frigate is reachable, but an expected camera is missing, stalled, or latest snapshot failed.

Check stats:

curl -s http://10.0.0.35:5000/api/stats | python3 -m json.tool

Test expected camera snapshots:

for cam in driveway backdoor kitchen front_door; do
  echo "=== $cam ==="
  curl -s -o /dev/null -w "%{http_code} %{content_type}\n" \
    "http://10.0.0.35:5000/api/$cam/latest.jpg?h=300"
done

Check recent Frigate errors:

docker logs --since 15m frigate 2>&1 | grep -Ei 'error|warn|ffmpeg|rtsp|timeout|404|unable|crash' | tail -100

Rerun camera watchdog:

~/ai-watchdog/scripts/watchdog_frigate_v1.sh
grep -A40 "## Attention Needed" "$(ls -t ~/ai-watchdog/reports/watchdog-frigate-*.md | head -1)"

If a camera was intentionally disabled:

nano ~/ai-watchdog/config/frigate_critical_cameras.txt
