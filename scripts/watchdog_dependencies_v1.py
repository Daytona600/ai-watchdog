#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import html
import json
import subprocess
import sys

BASE = Path.home() / "ai-watchdog"
PUBLIC = BASE / "public"
PUBLIC.mkdir(parents=True, exist_ok=True)

DEPENDENCIES = BASE / "config/watchdog_dependencies.tsv"
CHECKS = BASE / "config/watchdog_dependency_checks.tsv"

def read_tsv(path):
    rows = []
    if not path.exists():
        return rows
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = raw.split("\t")
        rows.append(parts)
    return rows

def run(cmd, timeout=8):
    try:
        p = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 999, "", repr(e)

def check_container(name):
    rc, out, err = run(f"docker inspect -f '{{{{.State.Running}}}}' {name} 2>/dev/null")
    ok = (rc == 0 and out.strip().lower() == "true")
    return ok, "running" if ok else f"not running or not found ({err or out})"

def check_http(url):
    try:
        req = Request(url, headers={"User-Agent": "ai-watchdog/1.0"})
        with urlopen(req, timeout=8) as r:
            ok = 200 <= r.status < 300
            return ok, f"HTTP {r.status}"
    except HTTPError as e:
        return False, f"HTTP {e.code}"
    except URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:
        return False, repr(e)

def check_path(path):
    p = Path(path)
    return p.exists(), "exists" if p.exists() else "missing"

def check_timer(name):
    rc1, out1, err1 = run(f"systemctl --user is-active {name}")
    rc2, out2, err2 = run(f"systemctl --user is-enabled {name}")
    ok = (out1.strip() == "active" and out2.strip() == "enabled")
    return ok, f"active={out1.strip() or err1}, enabled={out2.strip() or err2}"

def check_git_clean(path):
    p = Path(path)
    if not p.exists():
        return False, "repo path missing"
    rc, out, err = run(f"git -C {p} status --short")
    if rc != 0:
        return False, err or out
    return out.strip() == "", "clean" if out.strip() == "" else "uncommitted changes"

def check_command(cmd):
    rc, out, err = run(cmd)
    return rc == 0, "ok" if rc == 0 else (err or out)

def check_one(kind, target):
    if kind == "container":
        return check_container(target)
    if kind == "http":
        return check_http(target)
    if kind == "path":
        return check_path(target)
    if kind == "systemd-user-timer":
        return check_timer(target)
    if kind == "git-clean":
        return check_git_clean(target)
    if kind == "command":
        return check_command(target)
    return False, f"unknown check type: {kind}"

deps = []
services = set()

for row in read_tsv(DEPENDENCIES):
    if len(row) < 3:
        continue
    service, depends_on, why = row[0].strip(), row[1].strip(), row[2].strip()
    deps.append({"service": service, "depends_on": depends_on, "why": why})
    services.add(service)
    services.add(depends_on)

checks = {}
for row in read_tsv(CHECKS):
    if len(row) < 3:
        continue
    name, kind, target = row[0].strip(), row[1].strip(), row[2].strip()
    ok, detail = check_one(kind, target)
    checks[name] = {"ok": ok, "type": kind, "target": target, "detail": detail}

service_rows = []
for service in sorted(services):
    direct_deps = [d for d in deps if d["service"] == service]
    used_by = [d for d in deps if d["depends_on"] == service]
    check = checks.get(service)
    status = "unknown"
    detail = "no direct check configured"
    if check:
        status = "ok" if check["ok"] else "attention"
        detail = check["detail"]
    service_rows.append({
        "service": service,
        "status": status,
        "detail": detail,
        "depends_on": direct_deps,
        "used_by": used_by,
    })

summary = {
    "ok": sum(1 for c in checks.values() if c["ok"]),
    "attention": sum(1 for c in checks.values() if not c["ok"]),
    "unchecked": sum(1 for s in services if s not in checks),
}

data = {
    "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
    "summary": summary,
    "dependencies": deps,
    "checks": checks,
    "services": service_rows,
}

(PUBLIC / "dependencies.json").write_text(json.dumps(data, indent=2) + "\n")

def badge(status):
    cls = "ok" if status == "ok" else "bad" if status == "attention" else "unknown"
    return f'<span class="badge {cls}">{html.escape(status.upper())}</span>'

cards = []
for row in service_rows:
    deps_html = ""
    if row["depends_on"]:
        deps_html = "<h3>Depends on</h3><ul>" + "".join(
            f"<li><strong>{html.escape(d['depends_on'])}</strong>: {html.escape(d['why'])}</li>"
            for d in row["depends_on"]
        ) + "</ul>"
    else:
        deps_html = "<p class='muted'>No dependencies listed.</p>"

    used_html = ""
    if row["used_by"]:
        used_html = "<h3>Used by</h3><ul>" + "".join(
            f"<li>{html.escape(d['service'])}</li>"
            for d in row["used_by"]
        ) + "</ul>"

    cards.append(f"""
    <div class="card">
      <h2>{html.escape(row['service'])}</h2>
      {badge(row['status'])}
      <p class="muted">{html.escape(row['detail'])}</p>
      {deps_html}
      {used_html}
    </div>
    """)

updated = data["updated"]
html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Dependencies</title>
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
    .top {{
      margin-bottom: 16px;
    }}
    .muted {{ color: var(--muted); font-size: .9rem; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
      gap: 14px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 2px 8px rgba(0,0,0,.25);
    }}
    h1 {{ margin: 0 0 6px 0; }}
    h2 {{ margin: 0 0 8px 0; font-size: 1.1rem; }}
    h3 {{ margin-bottom: 6px; font-size: .95rem; }}
    ul {{ padding-left: 20px; }}
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
  <div class="top">
    <h1>AI Watchdog Dependency Map</h1>
    <div class="muted">Updated: {html.escape(updated)} · Auto-refreshes every 5 minutes</div>
    <p>
      <a href="dashboard.html">Watchdog Dashboard</a> ·
      <a href="dependencies.json">Dependency JSON</a> ·
      <a href="runbooks/index.html">Recovery Runbooks</a>
    </p>
    <p>
      Checked OK: {summary['ok']} ·
      Attention: {summary['attention']} ·
      Unchecked: {summary['unchecked']}
    </p>
  </div>
  <div class="grid">
    {''.join(cards)}
  </div>
</body>
</html>
"""

(PUBLIC / "dependencies.html").write_text(html_doc)

print(f"Dependency map written to: {PUBLIC / 'dependencies.html'}")
print(f"Dependency JSON:          {PUBLIC / 'dependencies.json'}")
print(f"Checked OK: {summary['ok']}  Attention: {summary['attention']}  Unchecked: {summary['unchecked']}")
