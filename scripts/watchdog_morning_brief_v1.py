#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime, timedelta
import html
import json
import os
import re
import subprocess

BASE = Path.home() / "ai-watchdog"
PUBLIC = BASE / "public"
REPORTS = BASE / "reports"

PUBLIC.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)

LOOKBACK_HOURS = int(os.environ.get("WATCHDOG_BRIEF_LOOKBACK_HOURS", "24"))

def now():
    return datetime.now().astimezone()

def iso_now():
    return now().isoformat(timespec="seconds")

def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(errors="replace"))
    except Exception:
        return default

def read_text(path, default=""):
    try:
        return Path(path).read_text(errors="replace")
    except Exception:
        return default

def newest(pattern):
    items = list(REPORTS.glob(pattern))
    if not items:
        return None
    return max(items, key=lambda p: p.stat().st_mtime)

def rel_public(path):
    if not path:
        return ""
    try:
        p = Path(path)
        if p.exists():
            return p.name
    except Exception:
        pass
    return str(path)

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None

def git_status():
    try:
        p = subprocess.run(
            ["git", "-C", str(BASE), "status", "--short"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        return p.stdout.strip()
    except Exception as e:
        return repr(e)

alert = read_json(PUBLIC / "alert.json", {})
dashboard = read_json(PUBLIC / "dashboard.json", {})
heartbeat = read_json(PUBLIC / "watchdog-heartbeat.json", {})
deps = read_json(PUBLIC / "dependencies.json", {})
updates = read_json(PUBLIC / "updates.json", {})
history = read_json(PUBLIC / "history.json", {})
changes = read_json(PUBLIC / "changes.json", {})
hints = read_json(PUBLIC / "action-hints.json", {})
drill = read_json(BASE / "state" / "watchdog-drill.json", {})

latest_master = newest("watchdog-master-*.md")
latest_updates = newest("watchdog-updates-*.md")
latest_change = newest("watchdog-change-*.md")
latest_backup = newest("watchdog-backup-validation-*.md")

cutoff = now() - timedelta(hours=LOOKBACK_HOURS)

hist_entries = history.get("entries") or []
recent_hist = []
for e in hist_entries:
    dt = parse_dt(e.get("timestamp"))
    if dt and dt >= cutoff:
        recent_hist.append(e)

recent_attention = [
    e for e in recent_hist
    if str(e.get("overall_status", "")).lower() not in ("ok", "clean", "false", "none")
    or e.get("alert_attention") is True
    or (e.get("dependency_attention") or 0) not in (0, "0", None)
    or (e.get("hint_count") or 0) not in (0, "0", None)
]

ha_update_data = updates.get("ha_update_entities") or {}
available_updates = ha_update_data.get("available_updates") or []
update_attention = updates.get("attention") or []

dep_summary = deps.get("summary") or {}
action_hints = hints.get("hints") or []
change_attention = changes.get("attention") or []
change_count = changes.get("change_count")
change_label = changes.get("label")

git_dirty = bool(git_status())

attention_items = []

drill_active = bool(drill.get("active"))
if drill_active:
    attention_items.append(
        "DRILL MODE active, not a real fault: "
        + str(drill.get("message") or "watchdog drill test")
    )


if alert.get("attention") is True:
    attention_items.append(f"Watchdog alert active: {alert.get('message', 'unknown')}")

if dep_summary.get("attention", 0) not in (0, "0", None):
    attention_items.append(f"Dependency map has {dep_summary.get('attention')} attention item(s).")

if action_hints:
    attention_items.append(f"Action hints available: {len(action_hints)}")

if recent_attention:
    attention_items.append(f"History shows {len(recent_attention)} attention entry/entries in the last {LOOKBACK_HOURS} hours.")

if change_attention:
    attention_items.append(f"Latest change detection has {len(change_attention)} attention item(s).")

# Updates are informational by default. Mention separately, not as main attention.
if update_attention:
    attention_items.append(f"Update monitor has {len(update_attention)} attention item(s).")

if git_dirty:
    attention_items.append("ai-watchdog Git repo has uncommitted changes.")

summary_status = "ok" if not attention_items else "attention"

recommended_first = "dashboard.html"
if alert.get("attention") is True:
    recommended_first = "latest.html"
elif action_hints:
    recommended_first = "action-hints.html"
elif dep_summary.get("attention", 0) not in (0, "0", None):
    recommended_first = "dependencies.html"
elif change_attention:
    recommended_first = "changes.html"
elif available_updates:
    recommended_first = "updates.html"

data = {
    "updated": iso_now(),
    "lookback_hours": LOOKBACK_HOURS,
    "status": summary_status,
    "attention_items": attention_items,
    "recommended_first": recommended_first,
    "latest_reports": {
        "master": str(latest_master) if latest_master else "",
        "updates": str(latest_updates) if latest_updates else "",
        "change": str(latest_change) if latest_change else "",
        "backup_validation": str(latest_backup) if latest_backup else "",
    },
    "current": {
        "alert_status": alert.get("status"),
        "alert_attention": alert.get("attention"),
        "alert_message": alert.get("message"),
        "heartbeat_status": heartbeat.get("status"),
        "dependency_summary": dep_summary,
        "action_hint_count": len(action_hints),
        "ha_update_count": len(available_updates),
        "update_attention_count": len(update_attention),
        "change_label": change_label,
        "change_count": change_count,
        "change_attention_count": len(change_attention),
        "git_dirty": git_dirty,
        "drill_active": bool(drill.get("active")),
        "drill_message": drill.get("message", ""),
    },
    "available_updates": available_updates,
    "recent_history_count": len(recent_hist),
    "recent_attention_history_count": len(recent_attention),
    "recent_history": recent_hist[-12:],
    "action_hints": action_hints[:20],
    "change_attention": change_attention,
}

(PUBLIC / "brief.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

stamp = now().strftime("%Y-%m-%d_%H-%M-%S")
report = REPORTS / f"watchdog-brief-{stamp}.md"

lines = []
lines.append("# AI Watchdog Morning Brief")
lines.append("")
lines.append(f"Date: {data['updated']}")
lines.append(f"Lookback: {LOOKBACK_HOURS} hours")
lines.append(f"Status: {summary_status.upper()}")
lines.append("")
lines.append("## Start Here")
lines.append("")
lines.append(f"- Recommended first page: `{recommended_first}`")
lines.append(f"- Latest master report: `{latest_master or 'not found'}`")
lines.append("")
lines.append("## Attention")
lines.append("")
if attention_items:
    lines.extend(f"- {x}" for x in attention_items)
else:
    lines.append("No overnight watchdog attention items.")
lines.append("")
lines.append("## Current Summary")
lines.append("")
lines.append(f"- Alert: `{alert.get('status')}` — {alert.get('message')}")
lines.append(f"- Heartbeat: `{heartbeat.get('status')}`")
lines.append(f"- Dependencies: OK `{dep_summary.get('ok')}`, Attention `{dep_summary.get('attention')}`, Unchecked `{dep_summary.get('unchecked')}`")
lines.append(f"- Action hints: `{len(action_hints)}`")
lines.append(f"- HA updates available: `{len(available_updates)}`")
lines.append(f"- Recent history entries: `{len(recent_hist)}`")
lines.append(f"- Recent attention history entries: `{len(recent_attention)}`")
lines.append(f"- Latest change window: `{change_label}` / changes `{change_count}` / attention `{len(change_attention)}`")
lines.append("")
lines.append("## Available Updates")
lines.append("")
if available_updates:
    for u in available_updates:
        lines.append(f"- {u.get('friendly_name') or u.get('entity_id')}: `{u.get('installed_version')}` → `{u.get('latest_version')}`")
else:
    lines.append("No HA update entities currently report available updates.")
lines.append("")
lines.append("## Change Detection Attention")
lines.append("")
if change_attention:
    lines.extend(f"- {x}" for x in change_attention)
else:
    lines.append("No change-detection attention items.")
lines.append("")
lines.append("## Action Hints")
lines.append("")
if action_hints:
    for h in action_hints[:20]:
        if isinstance(h, dict):
            title = h.get("title") or h.get("problem") or h.get("name") or json.dumps(h)
            lines.append(f"- {title}")
        else:
            lines.append(f"- {h}")
else:
    lines.append("No action hints.")
lines.append("")

report.write_text("\n".join(lines) + "\n")

def esc(x):
    return html.escape(str(x if x is not None else ""))

def badge(status):
    if str(status).lower() == "ok":
        return '<span class="badge ok">OK</span>'
    return '<span class="badge bad">ATTENTION</span>'

updates_html = ""
if available_updates:
    updates_html = "<ul>" + "".join(
        f"<li>{esc(u.get('friendly_name') or u.get('entity_id'))}: "
        f"<code>{esc(u.get('installed_version'))}</code> → "
        f"<code>{esc(u.get('latest_version'))}</code></li>"
        for u in available_updates
    ) + "</ul>"
else:
    updates_html = "<p>No HA update entities currently report available updates.</p>"

attention_html = ""
if attention_items:
    attention_html = "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in attention_items) + "</ul>"
else:
    attention_html = "<p>No overnight watchdog attention items.</p>"

hints_html = ""
if action_hints:
    hints_html = "<ul>" + "".join(f"<li>{esc(h.get('title') or h.get('problem') or h.get('name') or h) if isinstance(h, dict) else esc(h)}</li>" for h in action_hints[:20]) + "</ul>"
else:
    hints_html = "<p>No action hints.</p>"

hist_rows = ""
for e in reversed(recent_hist[-12:]):
    hist_rows += f"""
    <tr>
      <td>{esc(e.get('timestamp'))}</td>
      <td>{esc(e.get('overall_status'))}</td>
      <td>{esc(e.get('alert_message'))}</td>
      <td>{esc(e.get('dependency_attention'))}</td>
      <td>{esc(e.get('hint_count'))}</td>
    </tr>
    """

html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Brief</title>
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
    .muted {{ color: var(--muted); font-size: .9rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin: 16px 0;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
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
    th {{ color: var(--muted); }}
    code {{
      background: #10131a;
      padding: 2px 5px;
      border-radius: 5px;
    }}
  </style>
</head>
<body>
  <h1>AI Watchdog Brief</h1>
  <div class="muted">Updated: {esc(data['updated'])} · Lookback: {LOOKBACK_HOURS} hours</div>
  <p>
    <a href="dashboard.html">Dashboard</a> ·
    <a href="{esc(recommended_first)}">Recommended first page</a> ·
    <a href="updates.html">Updates</a> ·
    <a href="changes.html">Changes</a> ·
    <a href="history.html">History</a> ·
    <a href="drill.html">Drill</a> ·
    <a href="brief.json">Brief JSON</a>
  </p>

  <div class="grid">
    <div class="card">
      <h2>Status</h2>
      <p>{badge(summary_status)}</p>
      <p>Recommended first page: <a href="{esc(recommended_first)}">{esc(recommended_first)}</a></p>
    </div>

    <div class="card">
      <h2>Core</h2>
      <p>Alert: <code>{esc(alert.get('status'))}</code></p>
      <p>Heartbeat: <code>{esc(heartbeat.get('status'))}</code></p>
      <p>Action hints: <code>{len(action_hints)}</code></p>
    </div>

    <div class="card">
      <h2>Dependencies</h2>
      <p>OK: <code>{esc(dep_summary.get('ok'))}</code></p>
      <p>Attention: <code>{esc(dep_summary.get('attention'))}</code></p>
      <p>Unchecked: <code>{esc(dep_summary.get('unchecked'))}</code></p>
    </div>

    <div class="card">
      <h2>Updates</h2>
      <p>HA updates available: <code>{len(available_updates)}</code></p>
      <p>Update monitor attention: <code>{len(update_attention)}</code></p>
    </div>
  </div>

  <div class="card">
    <h2>Attention</h2>
    {attention_html}
  </div>

  <div class="card">
    <h2>Available Updates</h2>
    {updates_html}
  </div>

  <div class="card">
    <h2>Action Hints</h2>
    {hints_html}
  </div>

  <div class="card">
    <h2>Recent History</h2>
    <table>
      <thead>
        <tr>
          <th>Timestamp</th>
          <th>Status</th>
          <th>Alert</th>
          <th>Deps attention</th>
          <th>Hints</th>
        </tr>
      </thead>
      <tbody>
        {hist_rows}
      </tbody>
    </table>
  </div>
</body>
</html>
"""

(PUBLIC / "brief.html").write_text(html_doc)

print(f"Brief report saved to: {report}")
print(f"Brief page written to: {PUBLIC / 'brief.html'}")
print(f"Brief JSON written to: {PUBLIC / 'brief.json'}")
print(f"Status: {summary_status}")
print(f"Recommended first page: {recommended_first}")
