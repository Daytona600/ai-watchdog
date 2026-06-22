#!/usr/bin/env python3
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
import hashlib
import html
import json
import os
import re
import socket
import subprocess
import sys

BASE = Path.home() / "ai-watchdog"
SNAP_DIR = BASE / "change-snapshots"
STATE_DIR = BASE / "state"
REPORT_DIR = BASE / "reports"
PUBLIC = BASE / "public"

for p in (SNAP_DIR, STATE_DIR, REPORT_DIR, PUBLIC):
    p.mkdir(parents=True, exist_ok=True)

WINDOW_FILE = STATE_DIR / "watchdog-change-window.json"

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def stamp():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def safe_label(label):
    label = label or "manual"
    label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
    return label or "manual"

def run(cmd, timeout=20):
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return {
            "ok": p.returncode == 0,
            "rc": p.returncode,
            "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip(),
            "cmd": cmd,
        }
    except Exception as e:
        return {
            "ok": False,
            "rc": -1,
            "stdout": "",
            "stderr": repr(e),
            "cmd": cmd,
        }

def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

def parse_env(path):
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        env[k.strip()] = v
    return env

def http_json(url, token=None, timeout=15):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except Exception:
        return body.strip()

def read_public_json(name):
    p = PUBLIC / name
    try:
        return json.loads(p.read_text(errors="replace"))
    except Exception:
        return {}

def capture_ha():
    env = parse_env(BASE / "config" / "ha_token.env")
    base_url = env.get("HA_BASE_URL", "").rstrip("/")
    token = env.get("HA_TOKEN", "")

    result = {
        "ok": False,
        "error": "",
        "version": None,
        "location_name": None,
        "state_count": 0,
        "domain_counts": {},
        "unavailable_count": 0,
        "unknown_count": 0,
        "unavailable_entities": [],
        "unknown_entities": [],
        "available_updates": [],
        "critical_entities": {},
    }

    if not base_url or not token:
        result["error"] = "Missing HA_BASE_URL or HA_TOKEN"
        return result

    try:
        cfg = http_json(f"{base_url}/api/config", token=token)
        result["version"] = cfg.get("version") if isinstance(cfg, dict) else None
        result["location_name"] = cfg.get("location_name") if isinstance(cfg, dict) else None
    except Exception as e:
        result["error"] = f"config error: {e}"

    try:
        states = http_json(f"{base_url}/api/states", token=token)
        if not isinstance(states, list):
            result["error"] = "states endpoint did not return a list"
            return result

        result["state_count"] = len(states)
        by_entity = {}

        for s in states:
            ent = s.get("entity_id", "")
            state = s.get("state")
            attrs = s.get("attributes", {})
            by_entity[ent] = {
                "state": state,
                "friendly_name": attrs.get("friendly_name"),
            }

            domain = ent.split(".", 1)[0] if "." in ent else "unknown"
            result["domain_counts"][domain] = result["domain_counts"].get(domain, 0) + 1

            if state == "unavailable":
                result["unavailable_entities"].append(ent)
            if state == "unknown":
                result["unknown_entities"].append(ent)

            if ent.startswith("update.") and state == "on":
                result["available_updates"].append({
                    "entity_id": ent,
                    "friendly_name": attrs.get("friendly_name"),
                    "installed_version": attrs.get("installed_version"),
                    "latest_version": attrs.get("latest_version"),
                    "title": attrs.get("title"),
                })

        result["unavailable_entities"] = sorted(result["unavailable_entities"])
        result["unknown_entities"] = sorted(result["unknown_entities"])
        result["unavailable_count"] = len(result["unavailable_entities"])
        result["unknown_count"] = len(result["unknown_entities"])
        result["available_updates"] = sorted(result["available_updates"], key=lambda x: x["entity_id"])

        crit_path = BASE / "config" / "ha_critical_entities.txt"
        if crit_path.exists():
            for raw in crit_path.read_text(errors="replace").splitlines():
                ent = raw.strip()
                if not ent or ent.startswith("#"):
                    continue
                result["critical_entities"][ent] = by_entity.get(ent, {
                    "state": "missing",
                    "friendly_name": None,
                })

        result["ok"] = True
    except Exception as e:
        result["error"] = f"states error: {e}"

    return result

