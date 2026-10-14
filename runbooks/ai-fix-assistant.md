# AI Fix Assistant

Every warning or critical item on the dashboard gets an analysis from the local LLM on the EPYC box (qwen3.6-coder, the same model OpenCode uses). The analysis lists what is wrong, the likely cause, a fix plan with exact commands, and how to check the fix worked. Added 2026-10-14.

## Where to find it

- On the dashboard, a problem row shows a blue **AI fix →** chip (grey **AI analysing** while it is being written). Expanding the row shows the same link.
- The HA notification "AI Watchdog: N problem(s) analysed" lists each analysis with a link. The usual "Attention Needed" notification links to the list too.
- All analyses: /watchdog/fixes/index.html (home network or VPN only, like the rest of /watchdog/).

## The analysis page

- **What's wrong / Likely cause / Confidence**: low confidence means the evidence was thin. The first steps are then read-only checks that would settle it.
- **Proposed fix**: each step is tagged INSPECT (read-only), CHANGE (edits a config or restarts one service), DISRUPTIVE (reboot, deletes data, whole-house impact) or MANUAL (needs hands). Commands have a copy button.
- **Evidence the AI saw**: the check output, container/unit status and logs, the matching runbook and hint. If the analysis looks wrong, check here first.
- **Fix it with AI →** opens a new tab with an OpenCode session on the EPYC box (http://10.0.0.188:4096, its usual password), already loaded with this analysis. The "fixer" agent:
  - investigates freely with read-only tools (remote_inspect, read_remote_file): no prompts;
  - asks before **every** shell command and every file edit. Click Allow or Deny in OpenCode, or type to steer it ("don't restart it now", "check the NAS first");
  - verifies with `watchdog_ai_fix.py --recheck <id>` and ends with a summary of what changed and how to undo it.
- **Re-analyse with my note**: re-runs the first-pass analysis with your correction (for example "that container is meant to be stopped"). The page refreshes itself when the new analysis is ready (about a minute).

## How it runs

- `scripts/watchdog_ai_fix.py` runs in the background at the end of every watchdog run (every 6 hours). It analyses up to 6 new problems per run (about a minute each), most severe first. Log: `logs/ai-fix.log`.
- An open problem is analysed once, then again after 24 hours or when it clears and comes back. Settings: `config/watchdog_ai_fix.conf`.
- `scripts/watchdog_web.py` serves the /watchdog/ pages (it replaced `python3 -m http.server`; unit `ai-watchdog-web.service`). It also handles the two buttons and only accepts them from the watchdog's own pages.
- On the EPYC box: agent `fixer` in `~/.config/opencode/opencode.jsonc` (prompt in `prompts/fixer.md`), tool `tools/remote_inspect.ts`. Sessions live in `/home/davids1/fix-sessions`. The OpenCode password is copied into `config/opencode_web.env` on the main server (not in git).

## When something is off

- **No AI chip on a problem**: check whether the background run finished (`tail -30 ~/ai-watchdog/logs/ai-fix.log`). Run it by hand: `python3 ~/ai-watchdog/scripts/watchdog_ai_fix.py`. To see what it would send without calling the model: `--dry-run`.
- **"No analysis: could not reach the model"**: Ollama on the EPYC box. Check `ssh apex-agent-second 'docker ps --filter name=ollama-code; curl -s localhost:11434/api/ps'`.
- **Fix it with AI shows "Could not start the fix session"**: `ssh apex-agent-second 'systemctl status opencode-web'`. If the OpenCode password changed, re-copy it into `config/opencode_web.env`.
- **Turn it off**: set `AI_FIX_ENABLED="0"` in `config/watchdog_ai_fix.conf`.

The first-pass analysis comes from a local model and can be wrong. Nothing changes a system until you approve it in OpenCode or run a command yourself.
