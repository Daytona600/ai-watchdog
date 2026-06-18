#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
PUBLIC="$BASE/public"
mkdir -p "$PUBLIC"

python3 - "$BASE" <<'PY'
from pathlib import Path
from datetime import datetime
import html
import json
import os
import shutil
import sys

base = Path(sys.argv[1])
public = base / "public"
reports = base / "reports"

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return default

def newest(pattern: str):
    items = list(reports.glob(pattern))
    if not items:
        return None
    return max(items, key=lambda p: p.stat().st_mtime)

def fmt_time_from_path(path: Path):
    if not path or not path.exists():
        return "not found"
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")

def age_hours(path: Path):
    if not path or not path.exists():
        return "unknown"
    age = datetime.now().astimezone().timestamp() - path.stat().st_mtime
    return str(int(age // 3600))

def esc(x):
    return html.escape(str(x if x is not None else ""))

alert = read_json(public / "alert.json", {})
heartbeat = read_json(public / "watchdog-heartbeat.json", {})
actions = read_json(public / "action-hints.json", {})

latest_master = newest("watchdog-master-*.md")
latest_master_time = fmt_time_from_path(latest_master)
latest_master_age = age_hours(latest_master)

alert_attention = bool(alert.get("attention", False)) or str(alert.get("status", "ok")).lower() not in ("ok", "false", "none")
alert_message = alert.get("message") or "No alert message found."

heartbeat_status = str(heartbeat.get("status", "unknown")).lower()
heartbeat_ok = heartbeat_status == "ok"
heartbeat_message = heartbeat.get("message") or "No heartbeat message found."

hints = actions.get("hints") or []
hint_count = len(hints)

overall_attention = alert_attention or not heartbeat_ok
overall_status = "ATTENTION" if overall_attention else "OK"

updated = datetime.now().astimezone().isoformat(timespec="seconds")

def status_badge(label, ok):
    cls = "ok" if ok else "bad"
    return f'<span class="badge {cls}">{esc(label)}</span>'

hint_html = []
if not hints:
    hint_html.append("<p>No current action hints.</p>")
else:
    for item in hints:
        problem = item.get("problem", "Unknown problem")
        hint_html.append(f"<div class='hint'><h3>{esc(problem)}</h3><ul>")
        for h in item.get("hints", []):
            hint_html.append(f"<li>{esc(h)}</li>")
        hint_html.append("</ul></div>")

heartbeat_details = heartbeat.get("details", "")
if isinstance(heartbeat_details, str):
    heartbeat_details_html = "<pre>" + esc(heartbeat_details) + "</pre>"
else:
    heartbeat_details_html = "<pre>" + esc(json.dumps(heartbeat_details, indent=2)) + "</pre>"

data = {
    "status": overall_status.lower(),
    "updated": updated,
    "latest_master_report": str(latest_master) if latest_master else "",
    "latest_master_time": latest_master_time,
    "latest_master_age_hours": latest_master_age,
    "alert_attention": alert_attention,
    "alert_message": alert_message,
    "heartbeat_status": heartbeat_status,
    "heartbeat_message": heartbeat_message,
    "hint_count": hint_count,
}

(public / "dashboard.json").write_text(json.dumps(data, indent=2) + "\n")

html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #171a21;
      --text: #e8e8e8;
      --muted: #a8b0bf;
      --ok: #1f8f4d;
      --bad: #b83b3b;
      --warn: #b8871f;
      --line: #2a2f3a;
      --link: #8ab4ff;
    }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      line-height: 1.45;
    }}
    h1 {{
      margin: 0 0 6px 0;
      font-size: 1.7rem;
    }}
    h2 {{
      margin-top: 0;
      font-size: 1.1rem;
    }}
    h3 {{
      margin-bottom: 6px;
      font-size: 1rem;
    }}
    .muted {{
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 2px 8px rgba(0,0,0,.25);
    }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      font-weight: 700;
      letter-spacing: .03em;
      font-size: .85rem;
    }}
    .ok {{ background: var(--ok); color: white; }}
    .bad {{ background: var(--bad); color: white; }}
    .warn {{ background: var(--warn); color: white; }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #10131a;
      border: 1px solid var(--line);
      padding: 10px;
      border-radius: 10px;
      color: var(--text);
    }}
    ul {{
      padding-left: 20px;
    }}
    .hint {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
      margin-top: 10px;
    }}
    .links a {{
      display: block;
      margin: 5px 0;
    }}
  </style>
</head>
<body>
  <h1>AI Watchdog Dashboard</h1>
  <div class="muted">Updated: {esc(updated)} · Auto-refreshes every 5 minutes</div>

  <div class="grid">
    <div class="card">
      <h2>Overall Status</h2>
      {status_badge(overall_status, not overall_attention)}
      <p class="muted">This combines the alert state and heartbeat state.</p>
    </div>

    <div class="card">
      <h2>Latest Master Report</h2>
      <p><strong>Time:</strong> {esc(latest_master_time)}</p>
      <p><strong>Age:</strong> {esc(latest_master_age)} hours</p>
      <p><a href="latest.html">Open full report</a></p>
    </div>

    <div class="card">
      <h2>Alert State</h2>
      {status_badge("ATTENTION" if alert_attention else "OK", not alert_attention)}
      <p>{esc(alert_message)}</p>
      <p><a href="alert.txt">Open alert text</a></p>
    </div>

    <div class="card">
      <h2>Heartbeat</h2>
      {status_badge(heartbeat_status.upper(), heartbeat_ok)}
      <p>{esc(heartbeat_message)}</p>
      {heartbeat_details_html}
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Action Hints</h2>
      <p class="muted">Matched hint groups: {hint_count}</p>
      {''.join(hint_html)}
      <p><a href="action-hints.md">Open action hints markdown</a></p>
    </div>

    <div class="card links">
      <h2>Useful Links</h2>
      <a href="latest.html">Full watchdog report</a>
      <a href="latest.md">Latest markdown summary</a>
      <a href="latest-full.md">Latest full markdown</a>
      <a href="dashboard.json">Dashboard JSON</a>
      <a href="alert.json">Alert JSON</a>
      <a href="watchdog-heartbeat.json">Heartbeat JSON</a>
      <a href="drill.html">Safe drill mode</a>
      <a href="brief.html">Morning brief</a>
      <a href="changes.html">Change detection</a>
      <a href="updates.html">Update monitor</a>
      <a href="history.html">Watchdog history</a>
      <a href="dependencies.html">Service dependency map</a>
      <a href="runbooks/index.html">Recovery runbooks</a>
      <a href="action-hints.json">Action hints JSON</a>
    </div>
  </div>
</body>
</html>
"""

(public / "dashboard.html").write_text(html_doc)
(public / "index.html").write_text(html_doc)

print(f"Dashboard written to: {public / 'dashboard.html'}")
print(f"Dashboard JSON:       {public / 'dashboard.json'}")
PY
