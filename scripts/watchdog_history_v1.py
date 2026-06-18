#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timezone
import html
import json
import os
import re
import sys

BASE = Path.home() / "ai-watchdog"
PUBLIC = BASE / "public"
HISTORY_DIR = BASE / "history"
HISTORY_FILE = HISTORY_DIR / "watchdog-history.jsonl"

PUBLIC.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return default

def read_text(path: Path, default=""):
    try:
        return path.read_text(errors="replace")
    except Exception:
        return default

def newest(pattern: str):
    items = list((BASE / "reports").glob(pattern))
    if not items:
        return None
    return max(items, key=lambda p: p.stat().st_mtime)

def iso_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def file_time(path: Path):
    if not path or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")

def count_attention_lines(report_text: str):
    count = 0
    capture = False

    for raw in report_text.splitlines():
        line = raw.strip()

        if re.search(r"attention needed|critical entity problems", line, flags=re.I):
            capture = True
            continue

        if capture and line.startswith("#"):
            capture = False
            continue

        if not capture:
            continue

        if not line or line.startswith("```"):
            continue

        low = line.lower()

        # Ignore clean/no-problem lines.
        if low.startswith("no "):
            continue
        if "no watchdog attention" in low:
            continue
        if "no newly unknown" in low:
            continue
        if "no unavailable" in low:
            continue
        if "no new" in low:
            continue
        if "no missing" in low:
            continue
        if "no unhealthy" in low:
            continue

        # Count actual problem bullets and actual HA entity problem rows.
        if line.startswith("- "):
            count += 1
        elif re.search(r"\s(unavailable|unknown)\s*$", low):
            count += 1

    return count


alert = read_json(PUBLIC / "alert.json", {})
dashboard = read_json(PUBLIC / "dashboard.json", {})
heartbeat = read_json(PUBLIC / "watchdog-heartbeat.json", {})
deps = read_json(PUBLIC / "dependencies.json", {})
hints = read_json(PUBLIC / "action-hints.json", {})

latest_master = newest("watchdog-master-*.md")
master_text = read_text(latest_master) if latest_master else ""

record = {
    "timestamp": iso_now(),
    "latest_master_report": str(latest_master) if latest_master else "",
    "latest_master_time": file_time(latest_master),
    "overall_status": dashboard.get("status", "unknown"),
    "alert_attention": bool(alert.get("attention", False)),
    "alert_status": alert.get("status", "unknown"),
    "alert_message": alert.get("message", ""),
    "heartbeat_status": heartbeat.get("status", "unknown"),
    "dependency_ok": deps.get("summary", {}).get("ok"),
    "dependency_attention": deps.get("summary", {}).get("attention"),
    "dependency_unchecked": deps.get("summary", {}).get("unchecked"),
    "hint_count": len(hints.get("hints") or []),
    "master_attention_line_count": count_attention_lines(master_text),
}

# Avoid duplicate entries for the same master report unless status changed.
existing = []
if HISTORY_FILE.exists():
    for line in HISTORY_FILE.read_text(errors="replace").splitlines():
        try:
            existing.append(json.loads(line))
        except Exception:
            pass

should_append = True
if existing:
    last = existing[-1]
    if (
        last.get("latest_master_report") == record["latest_master_report"]
        and last.get("overall_status") == record["overall_status"]
        and last.get("alert_message") == record["alert_message"]
        and last.get("dependency_attention") == record["dependency_attention"]
        and last.get("hint_count") == record["hint_count"]
    ):
        should_append = False

if should_append:
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")

# Retain last 180 entries.
entries = []
if HISTORY_FILE.exists():
    for line in HISTORY_FILE.read_text(errors="replace").splitlines():
        try:
            entries.append(json.loads(line))
        except Exception:
            pass

entries = entries[-180:]
HISTORY_FILE.write_text("\n".join(json.dumps(x, sort_keys=True) for x in entries) + ("\n" if entries else ""))

(PUBLIC / "history.json").write_text(json.dumps({
    "updated": iso_now(),
    "count": len(entries),
    "latest": entries[-1] if entries else None,
    "entries": entries,
}, indent=2) + "\n")

def esc(x):
    return html.escape(str(x if x is not None else ""))

def badge(status):
    s = str(status or "unknown").lower()
    ok = s in ("ok", "false", "none") or s == "clean"
    cls = "ok" if ok else "bad"
    label = "OK" if ok else "ATTENTION"
    if s == "unknown":
        cls = "unknown"
        label = "UNKNOWN"
    return f'<span class="badge {cls}">{label}</span>'

rows = []
for e in reversed(entries[-60:]):
    rows.append(f"""
      <tr>
        <td>{esc(e.get("timestamp"))}</td>
        <td>{badge(e.get("overall_status"))}</td>
        <td>{esc(e.get("alert_message") or "No alert message")}</td>
        <td>{esc(e.get("heartbeat_status"))}</td>
        <td>{esc(e.get("dependency_ok"))}</td>
        <td>{esc(e.get("dependency_attention"))}</td>
        <td>{esc(e.get("dependency_unchecked"))}</td>
        <td>{esc(e.get("hint_count"))}</td>
      </tr>
    """)

attention_runs = sum(1 for e in entries if str(e.get("overall_status", "")).lower() not in ("ok", "false", "none", "clean"))
clean_runs = len(entries) - attention_runs

html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog History</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #171a21;
      --text: #e8e8e8;
      --muted: #a8b0bf;
      --ok: #1f8f4d;
      --bad: #b83b3b;
      --unknown: #6b7280;
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
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .muted {{ color: var(--muted); font-size: .9rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin: 16px 0;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      overflow: hidden;
    }}
    th, td {{
      padding: 9px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: .9rem;
    }}
    th {{
      color: var(--muted);
      font-weight: 700;
    }}
    .badge {{
      display: inline-block;
      padding: 4px 9px;
      border-radius: 999px;
      font-weight: 700;
      font-size: .78rem;
    }}
    .ok {{ background: var(--ok); color: white; }}
    .bad {{ background: var(--bad); color: white; }}
    .unknown {{ background: var(--unknown); color: white; }}
  </style>
</head>
<body>
  <h1>AI Watchdog History</h1>
  <div class="muted">Updated: {esc(iso_now())} · Showing latest 60 history entries</div>
  <p>
    <a href="dashboard.html">Dashboard</a> ·
    <a href="dependencies.html">Dependency map</a> ·
    <a href="runbooks/index.html">Recovery runbooks</a> ·
    <a href="history.json">History JSON</a>
  </p>

  <div class="grid">
    <div class="card"><h2>Total history entries</h2><p>{len(entries)}</p></div>
    <div class="card"><h2>Clean entries</h2><p>{clean_runs}</p></div>
    <div class="card"><h2>Attention entries</h2><p>{attention_runs}</p></div>
    <div class="card"><h2>Latest status</h2><p>{badge(entries[-1].get("overall_status") if entries else "unknown")}</p></div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Timestamp</th>
        <th>Status</th>
        <th>Alert message</th>
        <th>Heartbeat</th>
        <th>Deps OK</th>
        <th>Deps Attention</th>
        <th>Deps Unchecked</th>
        <th>Hints</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""

(PUBLIC / "history.html").write_text(html_doc)

print(f"History updated: {HISTORY_FILE}")
print(f"History page:    {PUBLIC / 'history.html'}")
print(f"Entries:         {len(entries)}")
print(f"Latest status:   {record['overall_status']}")
