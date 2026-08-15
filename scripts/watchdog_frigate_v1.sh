#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONF="$BASE/config/frigate_watchdog.conf"
CAMERA_LIST="$BASE/config/frigate_critical_cameras.txt"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/frigate/$STAMP"
REPORT="$BASE/reports/watchdog-frigate-$STAMP.md"

mkdir -p "$OUT" "$BASE/reports"

FRIGATE_BASE_URL="http://10.0.0.35:5000"
FRIGATE_WARN_ZERO_FPS="1"
FRIGATE_HTTP_TIMEOUT="8"
FRIGATE_ALLOW_ZERO_CAMERA_FPS=""

[ -f "$CONF" ] && source "$CONF"

ATTENTION="$OUT/attention-needed.txt"
: > "$ATTENTION"

add_attention() {
  echo "- $1" >> "$ATTENTION"
}

echo "# AI Watchdog Frigate Camera Report v1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "Frigate URL: $FRIGATE_BASE_URL" >> "$REPORT"
echo "" >> "$REPORT"

python3 - "$FRIGATE_BASE_URL" "$FRIGATE_HTTP_TIMEOUT" "$CAMERA_LIST" "$OUT" "$ATTENTION" "$FRIGATE_ALLOW_ZERO_CAMERA_FPS" <<'PY'
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote
import json
import sys
import time
import traceback

base_url = sys.argv[1].rstrip("/")
timeout = int(sys.argv[2])
camera_list_path = Path(sys.argv[3])
out = Path(sys.argv[4])
attention = Path(sys.argv[5])
allow_zero_camera_fps = {
    x.strip()
    for x in sys.argv[6].replace(",", " ").split()
    if x.strip()
}

def add_attention(msg: str) -> None:
    with attention.open("a") as f:
        f.write(f"- {msg}\n")

def fetch_json(path: str):
    url = base_url + path
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        body = r.read()
        return json.loads(body.decode("utf-8", errors="replace"))

def fetch_status(path: str):
    url = base_url + path
    req = Request(url, headers={"User-Agent": "ai-watchdog/1.0"})
    started = time.time()
    try:
        with urlopen(req, timeout=timeout) as r:
            # Read a small amount only; enough to prove endpoint works.
            chunk = r.read(2048)
            return {
                "ok": 200 <= r.status < 300,
                "status": r.status,
                "content_type": r.headers.get("Content-Type", ""),
                "bytes_sampled": len(chunk),
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": "",
            }
    except HTTPError as e:
        return {
            "ok": False,
            "status": e.code,
            "content_type": "",
            "bytes_sampled": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(e),
        }
    except URLError as e:
        return {
            "ok": False,
            "status": None,
            "content_type": "",
            "bytes_sampled": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": str(e),
        }
    except Exception as e:
        return {
            "ok": False,
            "status": None,
            "content_type": "",
            "bytes_sampled": 0,
            "elapsed_ms": int((time.time() - started) * 1000),
            "error": repr(e),
        }

