#!/usr/bin/env python3
"""AI fix assistant: analyse every dashboard problem with the local LLM and propose a fix.

Every warning/critical item on the dashboard (failed service-grid checks from
public/dependencies.json and the attention lines in public/alert.json) gets an
analysis page at public/fixes/<id>.html: what is wrong, the likely cause, a fix
plan with exact commands, how to check it worked, and the evidence the model
saw. The page has two buttons, handled by scripts/watchdog_web.py:

  Fix it with AI      opens an OpenCode session on the EPYC box (agent "fixer"),
                      seeded with this analysis. Investigating runs freely; every
                      change waits for approval in OpenCode.
  Ask for changes     re-runs the analysis with your note.

The model is Ollama on the EPYC box (config/watchdog_ai_fix.conf). Nothing here
changes any system: it only reads evidence and writes pages. A problem is
analysed once while it stays open (again after AI_FIX_REFRESH_HOURS, or when it
clears and comes back). The HA notification "ai_watchdog_fixes" lists the open
analyses and is dismissed when everything is clear.

Usage:
  watchdog_ai_fix.py                 analyse new problems (watchdog_master_v2.sh runs this)
  watchdog_ai_fix.py --reanalyse ID  process pending "ask for changes" requests (ID is informational)
  watchdog_ai_fix.py --render ID     re-render one page from its saved data (no LLM)
  watchdog_ai_fix.py --recheck ID    re-run that problem's dashboard check now (the fixer uses this to verify)
  watchdog_ai_fix.py --dry-run       list the problems and the evidence that would be sent
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(os.environ.get("WATCHDOG_BASE", str(Path.home() / "ai-watchdog")))
sys.path.insert(0, str(BASE / "scripts" / "lib"))
import watchdog_hosts  # noqa: E402
import watchdog_severity  # noqa: E402

PUBLIC = BASE / "public"
FIXES = PUBLIC / "fixes"
STATE_DIR = BASE / "state" / "ai-fix"
STATE_FILE = STATE_DIR / "state.json"
REQUESTS = STATE_DIR / "requests"
LOCK_FILE = STATE_DIR / "lock"
CONF_FILE = BASE / "config" / "watchdog_ai_fix.conf"
HA_ENV = BASE / "config" / "ha_token.env"
HINTS_TSV = BASE / "config" / "watchdog_action_hints.tsv"

SEV_ORDER = {"critical": 0, "warning": 1}
KINDS = ("inspect", "change", "disruptive", "manual")


# ---------------------------------------------------------------- small helpers

def load_env(path: Path) -> dict:
    out = {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


CONF = load_env(CONF_FILE)


def conf(name, default=""):
    return CONF.get(name, default)


def now():
    return datetime.now().astimezone()


def now_iso():
    return now().isoformat(timespec="seconds")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def item_id(key: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:48].strip("-")
    return f"{slug}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def run(cmd, timeout=15, shell=False) -> str:
    try:
        r = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")).strip()
        if r.returncode:
            out += f"\n[exit {r.returncode}]"
        return out[-4000:] or "(no output)"
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout}s)"
    except Exception as e:
        return f"(could not run: {type(e).__name__}: {e})"


# ---------------------------------------------------------------- what is wrong right now

def parse_problem_lines(text: str):
    # same parsing as watchdog_dashboard_v1.sh, so the keys match the dashboard's problem rows
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.endswith(":"):
            continue
        lines.append(stripped.lstrip("- ").strip())
    return lines


def current_items():
    items = []
    deps = read_json(PUBLIC / "dependencies.json", {})
    for name, c in (deps.get("checks") or {}).items():
        if c.get("ok") or c.get("severity", "warning") == "info":
            continue
        items.append({"key": f"svc:{name}", "name": name, "severity": c.get("severity", "warning"),
                      "detail": c.get("detail", ""), "type": c.get("type", ""), "target": c.get("target", ""),
                      "source": "service check"})
    alert = read_json(PUBLIC / "alert.json", {})
    if alert.get("status") == "attention":
        for line in parse_problem_lines(alert.get("message") or ""):
            sev = watchdog_severity.classify(line)
            if sev not in SEV_ORDER:
                continue
            items.append({"key": f"alert:{line}", "name": line if len(line) <= 90 else line[:87] + "...",
                          "severity": sev, "detail": line, "type": "report", "target": "",
                          "source": "watchdog report"})
    return items


# ---------------------------------------------------------------- evidence

HOST_OVERVIEW = ("hostname; uptime; df -h / | tail -1; free -h | head -2; "
                 "echo '--- failed units'; systemctl --failed --no-legend --plain | head -10; "
                 "echo '--- stopped/restarting containers'; "
                 "docker ps -a --filter status=exited --filter status=restarting --filter status=dead "
                 "--format '{{.Names}} {{.Status}}' 2>/dev/null | head -10")


def netdata_evidence(detail: str):
    parent = f"http://{watchdog_hosts.get('EPYC_LLM_IP')}:19999"
    out = []
    for node, alert in re.findall(r"([\w-]+): [^;]*?\((?:WARNING|CRITICAL), ([\w.]+)\)", detail):
        try:
            with urllib.request.urlopen(f"{parent}/host/{node}/api/v1/alarms", timeout=5) as r:
                alarms = json.load(r).get("alarms", {})
        except Exception as e:
            out.append((f"Netdata alert {alert} on {node}", f"(could not read the Netdata parent: {e})"))
            continue
        for a in alarms.values():
            if a.get("name") != alert:
                continue
            since = datetime.fromtimestamp(a.get("last_status_change", 0)).astimezone().isoformat(timespec="minutes")
            out.append((f"Netdata alert {alert} on {node}",
                        f"status {a.get('status')} since {since}\nchart {a.get('chart')} value {a.get('value_string')}\n"
                        f"info: {a.get('info')}\nsummary: {a.get('summary')}"))
    return out


def evidence_for(item):
    ev = []
    t, target = item.get("type"), item.get("target") or ""
    if t == "container":
        fmt = ("status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}} "
               "finished={{.State.FinishedAt}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} "
               "health={{if .State.Health}}{{.State.Health.Status}}{{end}}")
        ev.append(("docker inspect (main server)", run(["docker", "inspect", "-f", fmt, target])))
        ev.append((f"docker logs --tail 60 {target}", run(["docker", "logs", "--tail", "60", target])))
    elif t == "http":
        ev.append(("HTTP probe", run(["curl", "-sS", "-o", "/dev/null", "-m", "8", "-w",
                                      "HTTP %{http_code} in %{time_total}s\n", target], timeout=12)))
    elif t == "systemd-user-timer":
        ev.append(("systemctl --user status", run(["systemctl", "--user", "status", target, "--no-pager", "-n", "20"])))
        svc = re.sub(r"\.timer$", ".service", target)
        ev.append((f"journal of {svc}", run(["journalctl", "--user", "-u", svc, "-n", "40", "--no-pager"])))
    elif t == "path":
        q = shlex.quote(target)
        ev.append(("path status", run(["bash", "-c", f"ls -ld {q}; findmnt -T {q}; df -h {q}"])))
    elif t == "git-clean":
        ev.append(("git status", run(["git", "-C", target, "status", "--short"])))
    elif t == "command":
        ev.append(("the check, run again now", run(target, timeout=30, shell=True)))
        m = re.search(r"--ssh\s+(\S+)\s+(\S+)", target)
        if m:
            ev.append((f"host overview ({m.group(1)})",
                       run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", f"HostName={m.group(2)}",
                            m.group(1), HOST_OVERVIEW], timeout=20)))
        if "netdata-alerts" in target:
            ev += netdata_evidence(item.get("detail", ""))
    return ev


def hints_for(text: str):
    hints = []
    try:
        for raw in HINTS_TSV.read_text(errors="replace").splitlines():
            if not raw.strip() or raw.startswith("#") or "\t" not in raw:
                continue
            pattern, hint = (s.strip() for s in raw.split("\t", 1))
            if pattern and pattern.lower() in text.lower():
                hints.append(hint)
    except OSError:
        pass
    return hints


STOP = {"with", "from", "that", "this", "have", "check", "failed", "warning", "critical", "error", "errors",
        "watchdog", "host", "server", "returned", "http", "python3", "home", "davids", "scripts", "health",
        "none", "nodes"}


def best_runbook(item):
    words = set(re.findall(r"[a-z]{4,}", f"{item['name']} {item['detail']}".lower())) - STOP
    best, best_score = None, 0
    for p in (BASE / "runbooks").glob("*.md"):
        if p.name == "index.md":
            continue
        text = p.read_text(errors="replace")
        stem = set(re.findall(r"[a-z]{4,}", p.stem.replace("-", " ")))
        body = set(re.findall(r"[a-z]{4,}", text[:2000].lower()))
        score = 3 * len(words & stem) + len(words & body)
        if score > best_score:
            best, best_score = p, score
    if best and best_score >= 4:
        return best.name, best.read_text(errors="replace")[:3500]
    return None, None


# ---------------------------------------------------------------- the model

def house_context():
    h = watchdog_hosts.get
    return f"""HOME LAB (all on the 10.0.0.x LAN; every host is reachable by ssh alias from the main server):
