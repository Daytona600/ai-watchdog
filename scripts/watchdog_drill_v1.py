#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
import html
import json
import sys
import uuid

BASE = Path.home() / "ai-watchdog"
STATE = BASE / "state"
PUBLIC = BASE / "public"

STATE.mkdir(parents=True, exist_ok=True)
PUBLIC.mkdir(parents=True, exist_ok=True)

STATE_FILE = STATE / "watchdog-drill.json"
PUBLIC_JSON = PUBLIC / "drill.json"
PUBLIC_HTML = PUBLIC / "drill.html"

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def read_state():
    try:
        return json.loads(STATE_FILE.read_text(errors="replace"))
    except Exception:
        return {
            "active": False,
            "message": "",
            "updated": now_iso(),
        }

def write_state(data):
    data["updated"] = now_iso()
    STATE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    PUBLIC_JSON.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    write_html(data)

def esc(x):
    return html.escape(str(x if x is not None else ""))

def write_html(data):
    active = bool(data.get("active"))
    status = "DRILL ACTIVE" if active else "No drill active"
    message = data.get("message") or "No active drill."

    runbook_links = [
        ("Back door lock unavailable", "runbooks/back-door-lock-unavailable.html"),
        ("Frigate camera problem", "runbooks/frigate-camera-problem.html"),
        ("Node-RED flow problem", "runbooks/nodered-flow-problem.html"),
        ("NAS mount problem", "runbooks/nas-mount-problem.html"),
        ("Watchdog timer stopped", "runbooks/watchdog-timer-stopped.html"),
    ]

    links_html = "".join(
        f'<li><a href="{esc(url)}">{esc(title)}</a></li>'
        for title, url in runbook_links
    )

    badge_class = "bad" if active else "ok"

    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Drill Mode</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #171a21;
      --text: #e8e8e8;
      --muted: #a8b0bf;
      --line: #2a2f3a;
      --link: #8ab4ff;
      --ok: #1f8f4d;
      --bad: #b83b3b;
    }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      line-height: 1.45;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin: 14px 0;
    }}
    .muted {{ color: var(--muted); font-size: .9rem; }}
    .badge {{
      display: inline-block;
      padding: 5px 10px;
      border-radius: 999px;
      font-weight: 800;
      font-size: .82rem;
    }}
    .ok {{ background: var(--ok); color: white; }}
    .bad {{ background: var(--bad); color: white; }}
    code {{
      background: #10131a;
      padding: 2px 5px;
      border-radius: 5px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #10131a;
      border: 1px solid var(--line);
      padding: 10px;
      border-radius: 10px;
    }}
  </style>
</head>
<body>
  <h1>AI Watchdog Drill Mode</h1>
  <div class="muted">Updated: {esc(data.get("updated"))}</div>
  <p>
    <a href="dashboard.html">Dashboard</a> ·
    <a href="brief.html">Brief</a> ·
    <a href="history.html">History</a> ·
    <a href="drill.json">Drill JSON</a>
  </p>

  <div class="card">
    <h2>Status</h2>
    <p><span class="badge {badge_class}">{esc(status)}</span></p>
    <p><strong>This is a safe simulation only.</strong> It does not touch real devices, containers, cameras, locks, NAS mounts, or Node-RED.</p>
  </div>

  <div class="card">
    <h2>Message</h2>
    <p>{esc(message)}</p>
    <p>Drill ID: <code>{esc(data.get("drill_id", ""))}</code></p>
    <p>Started: <code>{esc(data.get("started", ""))}</code></p>
  </div>

  <div class="card">
    <h2>Useful runbooks</h2>
    <ul>{links_html}</ul>
  </div>

  <div class="card">
    <h2>Commands</h2>
    <pre>Start:
~/ai-watchdog/scripts/watchdog_drill_v1.py start "test notification path"

Clear:
~/ai-watchdog/scripts/watchdog_drill_v1.py clear</pre>
  </div>
</body>
</html>
"""
    PUBLIC_HTML.write_text(doc)

def cmd_start(message):
    data = {
        "active": True,
        "drill_id": str(uuid.uuid4())[:8],
        "started": now_iso(),
        "message": message or "Watchdog drill test",
        "severity": "drill",
        "not_real_fault": True,
    }
    write_state(data)
    print("DRILL MODE started.")
    print(f"Message: {data['message']}")
    print(f"Drill page: {PUBLIC_HTML}")
    print("")
    print("Next run:")
    print("  ~/ai-watchdog/scripts/watchdog_morning_brief_v1.py")
    print("  ~/ai-watchdog/scripts/watchdog_ha_notify_v1.py")

def cmd_clear():
    old = read_state()
    data = {
        "active": False,
        "drill_id": old.get("drill_id", ""),
        "started": old.get("started", ""),
        "cleared": now_iso(),
        "message": "No active drill.",
        "previous_message": old.get("message", ""),
        "not_real_fault": True,
    }
    write_state(data)
    print("DRILL MODE cleared.")
    print(f"Drill page: {PUBLIC_HTML}")
    print("")
    print("Next run:")
    print("  ~/ai-watchdog/scripts/watchdog_morning_brief_v1.py")
    print("  ~/ai-watchdog/scripts/watchdog_ha_notify_v1.py")

def cmd_status():
    data = read_state()
    write_html(data)
    PUBLIC_JSON.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(data, indent=2, sort_keys=True))

def usage():
    print("Usage:")
    print('  watchdog_drill_v1.py start "message"')
    print("  watchdog_drill_v1.py clear")
    print("  watchdog_drill_v1.py status")

def main():
    if len(sys.argv) < 2:
        usage()
        raise SystemExit(1)

    cmd = sys.argv[1].lower()

    if cmd == "start":
        message = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Watchdog drill test"
        cmd_start(message)
    elif cmd == "clear":
        cmd_clear()
    elif cmd == "status":
        cmd_status()
    else:
        usage()
        raise SystemExit(1)

if __name__ == "__main__":
    main()
