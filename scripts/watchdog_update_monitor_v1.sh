#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
CONF="$BASE/config/watchdog_update_monitor.conf"
HA_ENV="$BASE/config/ha_token.env"

STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/updates/$STAMP"
REPORT="$BASE/reports/watchdog-updates-$STAMP.md"
PUBLIC="$BASE/public"

mkdir -p "$OUT" "$BASE/reports" "$PUBLIC"

UPDATE_CHECK_APT="1"
UPDATE_APT_MAX_LINES="80"
UPDATE_HA_UPDATES_ARE_ATTENTION="0"
UPDATE_APT_UPGRADES_ARE_ATTENTION="0"
UPDATE_GIT_DIRTY_IS_ATTENTION="1"

[ -f "$CONF" ] && source "$CONF"

ATTENTION="$OUT/attention-needed.txt"
: > "$ATTENTION"

add_attention() {
  echo "- $1" >> "$ATTENTION"
}

echo "# AI Watchdog Update Monitor v1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Home Assistant update entities
# ------------------------------------------------------------
echo "## Home Assistant Update Entities" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

if [ -f "$HA_ENV" ]; then
  source "$HA_ENV"

  python3 - "$HA_BASE_URL" "$HA_TOKEN" "$OUT/ha-updates.json" "$ATTENTION" "$UPDATE_HA_UPDATES_ARE_ATTENTION" <<'PY'
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json
import sys

base_url = sys.argv[1].rstrip("/")
token = sys.argv[2]
out_path = sys.argv[3]
attention_path = sys.argv[4]
updates_are_attention = sys.argv[5] == "1"

def add_attention(msg):
    with open(attention_path, "a") as f:
        f.write(f"- {msg}\n")

req = Request(
    f"{base_url}/api/states",
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
)

result = {
    "ok": False,
    "total_update_entities": 0,
    "available_updates": [],
    "unavailable_update_entities": [],
    "error": "",
}

try:
    with urlopen(req, timeout=15) as r:
        states = json.loads(r.read().decode("utf-8", errors="replace"))

    updates = [s for s in states if str(s.get("entity_id", "")).startswith("update.")]
    result["total_update_entities"] = len(updates)

    for s in updates:
        ent = s.get("entity_id")
        state = s.get("state")
        attrs = s.get("attributes", {})
        item = {
            "entity_id": ent,
            "state": state,
            "friendly_name": attrs.get("friendly_name"),
            "installed_version": attrs.get("installed_version"),
            "latest_version": attrs.get("latest_version"),
            "release_summary": attrs.get("release_summary"),
            "title": attrs.get("title"),
        }

        if state == "on":
            result["available_updates"].append(item)
            if updates_are_attention:
                add_attention(f"Home Assistant update available: {ent} {item.get('installed_version')} -> {item.get('latest_version')}")
        elif state in ("unavailable", "unknown"):
            result["unavailable_update_entities"].append(item)

    result["ok"] = True
except Exception as e:
    result["error"] = repr(e)
    add_attention(f"Could not read Home Assistant update entities: {e}")

with open(out_path, "w") as f:
    json.dump(result, f, indent=2, sort_keys=True)

print(json.dumps(result, indent=2, sort_keys=True))
PY

  cat "$OUT/ha-updates.json" >> "$REPORT" 2>/dev/null
else
  add_attention "HA token env file missing; cannot check Home Assistant update entities."
  echo "HA token env file missing: $HA_ENV" >> "$REPORT"
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Docker containers/images
# ------------------------------------------------------------
echo "## Docker Container Image Inventory" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' \
  | sort > "$OUT/docker-containers.tsv" 2>"$OUT/docker-containers-error.txt" || true

if [ -s "$OUT/docker-containers.tsv" ]; then
  cat "$OUT/docker-containers.tsv" >> "$REPORT"
else
  add_attention "Could not list Docker containers for update inventory."
  cat "$OUT/docker-containers-error.txt" >> "$REPORT" 2>/dev/null
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

echo "## Docker Image Created Dates" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

python3 - "$OUT/docker-containers.tsv" "$OUT/docker-image-created.tsv" <<'PY'
from pathlib import Path
import subprocess
import sys

containers = Path(sys.argv[1])
out = Path(sys.argv[2])

