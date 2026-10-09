# Inventory page

Rebuilds the "Home AI Infrastructure Inventory" page: each machine's CPU, RAM, disks, GPUs and what runs on it.
Everything is read-only; no secrets are collected (no environment variables, no unit files, cron lines reduced
to schedule + script name).

1. Collect, one file per machine (keys: main, epyc, z4, livingroom, bedroom, jellyfin, skycam, vps):

       mkdir -p inventory-data
       ssh apex-agent-server    python3 - < scripts/inventory/collect_host.py > inventory-data/main.json
       ssh apex-agent-second    python3 - < scripts/inventory/collect_host.py > inventory-data/epyc.json
       ssh apex-agent-z4        python3 - < scripts/inventory/collect_host.py > inventory-data/z4.json
       ssh apex-agent-livingroom python3 - < scripts/inventory/collect_host.py > inventory-data/livingroom.json
       ssh apex-agent-bedroom   python3 - < scripts/inventory/collect_host.py > inventory-data/bedroom.json
       ssh apex-agent-jellyfin  python3 - < scripts/inventory/collect_host.py > inventory-data/jellyfin.json
       ssh apex-agent-skycam    python3 - < scripts/inventory/collect_host.py > inventory-data/skycam.json
       ssh searxng-vps          python3 - < scripts/inventory/collect_host.py > inventory-data/vps.json

2. Home Assistant (through its own API, run on the main server; the token is read from the nodered container):

       ssh apex-agent-server 'HA_TOKEN=$(docker exec nodered printenv HA_TOKEN) python3 -' < scripts/inventory/collect_ha.py > inventory-data/ha.json

3. Build the page (writes a page fragment: title + style + body):

       python scripts/inventory/build_page.py --data inventory-data --out inventory_page.html

4. Publish `inventory_page.html` as the artifact "Home AI Infrastructure Inventory".

The "Backups" table and the "Needs a look" list in build_page.py are written by hand; update them when they change.
Container descriptions come from CONTAINER_DESC in build_page.py (unknown containers show their image name).