def capture_docker():
    result = {
        "ok": False,
        "error": "",
        "containers": {},
        "images": {},
    }

    ps = run("docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'", timeout=20)
    if not ps["ok"]:
        result["error"] = ps["stderr"] or ps["stdout"]
        return result

    for line in ps["stdout"].splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, image, status = parts[0], parts[1], parts[2]
        result["containers"][name] = {
            "image": image,
            "status": status,
        }

    images = sorted({v["image"] for v in result["containers"].values()})
    for image in images:
        ins = run(f"docker image inspect {image} --format '{{{{.Id}}}}\t{{{{.Created}}}}\t{{{{.Size}}}}'", timeout=20)
        if ins["ok"]:
            parts = ins["stdout"].split("\t")
            result["images"][image] = {
                "id": parts[0] if len(parts) > 0 else "",
                "created": parts[1] if len(parts) > 1 else "",
                "size": parts[2] if len(parts) > 2 else "",
            }
        else:
            result["images"][image] = {
                "id": "",
                "created": "",
                "size": "",
                "error": ins["stderr"] or ins["stdout"],
            }

    result["ok"] = True
    return result

def capture_nodered():
    result = {
        "ok": False,
        "error": "",
        "flow_sha256": None,
        "object_count": 0,
        "tab_count": 0,
        "tabs": [],
        "type_counts": {},
        "package_dependencies": {},
    }

    flow = run("docker exec nodered sh -c 'cat /data/flows.json 2>/dev/null'", timeout=20)
    if flow["ok"] and flow["stdout"]:
        result["flow_sha256"] = sha256_text(flow["stdout"])
        try:
            data = json.loads(flow["stdout"])
            result["object_count"] = len(data) if isinstance(data, list) else 0
            for obj in data if isinstance(data, list) else []:
                typ = obj.get("type", "unknown")
                result["type_counts"][typ] = result["type_counts"].get(typ, 0) + 1
                if typ == "tab":
                    result["tabs"].append(obj.get("label", obj.get("id", "unknown")))
            result["tabs"] = sorted(result["tabs"])
            result["tab_count"] = len(result["tabs"])
            result["ok"] = True
        except Exception as e:
            result["error"] = f"flow parse error: {e}"
    else:
        result["error"] = flow["stderr"] or flow["stdout"] or "could not read flows.json"

    pkg = run("docker exec nodered sh -c 'cat /data/package.json 2>/dev/null'", timeout=20)
    if pkg["ok"] and pkg["stdout"]:
        try:
            data = json.loads(pkg["stdout"])
            deps = {}
            for section in ("dependencies", "devDependencies"):
                for k, v in (data.get(section, {}) or {}).items():
                    deps[k] = v
            result["package_dependencies"] = dict(sorted(deps.items()))
        except Exception:
            pass

    return result

def capture_frigate():
    result = {
        "ok": False,
        "error": "",
        "version": None,
        "cameras": {},
    }

    try:
        version = http_json("http://10.0.0.35:5000/api/version", timeout=8)
        result["version"] = version if isinstance(version, str) else json.dumps(version)
    except Exception as e:
        result["error"] = f"version error: {e}"

    try:
        stats = http_json("http://10.0.0.35:5000/api/stats", timeout=10)
        if isinstance(stats, dict):
            cams = stats.get("cameras", {})
            for name, data in cams.items():
                result["cameras"][name] = {
                    "camera_fps": data.get("camera_fps"),
                    "process_fps": data.get("process_fps"),
                    "detection_fps": data.get("detection_fps"),
                    "skipped_fps": data.get("skipped_fps"),
                }
            result["ok"] = True
    except Exception as e:
        result["error"] = (result["error"] + "; " if result["error"] else "") + f"stats error: {e}"

    return result

