# Node-RED Flow Problem

Node-RED is missing an expected critical tab, changed structure, or the watchdog cannot read the live flow file.

Check Node-RED:

docker ps --filter name=nodered
docker logs --tail 100 nodered

Node-RED runs in projects mode, so the live flows are in the active project
(/data/projects/Home_AI_system/flows.json), not /data/flows.json. The old
top-level copy is /data/flows-old.json and is not read by anything. Find the
live path:

~/ai-watchdog/scripts/nodered_flows_path.sh

Show live tabs:

docker exec nodered node -e "const fs=require('fs'); const j=JSON.parse(fs.readFileSync('$(~/ai-watchdog/scripts/nodered_flows_path.sh)','utf8')); console.log(j.filter(n=>n.type==='tab').map(n=>n.label).sort().join('\n'));"

Rerun watchdog:

~/ai-watchdog/scripts/watchdog_nodered_v1.sh
grep -A40 "## Attention Needed" "$(ls -t ~/ai-watchdog/reports/watchdog-nodered-*.md | head -1)"

If a tab was intentionally renamed:

nano ~/ai-watchdog/config/nodered_critical_tabs.txt
