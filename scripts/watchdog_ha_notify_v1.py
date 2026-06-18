#!/usr/bin/env python3
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json

BASE = Path.home() / "ai-watchdog"
PUBLIC = BASE / "public"
CONFIG = BASE / "config"
HA_ENV = CONFIG / "ha_token.env"
NOTIFY_CONF = CONFIG / "watchdog_notify.conf"

def parse_env_file(path):
    data = {}
    if not path.exists():
        return data

    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data

def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(errors="replace"))
    except Exception:
        return default

def as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")

def post_ha_service(base_url, token, domain, service, payload):
    url = f"{base_url.rstrip('/')}/api/services/{domain}/{service}"
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=15) as r:
        body = r.read().decode("utf-8", errors="replace")
        return r.status, body

ha = parse_env_file(HA_ENV)
conf = parse_env_file(NOTIFY_CONF)

enabled = as_bool(conf.get("WATCHDOG_NOTIFY_ENABLED"), True)
notify_clean = as_bool(conf.get("WATCHDOG_NOTIFY_CLEAN"), False)
notify_updates = as_bool(conf.get("WATCHDOG_NOTIFY_UPDATES"), True)
dismiss_when_clean = as_bool(conf.get("WATCHDOG_NOTIFY_DISMISS_WHEN_CLEAN"), True)

notification_id = conf.get("WATCHDOG_NOTIFY_ID", "ai_watchdog_daily_brief")
title = conf.get("WATCHDOG_NOTIFY_TITLE", "AI Watchdog Brief")

if not enabled:
    print("HA watchdog notifications disabled.")
    raise SystemExit(0)

base_url = ha.get("HA_BASE_URL", "").rstrip("/")
token = ha.get("HA_TOKEN", "")

if not base_url or not token:
    print("Missing HA_BASE_URL or HA_TOKEN in config/ha_token.env")
    raise SystemExit(1)

brief = read_json(PUBLIC / "brief.json", {})
current = brief.get("current", {}) or {}

attention_items = brief.get("attention_items", []) or []
available_updates = brief.get("available_updates", []) or []
recommended_first = brief.get("recommended_first") or "dashboard.html"
status = str(brief.get("status", "unknown")).lower()

ha_update_count = int(current.get("ha_update_count") or 0)
action_hint_count = int(current.get("action_hint_count") or 0)
change_attention_count = int(current.get("change_attention_count") or 0)
update_attention_count = int(current.get("update_attention_count") or 0)

dep_summary = current.get("dependency_summary") or {}
dep_attention = int(dep_summary.get("attention") or 0)

worthy = False
reasons = []

if status != "ok":
    worthy = True
    reasons.append(f"Brief status is {status}")

if attention_items:
    worthy = True
    reasons.extend(str(x) for x in attention_items[:8])

if action_hint_count:
    worthy = True
    reasons.append(f"Action hints available: {action_hint_count}")

if dep_attention:
    worthy = True
    reasons.append(f"Dependency attention items: {dep_attention}")

if change_attention_count:
    worthy = True
    reasons.append(f"Change-detection attention items: {change_attention_count}")

if update_attention_count:
    worthy = True
    reasons.append(f"Update-monitor attention items: {update_attention_count}")

if notify_updates and ha_update_count:
    worthy = True
    reasons.append(f"HA updates available: {ha_update_count}")

if not worthy and not notify_clean:
    if dismiss_when_clean:
        try:
            post_ha_service(
                base_url,
                token,
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
            )
            print("System clean. Existing watchdog notification dismissed if present.")
        except HTTPError as e:
            if e.code == 404:
                print("System clean. No existing notification to dismiss.")
            else:
                print(f"System clean, but dismiss failed: HTTP {e.code}")
        except Exception as e:
            print(f"System clean, but dismiss failed: {e}")
    else:
        print("System clean. No HA notification sent.")
    raise SystemExit(0)

reason_lines = []
if reasons:
    reason_lines = [f"- {x}" for x in reasons[:12]]
else:
    reason_lines = ["- No attention items."]

update_lines = []
if available_updates:
    for u in available_updates[:10]:
        name = u.get("friendly_name") or u.get("entity_id")
        installed = u.get("installed_version")
        latest = u.get("latest_version")
        update_lines.append(f"- {name}: `{installed}` -> `{latest}`")
else:
    update_lines.append("- No HA update entities currently report available updates.")

links = [
    "- Brief: `/watchdog/brief.html`",
    f"- Recommended: `/watchdog/{recommended_first}`",
    "- Dashboard: `/watchdog/dashboard.html`",
    "- Updates: `/watchdog/updates.html`",
    "- Changes: `/watchdog/changes.html`",
    "- Runbooks: `/watchdog/runbooks/index.html`",
]

message = "\n".join([
    f"Status: **{status.upper()}**",
    "",
    f"Recommended first page: **{recommended_first}**",
    "",
    "Attention / reasons:",
    "\n".join(reason_lines),
    "",
    "Available updates:",
    "\n".join(update_lines),
    "",
    "Links:",
    "\n".join(links),
])

payload = {
    "title": title,
    "message": message,
    "notification_id": notification_id,
}

try:
    status_code, body = post_ha_service(
        base_url,
        token,
        "persistent_notification",
        "create",
        payload,
    )
    print(f"HA persistent notification sent or updated. HTTP {status_code}")
    print(f"Notification ID: {notification_id}")
except Exception as e:
    print(f"Failed to send HA persistent notification: {e}")
    raise SystemExit(1)