def capture_apt():
    result = {
        "ok": False,
        "count": 0,
        "packages": [],
        "error": "",
    }
    r = run("apt list --upgradable 2>/dev/null | sed '1d' | head -n 120", timeout=30)
    if r["ok"]:
        pkgs = [x for x in r["stdout"].splitlines() if x.strip()]
        result["packages"] = pkgs
        result["count"] = len(pkgs)
        result["ok"] = True
    else:
        result["error"] = r["stderr"] or r["stdout"]
    return result

def capture_public():
    alert = read_public_json("alert.json")
    deps = read_public_json("dependencies.json")
    updates = read_public_json("updates.json")
    history = read_public_json("history.json")

    return {
        "alert_status": alert.get("status"),
        "alert_attention": alert.get("attention"),
        "alert_message": alert.get("message"),
        "dependency_summary": deps.get("summary", {}),
        "update_attention": updates.get("attention", []),
        "ha_available_update_count": len((updates.get("ha_update_entities") or {}).get("available_updates") or []),
        "history_count": history.get("count"),
    }

def capture(label, phase):
    label = safe_label(label)
    path = SNAP_DIR / f"{stamp()}_{phase}_{label}"
    path.mkdir(parents=True, exist_ok=True)

    summary = {
        "timestamp": now_iso(),
        "label": label,
        "phase": phase,
        "host": socket.gethostname(),
        "system": {
            "uname": run("uname -a")["stdout"],
            "lsb_release": run("lsb_release -ds 2>/dev/null || true")["stdout"],
            "docker_version": run("docker --version 2>/dev/null || true")["stdout"],
        },
        "git": {
            "branch": run(f"git -C {BASE} status -sb")["stdout"],
            "short": run(f"git -C {BASE} status --short")["stdout"],
        },
        "ha": capture_ha(),
        "docker": capture_docker(),
        "nodered": capture_nodered(),
        "frigate": capture_frigate(),
        "apt": capture_apt(),
        "public": capture_public(),
    }

    (path / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return path, summary

def set_diff(before_list, after_list):
    b = set(before_list or [])
    a = set(after_list or [])
    return sorted(a - b), sorted(b - a)

def compare_dict(before, after, prefix=""):
    changes = []
    keys = sorted(set(before.keys()) | set(after.keys()))
    for k in keys:
        b = before.get(k, "<missing>")
        a = after.get(k, "<missing>")
        name = f"{prefix}{k}"
        if b != a:
            changes.append((name, b, a))
    return changes

def load_summary(path):
    p = Path(path)
    if p.is_dir():
        p = p / "summary.json"
    return json.loads(p.read_text(errors="replace"))

def compare(before_path, after_path, label="manual"):
    before = load_summary(before_path)
    after = load_summary(after_path)

    changes = []
    attention = []

    def add(section, item):
        changes.append({"section": section, **item})

    # HA version, counts, criticals.
    b_ha = before.get("ha", {})
    a_ha = after.get("ha", {})

    if b_ha.get("version") != a_ha.get("version"):
        add("Home Assistant", {
            "change": "HA version changed",
            "before": b_ha.get("version"),
            "after": a_ha.get("version"),
        })

    for field in ("state_count", "unavailable_count", "unknown_count"):
        if b_ha.get(field) != a_ha.get(field):
            add("Home Assistant", {
                "change": f"{field} changed",
                "before": b_ha.get(field),
                "after": a_ha.get(field),
            })

    new_unavail, recovered_unavail = set_diff(b_ha.get("unavailable_entities"), a_ha.get("unavailable_entities"))
    if new_unavail:
        add("Home Assistant", {"change": "New unavailable entities", "before": "", "after": new_unavail[:50]})
        attention.append(f"New unavailable HA entities after update: {len(new_unavail)}")
    if recovered_unavail:
        add("Home Assistant", {"change": "Recovered unavailable entities", "before": recovered_unavail[:50], "after": ""})

    b_crit = b_ha.get("critical_entities", {})
    a_crit = a_ha.get("critical_entities", {})
    for ent in sorted(set(b_crit) | set(a_crit)):
        b_state = (b_crit.get(ent) or {}).get("state")
        a_state = (a_crit.get(ent) or {}).get("state")
        if b_state != a_state:
            add("Home Assistant Critical", {
                "change": ent,
                "before": b_state,
                "after": a_state,
            })
            if a_state in ("unavailable", "unknown", "missing"):
                attention.append(f"Critical HA entity changed to {a_state}: {ent}")

    b_updates = {x.get("entity_id"): x for x in b_ha.get("available_updates", [])}
    a_updates = {x.get("entity_id"): x for x in a_ha.get("available_updates", [])}
    added_updates = sorted(set(a_updates) - set(b_updates))
    cleared_updates = sorted(set(b_updates) - set(a_updates))
    if added_updates:
        add("Updates", {"change": "New HA update entities reporting updates", "before": "", "after": added_updates})
    if cleared_updates:
        add("Updates", {"change": "HA update entities cleared", "before": cleared_updates, "after": ""})

    # Docker containers/images.
    b_cont = before.get("docker", {}).get("containers", {})
    a_cont = after.get("docker", {}).get("containers", {})
    for name in sorted(set(b_cont) | set(a_cont)):
        b = b_cont.get(name)
        a = a_cont.get(name)
        if b != a:
            add("Docker containers", {"change": name, "before": b, "after": a})
            if a is None:
                attention.append(f"Docker container missing after update: {name}")

    # Node-RED
    b_nr = before.get("nodered", {})
    a_nr = after.get("nodered", {})
    for field in ("flow_sha256", "object_count", "tab_count"):
        if b_nr.get(field) != a_nr.get(field):
            add("Node-RED", {"change": field, "before": b_nr.get(field), "after": a_nr.get(field)})

    tabs_added, tabs_removed = set_diff(b_nr.get("tabs"), a_nr.get("tabs"))
    if tabs_added:
        add("Node-RED", {"change": "Tabs added", "before": "", "after": tabs_added})
    if tabs_removed:
        add("Node-RED", {"change": "Tabs removed", "before": tabs_removed, "after": ""})
        attention.append(f"Node-RED tabs removed after update: {', '.join(tabs_removed)}")

    # Frigate
    b_fg = before.get("frigate", {})
    a_fg = after.get("frigate", {})
    if b_fg.get("version") != a_fg.get("version"):
        add("Frigate", {"change": "Version changed", "before": b_fg.get("version"), "after": a_fg.get("version")})

    b_cam = b_fg.get("cameras", {})
    a_cam = a_fg.get("cameras", {})
    for cam in sorted(set(b_cam) | set(a_cam)):
        if b_cam.get(cam) != a_cam.get(cam):
            add("Frigate cameras", {"change": cam, "before": b_cam.get(cam), "after": a_cam.get(cam)})

    # APT count
    if before.get("apt", {}).get("count") != after.get("apt", {}).get("count"):
        add("APT", {
            "change": "Upgradable package count changed",
            "before": before.get("apt", {}).get("count"),
            "after": after.get("apt", {}).get("count"),
        })

    # Public watchdog state.
    b_pub = before.get("public", {})
    a_pub = after.get("public", {})
    for field in ("alert_status", "alert_attention", "alert_message", "dependency_summary"):
        if b_pub.get(field) != a_pub.get(field):
            add("Watchdog", {"change": field, "before": b_pub.get(field), "after": a_pub.get(field)})

    if a_pub.get("alert_attention") is True:
        attention.append(f"Watchdog alert active after update: {a_pub.get('alert_message')}")

    report_stamp = stamp()
    report = REPORT_DIR / f"watchdog-change-{report_stamp}.md"
    data = {
        "updated": now_iso(),
        "label": safe_label(label),
        "before_path": str(before_path),
        "after_path": str(after_path),
        "change_count": len(changes),
        "attention": attention,
        "changes": changes,
    }

    (PUBLIC / "changes.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    lines = []
    lines.append("# AI Watchdog Change Report")
    lines.append("")
    lines.append(f"Date: {data['updated']}")
    lines.append(f"Label: {data['label']}")
    lines.append(f"Before: `{before_path}`")
    lines.append(f"After: `{after_path}`")
    lines.append(f"Changes found: {len(changes)}")
    lines.append("")
    lines.append("## Attention")
    lines.append("")
    if attention:
        lines.extend(f"- {x}" for x in attention)
    else:
        lines.append("No change-detection attention items.")
    lines.append("")
    lines.append("## Changes")
    lines.append("")
    if not changes:
        lines.append("No meaningful changes detected.")
    else:
        for c in changes:
            lines.append(f"### {c['section']}: {c['change']}")
            lines.append("")
            lines.append("Before:")
            lines.append("```json")
            lines.append(json.dumps(c.get("before"), indent=2, sort_keys=True))
            lines.append("```")
            lines.append("")
            lines.append("After:")
            lines.append("```json")
            lines.append(json.dumps(c.get("after"), indent=2, sort_keys=True))
            lines.append("```")
            lines.append("")
    report.write_text("\n".join(lines) + "\n")

    make_html(data, report)
    return report, data

def esc(x):
    return html.escape(str(x if x is not None else ""))

def make_html(data, report):
    rows = []
    for c in data["changes"][:120]:
        rows.append(f"""
        <tr>
          <td>{esc(c.get('section'))}</td>
          <td>{esc(c.get('change'))}</td>
          <td><pre>{esc(json.dumps(c.get('before'), indent=2, sort_keys=True))}</pre></td>
          <td><pre>{esc(json.dumps(c.get('after'), indent=2, sort_keys=True))}</pre></td>
        </tr>
        """)

    att = data.get("attention") or []
    if att:
        att_html = "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in att) + "</ul>"
    else:
        att_html = "<p>No change-detection attention items.</p>"

    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Change Detection</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #171a21;
      --text: #e8e8e8;
      --muted: #a8b0bf;
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
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin: 14px 0;
    }}
    .muted {{ color: var(--muted); font-size: .9rem; }}
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
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 220px;
      overflow: auto;
      background: #10131a;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px;
    }}
  </style>
