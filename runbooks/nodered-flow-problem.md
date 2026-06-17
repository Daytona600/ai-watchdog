# Node-RED Flow Problem

Node-RED is missing an expected critical tab, changed structure, or the watchdog cannot read the live flow file.

Check Node-RED:

docker ps --filter name=nodered
docker logs --tail 100 nodered

Show live tabs:

docker exec nodered node -e "const fs=require('fs'); const j=JSON.parse(fs.readFileSync('/data/flows.json','utf8')); console.log(j.filter(n=>n.type==='tab').map(n=>n.label).sort().join('\n'));"

Rerun watchdog:

~/ai-watchdog/scripts/watchdog_nodered_v1.sh
grep -A40 "## Attention Needed" "$(ls -t ~/ai-watchdog/reports/watchdog-nodered-*.md | head -1)"

If a tab was intentionally renamed:

nano ~/ai-watchdog/config/nodered_critical_tabs.txt