- main server: ssh apex-agent-server ({h('MAIN_SERVER_IP')}), Pop!_OS 22.04. Docker: nodered (voice-assistant brain), ollama, parakeet-stt, kokoro-tts, openwakeword-*, adguardhome (DNS), cedalo_platform-mosquitto-1 (MQTT), postgres-main, postgrest, caddy-ha, searxng, openwebui. Runs this watchdog (~/ai-watchdog).
- EPYC box: ssh apex-agent-second ({h('EPYC_LLM_IP')}), Pop!_OS 24.04: Ollama + OpenCode (local coding LLM), Netdata parent (:19999) that every Linux host streams to.
- Z4: ssh apex-agent-z4 ({h('NAS_PRIMARY')}), Pop!_OS 24.04: NAS (RAID5 md1 at /mnt/recovered, NFS+Samba), Frigate NVR (Docker), replica AdGuard DNS.
- voice satellites (Debian 13): living room apex-agent-livingroom ({h('LIVING_ROOM_IP')}), bedroom apex-agent-bedroom ({h('BEDROOM_LUNA_IP')}), kitchen + Jellyfin apex-agent-jellyfin ({h('JELLYFIN_IP')}); sky camera apex-agent-skycam ({h('SKYCAM_IP')}).
- Home Assistant OS at {h('HA_SERVER_IP')} (limited shell; use its UI or API).
- Internet is Starlink behind the house router.
HOUSE RULES: smallest fix that works; no OS/driver/major-version upgrades just because they are newer; never print, log or ask for passwords, tokens or keys; containers are re-created with `docker compose up -d --force-recreate <service>` from their compose directory (plain `docker restart` has been unreliable here, except `docker restart ollama` which is the known fix for Ollama falling back to CPU); Node-RED flows are changed through its admin API and committed to git, never by hand-editing flows.json."""


SYSTEM_PROMPT = """You are the on-call engineer for a home lab. A monitoring check has flagged a problem. Using ONLY the evidence given, explain it and propose a fix.