</head>
<body>
  <h1>AI Watchdog Change Detection</h1>
  <div class="muted">Updated: {esc(data['updated'])}</div>
  <p>
    <a href="dashboard.html">Dashboard</a> ·
    <a href="updates.html">Update monitor</a> ·
    <a href="history.html">History</a> ·
    <a href="changes.json">Changes JSON</a>
  </p>

  <div class="card">
    <h2>Summary</h2>
    <p>Label: <strong>{esc(data['label'])}</strong></p>
    <p>Changes found: <strong>{esc(data['change_count'])}</strong></p>
    <p>Report: <code>{esc(report.name)}</code></p>
  </div>

  <div class="card">
    <h2>Attention</h2>
    {att_html}
  </div>

  <table>
    <thead>
      <tr>
        <th>Section</th>
        <th>Change</th>
        <th>Before</th>
        <th>After</th>
      </tr>
    </thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</body>
</html>
"""
    (PUBLIC / "changes.html").write_text(doc)

def publish_waiting_page(label, before_path):
    data = {
        "updated": now_iso(),
        "label": safe_label(label),
        "waiting_for_finish": True,
        "before_path": str(before_path),
    }
    (PUBLIC / "changes.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Change Window</title>
  <style>
    body {{ background:#0f1115; color:#e8e8e8; font-family:system-ui,Segoe UI,Arial,sans-serif; padding:18px; }}
    a {{ color:#8ab4ff; }}
    .card {{ background:#171a21; border:1px solid #2a2f3a; border-radius:14px; padding:14px; }}
  </style>
</head>
<body>
  <h1>AI Watchdog Change Window</h1>
  <p><a href="dashboard.html">Dashboard</a> · <a href="changes.json">Changes JSON</a></p>
  <div class="card">
    <h2>Before snapshot captured</h2>
    <p>Label: <strong>{esc(label)}</strong></p>
    <p>Before path: <code>{esc(before_path)}</code></p>
    <p>Now perform the update manually, then run:</p>
    <pre>~/ai-watchdog/scripts/watchdog_change_window_v1.py finish {esc(label)}</pre>
  </div>
</body>
</html>
"""
    (PUBLIC / "changes.html").write_text(doc)

