# OpenCode side of the AI fix assistant (EPYC box)

Copies of what is installed in `~/.config/opencode/` for user davids1 on the EPYC box (10.0.0.188).
They are kept here so the setup is versioned; the live files are the ones OpenCode loads.

- `prompts/fixer.md`: system prompt of the `fixer` agent (Fix it with AI sessions).
- `tools/remote_inspect.ts`: read-only host inspection with fixed command templates (allowed without approval).
- `tools/read_remote_file.ts`: existing tool; path quoting for the remote shell fixed 2026-10-14.

The agent block in `opencode.jsonc` (inserted after the top-level `"model"` line):

```jsonc
"agent": {
  "fixer": {
    "description": "AI Watchdog Fix-it: confirm the cause, fix with approval, verify",
    "mode": "primary",
    "model": "ollama/qwen3.6-coder",
    "prompt": "{file:./prompts/fixer.md}",
    "permission": {
      "bash": { "*": "ask", "*10.0.0.30*": "deny", "*ha.magnumz.com*": "deny", "*homeassistant.local*": "deny", "*:8123*": "deny" },
      "edit": "ask",
      "webfetch": "ask",
      "write_remote_file": "ask",
      "deploy_remote": "ask",
      "nodered_update": "ask",
      "nodered_edit": "ask",
      "remote_inspect": "allow",
      "read_remote_file": "allow",
      "check_file": "allow",
      "nodered_find": "allow",
      "nodered_get": "allow",
      "nodered_check": "allow"
    }
  }
},
```

Hard block on Home Assistant (2026-10-14). OpenCode has no HA token, so any request to HA from the
EPYC counts as a failed login, and five of them got 10.0.0.188 banned. The same four `"deny"` patterns are
appended at the END of the global `permission.bash` map. OpenCode applies the last matching rule, so they
beat `"*": "allow"`. They are also in the fixer's bash map. Live-tested: `echo` ran, `curl http://10.0.0.30:8123/...`
was refused. HA is read through `remote_inspect` `ha_state` / `ha_find`, which run on the main server.
`webfetch` accepts only a plain action (a pattern map makes the whole config invalid and OpenCode won't
start), so the fixer has `"webfetch": "ask"`; global webfetch is unchanged.

After changing a tool or the config: `sudo systemctl restart opencode-web` on the EPYC box.