images = []
if containers.exists():
    for line in containers.read_text(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            images.append(parts[1])

images = sorted(set(images))
rows = []

for image in images:
    try:
        p = subprocess.run(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}\t{{.Created}}\t{{.Size}}"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if p.returncode == 0:
            rows.append(f"{image}\t{p.stdout.strip()}")
        else:
            rows.append(f"{image}\tinspect failed\t{p.stderr.strip()}")
    except Exception as e:
        rows.append(f"{image}\tinspect exception\t{repr(e)}")

out.write_text("\n".join(rows) + ("\n" if rows else ""))
PY

cat "$OUT/docker-image-created.tsv" >> "$REPORT" 2>/dev/null

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Node-RED packages
# ------------------------------------------------------------
echo "## Node-RED Package Versions" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

docker exec nodered sh -c 'cat /data/package.json 2>/dev/null' > "$OUT/nodered-package.json" 2>"$OUT/nodered-package-error.txt" || true

if [ -s "$OUT/nodered-package.json" ]; then
  python3 - "$OUT/nodered-package.json" "$OUT/nodered-package-summary.txt" <<'PY'
from pathlib import Path
import json
import sys

p = Path(sys.argv[1])
out = Path(sys.argv[2])

data = json.loads(p.read_text(errors="replace"))
lines = []

for section in ("dependencies", "devDependencies"):
    deps = data.get(section, {})
    if deps:
        lines.append(section + ":")
        for k in sorted(deps):
            lines.append(f"  {k}: {deps[k]}")
        lines.append("")

out.write_text("\n".join(lines) + "\n")
PY
  cat "$OUT/nodered-package-summary.txt" >> "$REPORT"
else
  echo "Could not read Node-RED /data/package.json." >> "$REPORT"
  cat "$OUT/nodered-package-error.txt" >> "$REPORT" 2>/dev/null
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Frigate version
# ------------------------------------------------------------
echo "## Frigate Version" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

python3 - "$OUT/frigate-version.json" <<'PY'
from urllib.request import urlopen, Request
import json
import sys

out = sys.argv[1]
urls = [
    "http://10.0.0.35:5000/api/version",
    "http://10.0.0.35:5000/api/stats",
]

result = {"ok": False, "version": None, "source": None, "error": ""}

for url in urls:
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = body.strip()

        if isinstance(data, str):
            result.update({"ok": True, "version": data, "source": url})
            break

        version = data.get("version") or data.get("service", {}).get("version")
        if version:
            result.update({"ok": True, "version": version, "source": url})
            break

        if url.endswith("/api/stats"):
            result.update({"ok": True, "version": "version not present in stats", "source": url})
            break
    except Exception as e:
        result["error"] = repr(e)

with open(out, "w") as f:
    json.dump(result, f, indent=2, sort_keys=True)

print(json.dumps(result, indent=2, sort_keys=True))
PY

cat "$OUT/frigate-version.json" >> "$REPORT" 2>/dev/null

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# APT local-cache upgrades
# ------------------------------------------------------------
echo "## APT Local-Cache Upgradable Packages" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

if [ "$UPDATE_CHECK_APT" = "1" ]; then
  apt list --upgradable 2>/dev/null \
    | sed '1d' \
    | head -n "$UPDATE_APT_MAX_LINES" \
    > "$OUT/apt-upgradable.txt" || true

  APT_COUNT="$(apt list --upgradable 2>/dev/null | sed '1d' | grep -c . || true)"
  echo "Total upgradable packages from local apt cache: $APT_COUNT" >> "$REPORT"
  echo "" >> "$REPORT"

  if [ "$APT_COUNT" != "0" ] && [ "$UPDATE_APT_UPGRADES_ARE_ATTENTION" = "1" ]; then
    add_attention "APT has $APT_COUNT locally-known upgradable packages."
  fi

  cat "$OUT/apt-upgradable.txt" >> "$REPORT"
else
  echo "APT check disabled." >> "$REPORT"
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Git state
# ------------------------------------------------------------
echo "## Watchdog Git State" >> "$REPORT"
echo "" >> "$REPORT"
echo '```' >> "$REPORT"

git -C "$BASE" status -sb > "$OUT/git-status-branch.txt" 2>&1 || true
git -C "$BASE" status --short > "$OUT/git-status-short.txt" 2>&1 || true
cat "$OUT/git-status-branch.txt" >> "$REPORT"
echo "" >> "$REPORT"
cat "$OUT/git-status-short.txt" >> "$REPORT"

if [ -s "$OUT/git-status-short.txt" ] && [ "$UPDATE_GIT_DIRTY_IS_ATTENTION" = "1" ]; then
  add_attention "ai-watchdog Git repo has uncommitted changes during update monitoring."
fi

echo '```' >> "$REPORT"
echo "" >> "$REPORT"

# ------------------------------------------------------------
# Public JSON/HTML
# ------------------------------------------------------------
python3 - "$OUT" "$REPORT" "$PUBLIC/updates.json" "$PUBLIC/updates.html" <<'PY'
from pathlib import Path
from datetime import datetime
import html
import json
import sys

out = Path(sys.argv[1])
report = Path(sys.argv[2])
json_out = Path(sys.argv[3])
html_out = Path(sys.argv[4])

def load_json(name, default):
    try:
        return json.loads((out / name).read_text(errors="replace"))
    except Exception:
        return default

def read(name):
    try:
        return (out / name).read_text(errors="replace")
    except Exception:
        return ""

ha = load_json("ha-updates.json", {})
frigate = load_json("frigate-version.json", {})
docker_containers = read("docker-containers.tsv")
node_red = read("nodered-package-summary.txt")
apt = read("apt-upgradable.txt")
git_status = read("git-status-short.txt")
attention = read("attention-needed.txt")

data = {
    "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
    "report": str(report),
    "ha_update_entities": ha,
    "frigate_version": frigate,
    "apt_upgradable_count": len([x for x in apt.splitlines() if x.strip()]),
    "git_dirty": bool(git_status.strip()),
    "attention": [x for x in attention.splitlines() if x.strip()],
}

json_out.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

def esc(x):
    return html.escape(str(x if x is not None else ""))

ha_available = ha.get("available_updates", []) if isinstance(ha, dict) else []
ha_rows = ""
if ha_available:
    for u in ha_available:
        ha_rows += f"<li>{esc(u.get('friendly_name') or u.get('entity_id'))}: {esc(u.get('installed_version'))} → {esc(u.get('latest_version'))}</li>"
else:
    ha_rows = "<li>No HA update entities currently report available updates.</li>"

attention_html = ""
if data["attention"]:
    attention_html = "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in data["attention"]) + "</ul>"
else:
    attention_html = "<p>No update-monitor attention items.</p>"

html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="300">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog Updates</title>
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
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 14px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    .muted {{ color: var(--muted); font-size: .9rem; }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #10131a;
      border: 1px solid var(--line);
      padding: 10px;
      border-radius: 10px;
      max-height: 360px;
      overflow: auto;
    }}
  </style>