def cmd_start(label):
    path, summary = capture(label, "before")
    WINDOW_FILE.write_text(json.dumps({
        "label": safe_label(label),
        "before_path": str(path),
        "started": now_iso(),
    }, indent=2) + "\n")
    publish_waiting_page(label, path)
    print(f"Before snapshot captured: {path}")
    print(f"Change window state:      {WINDOW_FILE}")
    print(f"Change page:              {PUBLIC / 'changes.html'}")
    print("")
    print("Now do the update manually, then run:")
    print(f"  ~/ai-watchdog/scripts/watchdog_change_window_v1.py finish {safe_label(label)}")

def cmd_finish(label):
    if not WINDOW_FILE.exists():
        raise SystemExit("No open change window found. Run start first.")

    state = json.loads(WINDOW_FILE.read_text(errors="replace"))
    before_path = Path(state["before_path"])
    label = safe_label(label or state.get("label") or "manual")

    after_path, summary = capture(label, "after")
    report, data = compare(before_path, after_path, label)

    WINDOW_FILE.unlink(missing_ok=True)

    print(f"After snapshot captured:  {after_path}")
    print(f"Change report saved to:   {report}")
    print(f"Change page written to:   {PUBLIC / 'changes.html'}")
    print(f"Changes found:            {data['change_count']}")
    print(f"Attention items:          {len(data['attention'])}")

