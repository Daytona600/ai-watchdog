# Node-RED Config URL Stale

The "Node-RED Config URLs" check found an address in Node-RED's central config (AI_CONFIG.urls) that nothing answers on.
The nodes read these addresses, so a stale one fails silently: the node waits for a timeout and carries on with an empty
result. (2026-10-09: urls.frigate_base_url still pointed at the old Frigate host for a month, so "any dogs outside?"
answered "No dog currently detected" and alert photos were dead.)

The check output names the key and the address. Find where the service really lives, then change the value in ONE place:
the Postgres row Node-RED loads its config from. Nodes must never carry a literal address of their own.

See the row that is loaded:

curl -s "http://10.0.0.35:3011/config_sections?section_key=eq.urls"

Change one key (edit the key and the new address) and reload the config:

python3 - <<'PY'
import json, urllib.request
base = "http://10.0.0.35:3011/config_sections?section_key=eq.urls"
row = json.load(urllib.request.urlopen(base))[0]["value"]
row["frigate_base_url"] = "http://10.0.0.60:5000"
req = urllib.request.Request(base, data=json.dumps({"value": row}).encode(), method="PATCH",
                             headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).status)
PY
curl -s -X POST http://10.0.0.35:1880/inject/d1bb4e9ee76c87f1

(The inject is "Init AI config"; it reloads AI_CONFIG exactly like a Node-RED restart and prints OK.)

Re-run the check:

python3 ~/ai-watchdog/scripts/watchdog_host_health.py config-urls 10.0.0.35

If an entry is a retired service that no node reads, delete the key from the Postgres row after checking that no node uses it
(planner_url, qdrant_url and thinkpad_music_url were removed that way on 2026-10-09), or, as a last resort, list it with
--ignore KEY on the check's line in config/watchdog_dependency_checks.tsv.

If the check says Node-RED has NO AI_CONFIG loaded, the config loader ("Set AI global config -v2") failed: look for AI00 errors in
`docker logs nodered` (PostgREST down? PGRST_URL missing from the container?) and re-run the "Init AI config" inject (the curl above).
Until the config is loaded every node that needs an address stops with "AI_CONFIG.urls.<key> is not set" instead of guessing.
