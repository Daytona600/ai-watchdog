"""Read-only inventory of the Home Assistant OS box through HA's own APIs. Runs ON the main server.
Needs HA_TOKEN in the environment (the caller reads it from the nodered container; it is never printed or stored).
"""
import json
import os
import sys

import websocket

tok = os.environ.get("HA_TOKEN", "").strip()
if not tok:
    sys.exit("HA_TOKEN missing")
ws = websocket.create_connection("ws://10.0.0.30:8123/api/websocket", timeout=40)
assert json.loads(ws.recv())["type"] == "auth_required"
ws.send(json.dumps({"type": "auth", "access_token": tok}))
assert json.loads(ws.recv())["type"] == "auth_ok"
_n = [0]


def q(payload):
    _n[0] += 1
    ws.send(json.dumps(dict(payload, id=_n[0])))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == _n[0]:
            return r


def sup(endpoint):
    r = q({"type": "supervisor/api", "endpoint": endpoint, "method": "get"})
    return r.get("result") if r.get("success") else {"error": (r.get("error") or {}).get("message")}


def pick(d, keys):
    d = d if isinstance(d, dict) else {}
    return {k: d.get(k) for k in keys}


out = {
    "core": pick(sup("/core/info"), ("version", "machine", "arch", "update_available")),
    "os": pick(sup("/os/info"), ("version", "board", "update_available")),
    "host": pick(sup("/host/info"), ("hostname", "operating_system", "kernel", "disk_total", "disk_used", "disk_free", "chassis", "deployment")),
    "supervisor": pick(sup("/supervisor/info"), ("version", "channel", "arch", "healthy", "supported")),
}
ad = sup("/addons")
addons = ad.get("addons") if isinstance(ad, dict) else None
out["addons"] = [{"name": a.get("name"), "state": a.get("state"), "version": a.get("version")} for a in (addons or [])]
cfg = q({"type": "get_config"}).get("result") or {}
out["ha"] = {"version": cfg.get("version"), "components": len(cfg.get("components", [])), "time_zone": cfg.get("time_zone")}
states = q({"type": "get_states"}).get("result") or []
out["entities"] = len(states)
out["system_monitor"] = {s["entity_id"]: s["state"] for s in states if s["entity_id"].startswith("sensor.system_monitor_")}
out["integrations_with_most_entities"] = {}
print(json.dumps(out, indent=1))