def cmd_capture(label):
    path, summary = capture(label, "single")
    print(f"Snapshot captured: {path}")

def cmd_compare(before, after, label):
    report, data = compare(Path(before), Path(after), label)
    print(f"Change report saved to: {report}")
    print(f"Change page written to: {PUBLIC / 'changes.html'}")
    print(f"Changes found:          {data['change_count']}")
    print(f"Attention items:        {len(data['attention'])}")

def usage():
    print("Usage:")
    print("  watchdog_change_window_v1.py start LABEL")
    print("  watchdog_change_window_v1.py finish [LABEL]")
    print("  watchdog_change_window_v1.py capture LABEL")
    print("  watchdog_change_window_v1.py compare BEFORE_DIR AFTER_DIR [LABEL]")

def main():
    if len(sys.argv) < 2:
        usage()
        raise SystemExit(1)

    cmd = sys.argv[1]
    if cmd == "start":
        label = sys.argv[2] if len(sys.argv) > 2 else "manual"
        cmd_start(label)
    elif cmd == "finish":
        label = sys.argv[2] if len(sys.argv) > 2 else ""
        cmd_finish(label)
    elif cmd == "capture":
        label = sys.argv[2] if len(sys.argv) > 2 else "manual"
        cmd_capture(label)
    elif cmd == "compare":
        if len(sys.argv) < 4:
            usage()
            raise SystemExit(1)
        label = sys.argv[4] if len(sys.argv) > 4 else "manual"
        cmd_compare(sys.argv[2], sys.argv[3], label)
    else:
        usage()
        raise SystemExit(1)

if __name__ == "__main__":
    main()