</head>
<body>
  <h1>AI Watchdog Update Monitor</h1>
  <div class="muted">Updated: {esc(data['updated'])}</div>
  <p>
    <a href="dashboard.html">Dashboard</a> ·
    <a href="history.html">History</a> ·
    <a href="updates.json">Updates JSON</a> ·
    <a href="{esc(report.name)}">Report file</a>
  </p>

  <div class="grid">
    <div class="card">
      <h2>Attention</h2>
      {attention_html}
    </div>

    <div class="card">
      <h2>Home Assistant Updates</h2>
      <p>Total update entities: {esc(ha.get('total_update_entities', 'unknown') if isinstance(ha, dict) else 'unknown')}</p>
      <ul>{ha_rows}</ul>
    </div>

    <div class="card">
      <h2>Frigate</h2>
      <p>Version/source: {esc(frigate.get('version'))} / {esc(frigate.get('source'))}</p>
    </div>

    <div class="card">
      <h2>APT</h2>
      <p>Local-cache upgradable packages shown: {esc(data['apt_upgradable_count'])}</p>
      <pre>{esc(apt or 'No packages listed.')}</pre>
    </div>

    <div class="card">
      <h2>Docker Containers</h2>
      <pre>{esc(docker_containers)}</pre>
    </div>

    <div class="card">
      <h2>Node-RED Packages</h2>
      <pre>{esc(node_red or 'No package summary found.')}</pre>
    </div>
  </div>
</body>
</html>
"""
html_out.write_text(html_doc)
PY

echo "## Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$ATTENTION" ]; then
  cat "$ATTENTION" >> "$REPORT"
else
  echo "No update-monitor attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Update monitor snapshot saved to: $OUT"
echo "Update monitor report saved to:   $REPORT"
echo "Update page written to:           $PUBLIC/updates.html"
