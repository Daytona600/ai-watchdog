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
      "bash": { "*": "ask" },
      "edit": "ask",
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

After changing a tool or the config: `sudo systemctl restart opencode-web` on the EPYC box.
