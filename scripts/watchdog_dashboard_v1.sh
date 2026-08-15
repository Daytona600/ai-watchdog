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
import sys

base = Path(sys.argv[1])
public = base / "public"
reports = base / "reports"

sys.path.insert(0, str(base / "scripts" / "lib"))
import watchdog_severity  # noqa: E402

SEVERITY_ORDER = {"ok": 0, "info": 1, "unknown": 1, "warning": 2, "critical": 3}
SEVERITY_LABEL = {"ok": "OK", "info": "INFO", "unknown": "UNKNOWN", "warning": "WARNING", "critical": "CRITICAL"}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return default


def esc(x):
    return html.escape(str(x if x is not None else ""))


def newest(pattern: str):
    items = list(reports.glob(pattern))
    if not items:
        return None
    return max(items, key=lambda p: p.stat().st_mtime)


def fmt_time_from_path(path):
    if not path or not path.exists():
        return "not found"
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def age_hours(path):
    if not path or not path.exists():
        return None
    age = datetime.now().astimezone().timestamp() - path.stat().st_mtime
    return int(age // 3600)


def load_hint_rules(path: Path):
    rules = []
    if path.exists():
        for raw in path.read_text(errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "\t" not in raw:
                continue
            pattern, hint = raw.split("\t", 1)
            pattern, hint = pattern.strip(), hint.strip()
            if pattern and hint:
                rules.append((pattern, hint))
    return rules


def hints_for(text: str, rules):
    low = text.lower()
    return [hint for pattern, hint in rules if pattern.lower() in low]


def parse_problem_lines(text: str):
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.endswith(":"):
            continue
        lines.append(stripped.lstrip("- ").strip())
    return lines


alert = read_json(public / "alert.json", {})
heartbeat = read_json(public / "watchdog-heartbeat.json", {})
dependencies = read_json(public / "dependencies.json", {})
hint_rules = load_hint_rules(base / "config" / "watchdog_action_hints.tsv")

latest_master = newest("watchdog-master-*.md")
latest_master_time = fmt_time_from_path(latest_master)
latest_master_age = age_hours(latest_master)

heartbeat_status = str(heartbeat.get("status", "unknown")).lower()
heartbeat_ok = heartbeat_status == "ok"
heartbeat_message = heartbeat.get("message") or "No heartbeat data found."

# --- Problems (warning/critical) from alert.json, each re-classified so the
# dashboard can bucket by severity instead of treating every attention line
# the same way. ---
problems = []
for line in parse_problem_lines(alert.get("message") or ""):
    sev = watchdog_severity.classify(line)
    problems.append({"text": line, "severity": sev, "hints": hints_for(line, hint_rules)})
problems.sort(key=lambda p: -SEVERITY_ORDER.get(p["severity"], 2))

# --- Info-only items: from alert.json's own info list plus any dependency
# service sitting at info severity (e.g. git-dirty during active dev). ---
info_items = []
for raw in (alert.get("info") or []):
    stripped = str(raw).strip()
    if not stripped or stripped.endswith(":"):
        continue
    info_items.append(stripped.lstrip("- ").strip())

# --- Live services grid, from watchdog_dependencies_v1.py's real checks. ---
services = dependencies.get("services") or []


def service_badge_class(row):
    if row["status"] == "ok":
        return "ok"
    if row["status"] == "unknown":
        return "unknown"
    sev = row.get("severity", "warning")
    if sev == "info":
        return "info"
    if sev == "critical":
        return "bad"
    return "warn"


def service_badge_label(row):
    if row["status"] == "ok":
        return "OK"
    if row["status"] == "unknown":
        return "UNKNOWN"
    return SEVERITY_LABEL.get(row.get("severity", "warning"), "WARNING")


STATUS_BUCKET = {"attention": 0, "unknown": 1, "ok": 2}
services_sorted = sorted(
    services,
    key=lambda r: (
        STATUS_BUCKET.get(r["status"], 1),
        -{"critical": 3, "warning": 2, "info": 1}.get(r.get("severity", "warning"), 2) if r["status"] == "attention" else 0,
        r["service"].lower(),
    ),
)

overall_critical = heartbeat_status != "ok"
overall_warning = False
for p in problems:
    if p["severity"] == "critical":
        overall_critical = True
    elif p["severity"] == "warning":
        overall_warning = True
for row in services:
    if row["status"] == "attention":
        if row.get("severity") == "critical":
            overall_critical = True
        elif row.get("severity") != "info":
            overall_warning = True

if overall_critical:
    overall_status = "CRITICAL"
elif overall_warning:
    overall_status = "WARNING"
else:
    overall_status = "OK"

updated = datetime.now().astimezone().isoformat(timespec="seconds")


def status_badge(label, cls):
    return f'<span class="badge {cls}">{esc(label)}</span>'


def service_card(row):
    badge = status_badge(service_badge_label(row), service_badge_class(row))
    detail_text = row.get("detail", "")
    hints = hints_for(f"{row['service']} {detail_text}", hint_rules) if row["status"] != "ok" else []

    body = f"<p class='muted'>{esc(detail_text)}</p>"

    deps = row.get("depends_on") or []
    used_by = row.get("used_by") or []
    extra = []
    if deps:
        extra.append("<h4>Depends on</h4><ul>" + "".join(
            f"<li><strong>{esc(d['depends_on'])}</strong>: {esc(d['why'])}</li>" for d in deps
        ) + "</ul>")
    if used_by:
        extra.append("<h4>Used by</h4><ul>" + "".join(f"<li>{esc(d['service'])}</li>" for d in used_by) + "</ul>")
    if hints:
        extra.append("<h4>Suggested next step</h4><ul>" + "".join(f"<li>{esc(h)}</li>" for h in hints) + "</ul>")
    if not extra:
        extra.append("<p class='muted'>No dependency or hint details recorded.</p>")

    return f"""
    <details class="card service-card sev-{service_badge_class(row)}">
      <summary>
        <span class="service-name">{esc(row['service'])}</span>
        {badge}
      </summary>
      {body}
      {''.join(extra)}
    </details>
    """


def problem_item(p):
    hint_html = ""
    if p["hints"]:
        hint_html = "<ul>" + "".join(f"<li>{esc(h)}</li>" for h in p["hints"]) + "</ul>"
    else:
        hint_html = "<p class='muted'>No matching hint recorded for this problem yet.</p>"
    sev_cls = "bad" if p["severity"] == "critical" else "warn"
    return f"""
    <details class="problem sev-{sev_cls}">
      <summary>{status_badge(SEVERITY_LABEL.get(p['severity'], 'WARNING'), sev_cls)} {esc(p['text'])}</summary>
      {hint_html}
    </details>
    """


services_html = "".join(service_card(r) for r in services_sorted) if services_sorted else \
    "<p class='muted'>No dependency/service checks found yet. Run watchdog_dependencies_v1.py.</p>"

problems_html = "".join(problem_item(p) for p in problems) if problems else \
    "<p class='muted'>No warnings or critical problems right now.</p>"

info_html = "".join(f"<li>{esc(i)}</li>" for i in info_items) if info_items else \
    "<li class='muted'>Nothing to note.</li>"

data = {
    "status": overall_status.lower(),
    "updated": updated,
    "latest_master_report": str(latest_master) if latest_master else "",
    "latest_master_time": latest_master_time,
    "latest_master_age_hours": latest_master_age,
    "heartbeat_status": heartbeat_status,
    "problem_count": len(problems),
    "critical_count": sum(1 for p in problems if p["severity"] == "critical"),
    "warning_count": sum(1 for p in problems if p["severity"] == "warning"),
    "info_count": len(info_items),
    "service_count": len(services),
    "service_attention_count": sum(1 for r in services if r["status"] == "attention"),
}

(public / "dashboard.json").write_text(json.dumps(data, indent=2) + "\n")

overall_cls = "bad" if overall_status == "CRITICAL" else "warn" if overall_status == "WARNING" else "ok"

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
      --info: #3a5f8a;
      --unknown: #6b7280;
      --line: #2a2f3a;
      --link: #8ab4ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      line-height: 1.45;
    }}
    h1 {{ margin: 0 0 4px 0; font-size: 1.6rem; }}
    h2 {{ margin: 18px 0 8px 0; font-size: 1.15rem; }}
    h3 {{ margin: 0 0 8px 0; font-size: 1rem; }}
    h4 {{ margin: 10px 0 4px 0; font-size: 0.85rem; color: var(--muted); }}
    .muted {{ color: var(--muted); font-size: 0.9rem; }}
    .top-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      margin-bottom: 4px;
    }}
    .stat-line {{
      color: var(--muted);
      font-size: 0.9rem;
      margin-bottom: 18px;
    }}
    .stat-line strong {{ color: var(--text); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 2px 8px rgba(0,0,0,.25);
    }}
    details.card > summary {{
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }}
    details.card > summary::-webkit-details-marker {{ display: none; }}
    .service-name {{ font-weight: 600; }}
    details.card[open] {{ border-color: var(--muted); }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-weight: 700;
      letter-spacing: .03em;
      font-size: .75rem;
      white-space: nowrap;
    }}
    .ok {{ background: var(--ok); color: white; }}
    .bad {{ background: var(--bad); color: white; }}
    .warn {{ background: var(--warn); color: white; }}
    .info {{ background: var(--info); color: white; }}
    .unknown {{ background: var(--unknown); color: white; }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    ul {{ padding-left: 20px; margin: 6px 0; }}
    .problem {{
      background: var(--card);
      border: 1px solid var(--line);
      border-left: 4px solid var(--warn);
      border-radius: 10px;
      padding: 10px 14px;
      margin-bottom: 8px;
    }}
    .problem.sev-bad {{ border-left-color: var(--bad); }}
    .problem summary {{ cursor: pointer; display: flex; gap: 10px; align-items: center; }}
    .info-box {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    .info-box summary {{ cursor: pointer; color: var(--muted); }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #10131a;
      border: 1px solid var(--line);
      padding: 10px;
      border-radius: 10px;
      color: var(--text);
    }}
    .links a {{ display: block; margin: 5px 0; }}
  </style>
</head>
<body>
  <div class="top-row">
    <h1>AI Watchdog</h1>
    {status_badge(overall_status, overall_cls)}
  </div>
  <div class="stat-line">
    Updated {esc(updated)} &middot; auto-refreshes every 5 minutes &middot;
    <strong>{data['service_count']}</strong> services checked
    (<strong>{data['service_attention_count']}</strong> need attention) &middot;
    <strong>{data['critical_count']}</strong> critical &middot;
    <strong>{data['warning_count']}</strong> warning &middot;
    <strong>{data['info_count']}</strong> info-only &middot;
    heartbeat: <strong>{esc(heartbeat_status.upper())}</strong> &middot;
    last full run {esc(str(latest_master_age) + 'h ago' if latest_master_age is not None else 'unknown')}
  </div>

  <h2>Needs Attention</h2>
  {problems_html}

  <h2>Live Services</h2>
  <div class="grid">
    {services_html}
  </div>

  <h2>Info / FYI</h2>
  <details class="info-box">
    <summary>{len(info_items)} informational item(s) &mdash; not paging you, shown for reference</summary>
    <ul>{info_html}</ul>
    <p class="muted">Heartbeat: {esc(heartbeat_message)}</p>
  </details>

  <h2>Useful Links</h2>
  <div class="card links">
    <a href="latest.html">Full watchdog report</a>
    <a href="latest.md">Latest markdown summary</a>
    <a href="latest-full.md">Latest full markdown</a>
    <a href="dependencies.html">Full dependency map</a>
    <a href="updates.html">Update monitor</a>
    <a href="changes.html">Change detection</a>
    <a href="history.html">Watchdog history</a>
    <a href="brief.html">Morning brief</a>
    <a href="drill.html">Safe drill mode</a>
    <a href="runbooks/index.html">Recovery runbooks</a>
    <a href="dashboard.json">Dashboard JSON</a>
    <a href="dependencies.json">Dependency JSON</a>
    <a href="alert.json">Alert JSON</a>
    <a href="watchdog-heartbeat.json">Heartbeat JSON</a>
  </div>
</body>
</html>
"""

(public / "dashboard.html").write_text(html_doc)
(public / "index.html").write_text(html_doc)

print(f"Dashboard written to: {public / 'dashboard.html'}")
print(f"Dashboard JSON:       {public / 'dashboard.json'}")
print(f"Overall status:       {overall_status}")
PY
