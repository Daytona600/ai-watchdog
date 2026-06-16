#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
PUBLIC="$BASE/public"

mkdir -p "$PUBLIC"

LATEST_MASTER="$(ls -t "$BASE"/reports/watchdog-master-*.md 2>/dev/null | head -1)"
LATEST_COMBINED="$(ls -t "$BASE"/reports/watchdog-combined-*.md 2>/dev/null | head -1)"
LATEST_STORAGE="$(ls -t "$BASE"/reports/watchdog-storage-*.md 2>/dev/null | head -1)"
LATEST_HA_DIFF="$(ls -t "$BASE"/reports/watchdog-diff-*.md 2>/dev/null | head -1)"
LATEST_SERVER_DIFF="$(ls -t "$BASE"/reports/watchdog-server-diff-*.md 2>/dev/null | head -1)"

if [ -z "${LATEST_MASTER:-}" ] || [ ! -f "$LATEST_MASTER" ]; then
  echo "No master report found."
  exit 1
fi

cp "$LATEST_MASTER" "$PUBLIC/latest.md"

{
  echo "# AI Watchdog Latest"
  echo ""
  echo "Generated: $(date)"
  echo ""
  echo "## Latest Files"
  echo ""
  echo "- Master: ${LATEST_MASTER:-not found}"
  echo "- Combined: ${LATEST_COMBINED:-not found}"
  echo "- Storage: ${LATEST_STORAGE:-not found}"
  echo "- HA diff: ${LATEST_HA_DIFF:-not found}"
  echo "- Server diff: ${LATEST_SERVER_DIFF:-not found}"
  echo ""
  echo "## Master Report"
  echo ""
  cat "$LATEST_MASTER"
} > "$PUBLIC/latest-full.md"

python3 - <<'PY'
from pathlib import Path
import html
import datetime

base = Path.home() / "ai-watchdog"
public = base / "public"
md = public / "latest-full.md"
html_file = public / "latest.html"
txt = md.read_text(errors="replace") if md.exists() else "No watchdog report found."

# Pull a compact status from the markdown.
attention = []
for line in txt.splitlines():
    if "GPU 1" in line or "No critical HA entity problems" in line or "No storage/NAS attention" in line:
        attention.append(line)

status_html = "\n".join(f"<li>{html.escape(x)}</li>" for x in attention[:10])
if not status_html:
    status_html = "<li>No compact status extracted. See full report below.</li>"

page = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>AI Watchdog Latest</title>
  <meta http-equiv="refresh" content="300">
  <style>
    body {{
      font-family: system-ui, -apple-system, Segoe UI, sans-serif;
      background: #111;
      color: #eee;
      margin: 0;
      padding: 1.25rem;
    }}
    .card {{
      background: #1d1d1d;
      border: 1px solid #333;
      border-radius: 12px;
      padding: 1rem;
      margin-bottom: 1rem;
    }}
    h1, h2, h3 {{
      color: #fff;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #0b0b0b;
      border: 1px solid #333;
      border-radius: 10px;
      padding: 1rem;
      overflow-x: auto;
    }}
    .muted {{
      color: #aaa;
      font-size: 0.9rem;
    }}
    a {{
      color: #8ab4ff;
    }}
  </style>
</head>
<body>
  <h1>AI Watchdog Latest</h1>
  <p class="muted">Published: {html.escape(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>

  <div class="card">
    <h2>Quick Status</h2>
    <ul>
      {status_html}
    </ul>
  </div>

  <div class="card">
    <h2>Full Latest Report</h2>
    <pre>{html.escape(txt)}</pre>
  </div>
</body>
</html>
"""
html_file.write_text(page)
PY

echo "Published:"
echo "$PUBLIC/latest.html"
echo "$PUBLIC/latest.md"
echo "$PUBLIC/latest-full.md"