def to_float(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None

def pick_num(d, names):
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d:
            val = to_float(d.get(n))
            if val is not None:
                return val
    return None

def flatten_numbers(prefix, obj, found):
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten_numbers(f"{prefix}.{k}" if prefix else str(k), v, found)
    elif isinstance(obj, (int, float)):
        found[prefix] = obj

# Load expected cameras.
expected = []
if camera_list_path.exists():
    for raw in camera_list_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            expected.append(line)

api_status = {
    "stats_ok": False,
    "config_ok": False,
    "stats_error": "",
    "config_error": "",
}

try:
    stats = fetch_json("/api/stats")
    api_status["stats_ok"] = True
    (out / "stats.json").write_text(json.dumps(stats, indent=2, sort_keys=True))
except Exception as e:
    stats = {}
    api_status["stats_error"] = repr(e)
    add_attention(f"Frigate /api/stats is not reachable or not valid JSON: {e}")

try:
    config = fetch_json("/api/config")
    api_status["config_ok"] = True
    (out / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
except Exception as e:
    config = {}
    api_status["config_error"] = repr(e)
    # Not fatal; stats is enough for health.
    (out / "config-error.txt").write_text(traceback.format_exc())

(out / "api-status.json").write_text(json.dumps(api_status, indent=2, sort_keys=True))

stats_cameras = stats.get("cameras", {}) if isinstance(stats, dict) else {}
config_cameras = config.get("cameras", {}) if isinstance(config, dict) else {}

stats_camera_names = sorted(stats_cameras.keys())
config_camera_names = sorted(config_cameras.keys())

if not expected:
    expected = config_camera_names or stats_camera_names

(out / "cameras-expected.txt").write_text("\n".join(expected) + ("\n" if expected else ""))
(out / "cameras-from-stats.txt").write_text("\n".join(stats_camera_names) + ("\n" if stats_camera_names else ""))
(out / "cameras-from-config.txt").write_text("\n".join(config_camera_names) + ("\n" if config_camera_names else ""))

camera_rows = []
snapshot_rows = []

for cam in expected:
    row = {
        "camera": cam,
        "in_stats": cam in stats_cameras,
        "in_config": cam in config_cameras if config_cameras else None,
        "camera_fps": None,
        "process_fps": None,
        "detection_fps": None,
        "skipped_fps": None,
        "ffmpeg_pid": None,
        "capture_pid": None,
        "pid": None,
        "problems": [],
    }

    cam_stats = stats_cameras.get(cam)

    if cam_stats is None:
        row["problems"].append("missing from /api/stats")
        add_attention(f"Frigate camera missing from stats: {cam}")
    else:
        numbers = {}
        flatten_numbers("", cam_stats, numbers)

        # Common Frigate stats fields vary by version; try several.
        row["camera_fps"] = pick_num(cam_stats, ["camera_fps", "capture_fps", "fps"])
        row["process_fps"] = pick_num(cam_stats, ["process_fps"])
        row["detection_fps"] = pick_num(cam_stats, ["detection_fps"])
        row["skipped_fps"] = pick_num(cam_stats, ["skipped_fps"])

        row["ffmpeg_pid"] = cam_stats.get("ffmpeg_pid") or cam_stats.get("ffmpeg_pid_0")
        row["capture_pid"] = cam_stats.get("capture_pid")
        row["pid"] = cam_stats.get("pid")

        # If direct keys were absent, fall back to flattened matching.
        if row["camera_fps"] is None:
            for k, v in numbers.items():
                if k.endswith("camera_fps") or k.endswith("capture_fps"):
                    row["camera_fps"] = to_float(v)
                    break

        if row["process_fps"] is None:
            for k, v in numbers.items():
                if k.endswith("process_fps"):
                    row["process_fps"] = to_float(v)
                    break

        if row["detection_fps"] is None:
            for k, v in numbers.items():
                if k.endswith("detection_fps"):
                    row["detection_fps"] = to_float(v)
                    break

        if row["skipped_fps"] is None:
            for k, v in numbers.items():
                if k.endswith("skipped_fps"):
                    row["skipped_fps"] = to_float(v)
                    break

        camera_zero_allowed = cam in allow_zero_camera_fps

        if row["camera_fps"] == 0:
            if camera_zero_allowed:
                row["problems"].append("camera_fps is 0, allowed by config")
            else:
                row["problems"].append("camera_fps is 0")

        if row["process_fps"] == 0:
            row["problems"].append("process_fps is 0")

        if row["camera_fps"] == 0 and row["process_fps"] == 0:
            if camera_zero_allowed:
                add_attention(f"Frigate camera may be stalled: {cam} has camera_fps=0 and process_fps=0 even though camera_fps zero is allowed")
            else:
                add_attention(f"Frigate camera appears stalled: {cam} has camera_fps=0 and process_fps=0")
        elif row["camera_fps"] == 0:
            if not camera_zero_allowed:
                add_attention(f"Frigate camera capture FPS is zero: {cam}")
        elif row["process_fps"] == 0:
            add_attention(f"Frigate camera process FPS is zero: {cam}")

    snap_path = f"/api/{quote(cam, safe='')}/latest.jpg?h=300"
    snap = fetch_status(snap_path)
    snap["camera"] = cam
    snapshot_rows.append(snap)

    if not snap["ok"]:
        add_attention(f"Frigate latest snapshot failed for {cam}: HTTP {snap['status']} {snap['error']}")

    camera_rows.append(row)

(out / "camera-health.json").write_text(json.dumps(camera_rows, indent=2, sort_keys=True))
(out / "snapshot-checks.json").write_text(json.dumps(snapshot_rows, indent=2, sort_keys=True))

detectors = stats.get("detectors", {}) if isinstance(stats, dict) else {}
(out / "detectors.json").write_text(json.dumps(detectors, indent=2, sort_keys=True))

# Write text summaries for the markdown report.
with (out / "camera-health.txt").open("w") as f:
    for row in camera_rows:
        f.write(f"{row['camera']}\n")
        f.write(f"  in_stats: {row['in_stats']}\n")
        f.write(f"  in_config: {row['in_config']}\n")
        f.write(f"  camera_fps: {row['camera_fps']}\n")
        f.write(f"  process_fps: {row['process_fps']}\n")
        f.write(f"  detection_fps: {row['detection_fps']}\n")
        f.write(f"  skipped_fps: {row['skipped_fps']}\n")
        if row["problems"]:
            f.write(f"  problems: {', '.join(row['problems'])}\n")
        else:
            f.write("  problems: none\n")
        f.write("\n")

with (out / "snapshot-checks.txt").open("w") as f:
    for row in snapshot_rows:
        f.write(
            f"{row['camera']}: ok={row['ok']} "
            f"http={row['status']} type={row['content_type']} "
            f"bytes={row['bytes_sampled']} elapsed_ms={row['elapsed_ms']} "
            f"error={row['error']}\n"
        )

with (out / "detectors.txt").open("w") as f:
    if detectors:
        for name, d in detectors.items():
            f.write(f"{name}: {json.dumps(d, sort_keys=True)}\n")
    else:
        f.write("No detector data found in /api/stats.\n")
PY

echo "## API Status" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/api-status.json" 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Expected Cameras" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/cameras-expected.txt" 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Cameras From Frigate Stats" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/cameras-from-stats.txt" 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Camera Health" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/camera-health.txt" 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Latest Snapshot Checks" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/snapshot-checks.txt" 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Detector Summary" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"
cat "$OUT/detectors.txt" 2>/dev/null >> "$REPORT"
echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$ATTENTION" ]; then
  cat "$ATTENTION" >> "$REPORT"
else
  echo "No Frigate camera attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Frigate snapshot saved to: $OUT"
echo "Frigate report saved to:   $REPORT"