Rules:
- Base every claim on the evidence. If the evidence does not settle the cause, say so, use confidence "low", and make the first steps "inspect" commands that would settle it.
- Each step: a complete, copy-pasteable command, run on the named host (from the main server use the ssh alias, e.g. ssh apex-agent-z4 '...'). Do not invent file paths, container names or units that the evidence or the house description does not mention; when unsure, inspect first.
- kind: "inspect" = read-only; "change" = edits a config or restarts one service; "disruptive" = reboots a host, deletes data, or interrupts the whole house; "manual" = needs a person (hardware, cables, power, a UI click).
- Order: inspect first, then the fix, then nothing else. Keep it short: at most 8 steps.
- Never include secrets or commands that print them.

Reply with JSON only:
{"summary": "one or two plain sentences: what is wrong and what it affects",
 "likely_cause": "...",
 "confidence": "low" | "medium" | "high",
 "needs_human": "" or "what a person must physically do",
 "steps": [{"why": "...", "host": "main server|EPYC|Z4|...", "command": "...", "kind": "inspect|change|disruptive|manual"}],
 "verify": "how to confirm it is fixed (a command or what to look for)",
 "rollback": "how to undo the change, or \\"\\" if nothing to undo",
 "prevent": "optional: what would stop it happening again, or \\"\\""}"""


def build_prompt(item, evidence, runbook, hints, note=None, previous=None):
    parts = [house_context(), "",
             f"PROBLEM ({item['severity'].upper()}, from the {item['source']}): {item['name']}",
             f"Check output: {item['detail']}"]
    if item.get("type") and item.get("target"):
        parts.append(f"Check type: {item['type']}; target: {item['target']}")
    if hints:
        parts += ["", "Existing hint for this kind of problem:"] + [f"- {h}" for h in hints]
    for title, text in evidence:
        parts += ["", f"EVIDENCE: {title}", text]
    if runbook[0]:
        parts += ["", f"RUNBOOK {runbook[0]} (house notes; may be partly out of date):", runbook[1]]
    if previous:
        parts += ["", "YOUR PREVIOUS ANALYSIS:", json.dumps(previous, indent=1)[:4000]]
    if note:
        parts += ["", f"DAVID'S NOTE ON IT (follow it): {note}"]
    return "\n".join(parts)


def ask_model(prompt):
    url = f"http://{watchdog_hosts.get('EPYC_LLM_IP')}:{conf('AI_FIX_OLLAMA_PORT', '11434')}/api/chat"
    payload = {"model": conf("AI_FIX_MODEL", "qwen3.6-coder"), "stream": False, "think": False, "format": "json",
               "keep_alive": "60m",
               "options": {"num_ctx": int(conf("AI_FIX_NUM_CTX", "32768")), "temperature": 0.2},
               "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]}
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=int(conf("AI_FIX_TIMEOUT_SEC", "420"))) as r:
        return json.load(r)["message"]["content"]


def normalise(raw: str):
    a = json.loads(raw)
    if not isinstance(a, dict):
        raise ValueError("model reply is not a JSON object")
    steps = []
    for s in a.get("steps") or []:
        if not isinstance(s, dict) or not str(s.get("command", "")).strip() and s.get("kind") != "manual":
            continue
        kind = str(s.get("kind", "inspect")).lower()
        steps.append({"why": str(s.get("why", "")), "host": str(s.get("host", "")),
                      "command": str(s.get("command", "")).strip(),
                      "kind": kind if kind in KINDS else "change"})
    conf_ = str(a.get("confidence", "low")).lower()
    return {"summary": str(a.get("summary", "")).strip(), "likely_cause": str(a.get("likely_cause", "")).strip(),
            "confidence": conf_ if conf_ in ("low", "medium", "high") else "low",
            "needs_human": str(a.get("needs_human", "") or "").strip(), "steps": steps[:10],
            "verify": str(a.get("verify", "")).strip(), "rollback": str(a.get("rollback", "") or "").strip(),
            "prevent": str(a.get("prevent", "") or "").strip()}


def analyse(item, note=None, previous=None):
    evidence = evidence_for(item)
    hints = hints_for(f"{item['name']} {item['detail']}")
    runbook = best_runbook(item)
    prompt = build_prompt(item, evidence, runbook, hints, note, previous)
    started = time.time()
    error, analysis = None, None
    for attempt in range(2):
        try:
            analysis = normalise(ask_model(prompt))
            break
        except (json.JSONDecodeError, ValueError) as e:
            error = f"the model's reply was not usable JSON ({e})"
        except Exception as e:
            error = f"could not reach the model: {type(e).__name__}: {e}"
            break
    return {"analysis": analysis, "error": None if analysis else error, "evidence": evidence,
            "runbook": runbook[0], "hints": hints, "analysed_at": now_iso(),
            "duration_s": round(time.time() - started), "model": conf("AI_FIX_MODEL", "qwen3.6-coder"),
            "note": note}


# ---------------------------------------------------------------- pages

CSS = """
:root { color-scheme: dark; --bg:#0f1115; --card:#171a21; --text:#e8e8e8; --muted:#a8b0bf; --ok:#1f8f4d;
  --bad:#b83b3b; --warn:#b8871f; --info:#3a5f8a; --line:#2a2f3a; --link:#8ab4ff; --code:#10131a; }
* { box-sizing: border-box; }
body { margin:0; padding:18px; background:var(--bg); color:var(--text); line-height:1.45;
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 980px; }
h1 { margin:0 0 6px 0; font-size:1.4rem; } h2 { margin:20px 0 8px 0; font-size:1.1rem; }
a { color:var(--link); text-decoration:none; } a:hover { text-decoration:underline; }
.muted { color:var(--muted); font-size:.9rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; margin:10px 0; }
.badge { display:inline-block; padding:3px 9px; border-radius:999px; font-weight:700; font-size:.72rem;
  letter-spacing:.03em; white-space:nowrap; color:white; vertical-align:middle; }
.ok { background:var(--ok); } .bad { background:var(--bad); } .warn { background:var(--warn); } .info { background:var(--info); }
.k-inspect { background:var(--info); } .k-change { background:var(--warn); } .k-disruptive { background:var(--bad); }
.k-manual { background:#6b7280; }
.step { border-left:4px solid var(--line); padding:6px 0 6px 12px; margin:12px 0; }
.step.k-change-b { border-left-color:var(--warn); } .step.k-disruptive-b { border-left-color:var(--bad); }
.step.k-inspect-b { border-left-color:var(--info); }
pre { white-space:pre-wrap; word-break:break-word; background:var(--code); border:1px solid var(--line);
  padding:10px; border-radius:10px; margin:6px 0; font-size:.85rem; position:relative; }
.copy { float:right; font-size:.75rem; background:#2a2f3a; color:var(--text); border:0; border-radius:6px;
  padding:2px 8px; cursor:pointer; }
.actions { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-start; }
.actions form { margin:0; }
button.primary { background:#2f6fde; color:white; border:0; border-radius:10px; padding:10px 18px;
  font-weight:700; font-size:1rem; cursor:pointer; }
button.secondary { background:#2a2f3a; color:var(--text); border:1px solid var(--line); border-radius:10px;
  padding:8px 14px; font-weight:600; cursor:pointer; }
textarea { width:100%; min-height:70px; background:var(--code); color:var(--text); border:1px solid var(--line);
  border-radius:10px; padding:8px; font:inherit; }
.banner { border-left:4px solid var(--warn); }
details summary { cursor:pointer; color:var(--muted); }
table { border-collapse:collapse; width:100%; } td, th { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }
"""

COPY_JS = """<script>
document.querySelectorAll('pre[data-copy]').forEach(function (pre) {
  var b = document.createElement('button'); b.className = 'copy'; b.textContent = 'copy';
  b.onclick = function () { navigator.clipboard.writeText(pre.getAttribute('data-copy'));
    b.textContent = 'copied'; setTimeout(function () { b.textContent = 'copy'; }, 1500); };
  pre.prepend(b);
});
</script>"""


def sev_badge(sev):
    return f'<span class="badge {"bad" if sev == "critical" else "warn"}">{esc(sev.upper())}</span>'


def sessions_file(fid):
    return FIXES / f"{fid}.sessions.json"


def render_page(fid):
    doc = read_json(FIXES / f"{fid}.json", None)
    if not doc:
        return
    item, latest = doc["item"], doc.get("latest") or {}
    a = latest.get("analysis")
    request = read_json(REQUESTS / f"{fid}.json", None)
    sessions = read_json(sessions_file(fid), [])
    status = doc.get("status", "open")
    refresh = '<meta http-equiv="refresh" content="30">' if request else ""

    out = [f"<!doctype html><html><head><meta charset='utf-8'>{refresh}"
           "<meta name='viewport' content='width=device-width, initial-scale=1'>"
           f"<title>AI fix: {esc(item['name'])}</title><style>{CSS}</style></head><body>",
           "<p class='muted'><a href='../dashboard.html'>&larr; Dashboard</a> &middot; <a href='index.html'>All AI analyses</a></p>",
           f"<h1>{esc(item['name'])}</h1>",
           f"<p>{sev_badge(item['severity'])} "
           + ("<span class='badge ok'>RESOLVED</span> " if status == "resolved" else "")
           + f"<span class='muted'>{esc(item['source'])} &middot; first seen {esc(doc.get('first_seen', '')[:16].replace('T', ' '))}"
           + (f" &middot; cleared {esc(doc.get('resolved_at', '')[:16].replace('T', ' '))}" if status == "resolved" else "")
           + "</span></p>",
           f"<div class='card'><div class='muted'>Check output</div><pre>{esc(item['detail'])}</pre></div>"]

    if request:
        out.append(f"<div class='card banner'><strong>Re-analysis requested</strong> {esc(request.get('requested_at', '')[11:16])} "
                   f"with your note: <em>{esc(request.get('note', ''))}</em><br><span class='muted'>Takes about a minute "
                   "once the model is free; this page refreshes itself.</span></div>")

    if a:
        conf_cls = {"high": "ok", "medium": "warn", "low": "bad"}[a["confidence"]]
        out.append(f"<h2>What's wrong</h2><div class='card'><p>{esc(a['summary'])}</p>"
                   f"<p><strong>Likely cause:</strong> {esc(a['likely_cause'])}</p>"
                   f"<p class='muted'>Confidence <span class='badge {conf_cls}'>{esc(a['confidence'].upper())}</span> "
                   f"&middot; {esc(latest.get('model'))} on the EPYC box &middot; {esc(latest.get('analysed_at', '')[:16].replace('T', ' '))} "
                   f"({esc(latest.get('duration_s'))} s)"
                   + (f" &middot; with your note: <em>{esc(latest['note'])}</em>" if latest.get("note") else "")
                   + "</p></div>")
        if a["needs_human"]:
            out.append(f"<div class='card banner'><strong>Needs a person:</strong> {esc(a['needs_human'])}</div>")
        out.append("<h2>Proposed fix</h2><div class='card'>")
        for i, s in enumerate(a["steps"], 1):
            out.append(f"<div class='step k-{s['kind']}-b'><span class='badge k-{s['kind']}'>{esc(s['kind'].upper())}</span> "
                       f"<strong>{i}.</strong> {esc(s['why'])} <span class='muted'>({esc(s['host'])})</span>"
                       + (f"<pre data-copy='{esc(s['command'])}'>{esc(s['command'])}</pre>" if s["command"] else "")
                       + "</div>")
        if not a["steps"]:
            out.append("<p class='muted'>No steps proposed.</p>")
        out.append("<p class='muted'>INSPECT = read-only &middot; CHANGE = edits a config or restarts one service &middot; "
                   "DISRUPTIVE = reboots, deletes, or affects the whole house &middot; MANUAL = needs a person</p></div>")
        if a["verify"]:
            out.append(f"<h2>How to check it worked</h2><div class='card'><pre>{esc(a['verify'])}</pre></div>")
        if a["rollback"] or a["prevent"]:
            out.append("<div class='card'>" + (f"<p><strong>Undo:</strong> {esc(a['rollback'])}</p>" if a["rollback"] else "")
                       + (f"<p><strong>To stop it happening again:</strong> {esc(a['prevent'])}</p>" if a["prevent"] else "")
                       + "</div>")
    elif latest.get("error"):
        out.append(f"<div class='card banner'><strong>No analysis:</strong> {esc(latest['error'])}. "
                   "It is retried on the next watchdog run, or ask for it below.</div>")
    else:
        out.append("<div class='card banner'>Analysis is queued; the next watchdog run picks it up.</div>")

    out.append("<h2>Act on it</h2><div class='card'><div class='actions'>"
               "<form method='post' action='../api/fix-start' target='_blank'>"
               f"<input type='hidden' name='id' value='{esc(fid)}'>"
               "<button class='primary' type='submit'>Fix it with AI &rarr;</button></form>"
               "<div class='muted' style='max-width:520px'>Opens OpenCode on the EPYC box in a new tab, loaded with this analysis. "
               "It checks the diagnosis first (read-only, no prompts), then asks you to <strong>Allow</strong> each change. "
               "Type in that session to steer it.</div></div>")
    if sessions:
        out.append("<p class='muted'>Earlier fix sessions: " + ", ".join(
            f"<a href='{esc(s['url'])}' target='_blank'>{esc(s['started'][5:16].replace('T', ' '))}</a>" for s in sessions[-5:]) + "</p>")
    out.append("<form method='post' action='../api/fix-reanalyse' style='margin-top:14px'>"
               f"<input type='hidden' name='id' value='{esc(fid)}'>"
               "<div class='muted'>Ask for a different analysis (e.g. \"that container is supposed to be stopped\", "
               "\"don't restart anything during the day\", \"check the NAS mount first\"):</div>"
               "<textarea name='note' required maxlength='1500'></textarea>"
               "<button class='secondary' type='submit'>Re-analyse with my note</button></form></div>")

    if latest.get("evidence"):
        out.append("<h2>Evidence the AI saw</h2><div class='card'>")
        for title, text in latest["evidence"]:
            out.append(f"<details><summary>{esc(title)}</summary><pre>{esc(text)}</pre></details>")
        if latest.get("runbook"):
            out.append(f"<p class='muted'>Runbook used: <a href='../runbooks/{esc(latest['runbook'][:-3])}.html'>{esc(latest['runbook'])}</a></p>")
        if latest.get("hints"):
            out.append("<p class='muted'>Hint used: " + esc(" / ".join(latest["hints"])) + "</p>")
        out.append("</div>")

    history = doc.get("history") or []
    if history:
        out.append("<h2>Earlier analyses</h2><div class='card'>")
        for h in reversed(history[-5:]):
            ha = h.get("analysis") or {}
            out.append(f"<details><summary>{esc(h.get('analysed_at', '')[:16].replace('T', ' '))}"
                       + (f" &middot; note: {esc(h['note'])}" if h.get("note") else "") + "</summary>"
                       f"<p>{esc(ha.get('summary') or h.get('error') or '')}</p>"
                       + "".join(f"<pre>{esc(s['command'])}</pre>" for s in ha.get("steps", []) if s.get("command"))
                       + "</details>")
        out.append("</div>")

    out.append("<p class='muted'>Written by a local model from the evidence above. Read a command before you run it; "
               "the Fix-it session asks before every change. See runbooks/ai-fix-assistant.md.</p>")
    out.append(COPY_JS + "</body></html>")
    write_text(FIXES / f"{fid}.html", "\n".join(out))


def render_index(state):
    rows_open, rows_done = [], []
    cutoff = now() - timedelta(days=7)
    for key, s in sorted(state.items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True):
        doc = read_json(FIXES / f"{s['id']}.json", {})
        a = (doc.get("latest") or {}).get("analysis") or {}
        row = (f"<tr><td>{sev_badge(s.get('severity', 'warning'))}</td><td><a href='{esc(s['id'])}.html'>{esc(s['name'])}</a>"
               f"<div class='muted'>{esc(a.get('summary', '') or 'analysis pending')}</div></td>"
               f"<td class='muted'>{esc(s.get('first_seen', '')[5:16].replace('T', ' '))}</td></tr>")
        if s.get("status") == "open":
            rows_open.append(row)
        elif s.get("resolved_at") and datetime.fromisoformat(s["resolved_at"]) > cutoff:
            rows_done.append(row)
    body = ["<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='refresh' content='120'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>AI fix assistant</title><style>{CSS}</style></head><body>",
            "<p class='muted'><a href='../dashboard.html'>&larr; Dashboard</a></p><h1>AI analyses</h1>",
            f"<p class='muted'>Updated {esc(now_iso()[:16].replace('T', ' '))}. Each open problem gets an analysis from "
            f"{esc(conf('AI_FIX_MODEL', 'qwen3.6-coder'))} on the EPYC box.</p>",
            "<h2>Open</h2><div class='card'><table>" + ("".join(rows_open) or "<tr><td class='muted'>Nothing open.</td></tr>") + "</table></div>",
            "<h2>Cleared in the last 7 days</h2><div class='card'><table>"
            + ("".join(rows_done) or "<tr><td class='muted'>None.</td></tr>") + "</table></div></body></html>"]
    write_text(FIXES / "index.html", "\n".join(body))
    write_json(FIXES / "index.json", {"updated": now_iso(), "items": {
        k: {"id": s["id"], "status": s.get("status"), "analysed": bool(s.get("analysed_at"))} for k, s in state.items()}})


# ---------------------------------------------------------------- HA notification

def notify(state):
    ha = load_env(HA_ENV)
    base_url, token = ha.get("HA_BASE_URL", "").rstrip("/"), ha.get("HA_TOKEN", "")
    if not base_url or not token:
        print("HA_BASE_URL/HA_TOKEN missing; no notification")
        return
    nid = conf("AI_FIX_NOTIFICATION_ID", "ai_watchdog_fixes")
    link = conf("AI_FIX_PUBLIC_BASE", "https://ha.magnumz.com/watchdog").rstrip("/")
    open_items = [s for s in state.values() if s.get("status") == "open" and s.get("analysed_at")]
    lines = []
    for s in sorted(open_items, key=lambda s: SEV_ORDER.get(s.get("severity"), 1)):
        a = (read_json(FIXES / f"{s['id']}.json", {}).get("latest") or {}).get("analysis") or {}
        lines.append(f"- **{s['name']}**: {a.get('summary', '')[:220]} [View analysis and fix]({link}/fixes/{s['id']}.html)")
    digest = hashlib.sha1("\n".join(lines).encode()).hexdigest()
    marker = STATE_DIR / "last_notification.sha1"
    if marker.exists() and marker.read_text().strip() == digest:
        return
    if lines:
        service, payload = "create", {"notification_id": nid, "title": f"AI Watchdog: {len(lines)} problem(s) analysed",
                                      "message": "\n".join(lines) + f"\n\n[All AI analyses]({link}/fixes/index.html) (home network / VPN only)"}
    else:
        service, payload = "dismiss", {"notification_id": nid}
    req = urllib.request.Request(f"{base_url}/api/services/persistent_notification/{service}",
                                 json.dumps(payload).encode(),
                                 {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
        marker.write_text(digest)
        print(f"HA notification {service}d ({len(lines)} open)")
    except Exception as e:
        print(f"HA notification failed: {e}")


# ---------------------------------------------------------------- runs

def save_analysis(fid, item, result, state_entry):
    doc = read_json(FIXES / f"{fid}.json", None) or {"id": fid, "key": item["key"], "history": []}
    if doc.get("latest"):
        doc["history"] = (doc.get("history") or [])[-9:] + [
            {k: doc["latest"].get(k) for k in ("analysis", "error", "analysed_at", "note")}]
    doc.update({"item": item, "latest": result, "status": state_entry.get("status", "open"),
                "first_seen": state_entry.get("first_seen"), "resolved_at": state_entry.get("resolved_at")})
    write_json(FIXES / f"{fid}.json", doc)
    render_page(fid)


def mark_status(fid, entry):
    doc = read_json(FIXES / f"{fid}.json", None)
    if doc:
        doc.update({"status": entry.get("status"), "resolved_at": entry.get("resolved_at")})
        write_json(FIXES / f"{fid}.json", doc)
        render_page(fid)


def refresh_dashboard():
    run(["bash", str(BASE / "scripts" / "watchdog_dashboard_v1.sh")], timeout=60)


def main_run(max_items, only_requests=False):
    state = read_json(STATE_FILE, {})
    items = current_items()
    seen = {i["key"]: i for i in items}
    ts = now_iso()

    if not only_requests:
        for key, s in state.items():
            if key not in seen and s.get("status") == "open":
                s.update({"status": "resolved", "resolved_at": ts})
                mark_status(s["id"], s)
        for item in items:
            s = state.setdefault(item["key"], {"id": item_id(item["key"]), "first_seen": ts})
            if s.get("status") == "resolved":  # came back: treat as new
                s.update({"first_seen": ts, "analysed_at": None, "resolved_at": None})
            s.update({"name": item["name"], "severity": item["severity"], "status": "open", "last_seen": ts})
        write_json(STATE_FILE, state)

    # "ask for changes" requests first: someone is waiting on the page
    done = 0
    for req_path in sorted(REQUESTS.glob("*.json")):
        fid, req = req_path.stem, read_json(req_path, {})
        entry = next((s for s in state.values() if s["id"] == fid), None)
        doc = read_json(FIXES / f"{fid}.json", None)
        if not entry or not doc:
            req_path.unlink(missing_ok=True)
            continue
        item = seen.get(doc["key"], doc["item"])
        previous = (doc.get("latest") or {}).get("analysis")
        print(f"re-analysing {item['name']} with note")
        result = analyse(item, note=req.get("note"), previous=previous)
        req_path.unlink(missing_ok=True)
        entry["analysed_at"] = result["analysed_at"]
        save_analysis(fid, item, result, entry)
        write_json(STATE_FILE, state)
        done += 1

    if not only_requests:
        refresh_after = now() - timedelta(hours=float(conf("AI_FIX_REFRESH_HOURS", "24")))
        todo = [i for i in items if not state[i["key"]].get("analysed_at")
                or datetime.fromisoformat(state[i["key"]]["analysed_at"]) < refresh_after]
        todo.sort(key=lambda i: SEV_ORDER.get(i["severity"], 1))
        for item in todo[:max_items]:
            entry = state[item["key"]]
            print(f"analysing {item['name']} ...", flush=True)
            result = analyse(item)
            print(f"  {'ok' if result['analysis'] else result['error']} ({result['duration_s']} s)", flush=True)
            if result["analysis"]:
                entry["analysed_at"] = result["analysed_at"]
            save_analysis(entry["id"], item, result, entry)
            write_json(STATE_FILE, state)
            done += 1
        if len(todo) > max_items:
            print(f"{len(todo) - max_items} more wait for the next run")

    # forget resolved items after 30 days (their pages stay until retention cleanup)
    cutoff = now() - timedelta(days=30)
    for key in [k for k, s in state.items() if s.get("status") == "resolved" and s.get("resolved_at")
                and datetime.fromisoformat(s["resolved_at"]) < cutoff]:
        del state[key]
    write_json(STATE_FILE, state)
    render_index(state)
    notify(state)
    refresh_dashboard()
    print(f"done: {done} analysis(es), {sum(1 for s in state.values() if s.get('status') == 'open')} open")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--reanalyse", metavar="ID")
    p.add_argument("--render", metavar="ID")
    p.add_argument("--recheck", metavar="ID")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=int(conf("AI_FIX_MAX_PER_RUN", "6")))
    args = p.parse_args()

    if args.render:
        render_page(args.render)
        return 0
    if args.recheck:
        doc = read_json(FIXES / f"{args.recheck}.json", None)
        if not doc:
            print(f"no analysis with id {args.recheck}")
            return 1
        if not doc["key"].startswith("svc:"):
            print("This problem comes from the full watchdog report; re-check it with: "
                  "bash ~/ai-watchdog/scripts/watchdog_master_v2.sh (a few minutes), then look at public/alert.json")
            return 0
        name = doc["key"][4:]
        run(["python3", str(BASE / "scripts" / "watchdog_dependencies_v1.py"), "--only", name], timeout=60)
        refresh_dashboard()
        c = (read_json(PUBLIC / "dependencies.json", {}).get("checks") or {}).get(name, {})
        print(f"{name}: {'OK' if c.get('ok') else 'STILL FAILING'} - {c.get('detail', 'no result')}")
        return 0 if c.get("ok") else 1
    if args.dry_run:
        for item in current_items():
            print(f"== {item['key']} -> {item_id(item['key'])}")
            for title, text in evidence_for(item):
                print(f"  -- {title}\n" + "\n".join("     " + ln for ln in text.splitlines()[:12]))
            print("  runbook:", best_runbook(item)[0], " hints:", hints_for(f"{item['name']} {item['detail']}"))
        return 0
    if conf("AI_FIX_ENABLED", "1") != "1":
        print("AI fix assistant disabled in config/watchdog_ai_fix.conf")
        return 0

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REQUESTS.mkdir(parents=True, exist_ok=True)
    FIXES.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        if args.reanalyse:
            fcntl.flock(lock, fcntl.LOCK_EX)  # wait for a running analysis to finish
        else:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("another AI fix run is in progress; skipping")
                return 0
        main_run(args.max, only_requests=bool(args.reanalyse))
    return 0


if __name__ == "__main__":
    sys.exit(main())
