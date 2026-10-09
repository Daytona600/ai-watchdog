#!/usr/bin/env python3
"""Per-host health checks for the dependency grid (Z4 NAS, EPYC LLM box, ...).

Usage:
  watchdog_host_health.py raid --ssh ALIAS HOST
      md arrays all active, complete, not rebuilding
  watchdog_host_health.py health HOST [--gpus N] [--gpu-temp-warn C]
      disk / VRAM / GPU count (and CPU/GPU temps where the agent reports them)
      from the system-monitor agent on port 9191
  watchdog_host_health.py sensors --ssh ALIAS HOST [--gpu-junction-warn C] [--cpu-warn C]
      GPU junction and CPU package temps read straight from the host's hwmon
  watchdog_host_health.py unit --ssh ALIAS HOST UNIT
      a systemd unit is active
  watchdog_host_health.py ollama HOST MODEL
      Ollama answers, MODEL is installed, and if loaded it is fully on GPU
  watchdog_host_health.py ollama-chat HOST MODEL [--embed-model M] [--timeout S] [--embed-timeout S] [--port P]
                                      [--no-think] [--only-if-loaded] [--busy-gpu-util PCT]
      a real, tiny chat request gets a sensible answer (and, with --embed-model, an
      embedding request returns a vector). The check above only lists installed and
      loaded models, so it stays green when the GPU is wedged but the API still
      answers; this one does not. For big shared models: --no-think (Qwen3-style
      models otherwise spend the short reply on hidden thinking), --only-if-loaded
      (skip when the model is idle instead of forcing a slow load) and
      --busy-gpu-util PCT (a timeout is not a failure while the monitor agent shows
      a GPU at least PCT% busy and Ollama still answers). Worst case: --timeout + 3 s
      with --busy-gpu-util, else --timeout + --embed-timeout; keep it under the
      dashboard's 8 s limit.
  watchdog_host_health.py http-up URL [CODE ...]
      URL answers with one of CODE (default 200) - e.g. 401 for a
      password-protected page that is up
  watchdog_host_health.py backup [--ssh ALIAS HOST] [--max-age-hours N]
      every repository in the machine's nightly restic backup status file
      (/var/lib/host-backup/status.json, written by host-backup or, on the main
      server, by backup.sh) has a good run within N hours (default 50, so one
      missed night is tolerated but two in a row are not). Without --ssh it reads
      the local file.
  watchdog_host_health.py dns HOST [--name N] [--same-as REF_HOST [--same-name N2]]
      HOST answers a DNS lookup (default example.com). With --same-as it must
      also give the same answer as REF_HOST for N2 (default ha.magnumz.com, the
      one local-name rewrite) - catches a replica whose AdGuard config copy has
      gone stale. If REF_HOST itself is down that half is skipped (its own
      check reports it). Needs `dig` on the watchdog host.
  watchdog_host_health.py config-urls HOST [--ignore KEY ...] [--nodered-port P] [--pgrst-port P]
      every address in Node-RED's loaded AI_CONFIG.urls (read from the Node-RED
      context API on HOST, falling back to the Postgres row it is loaded from)
      answers. Any HTTP reply counts, even 404/405; only a refused or timed-out
      connection fails. Catches a service that moved while the central config kept
      the old address: the nodes read these URLs, so the failure is otherwise silent
      (2026-10-09: urls.frigate_base_url pointed at a dead host for a month).
      --ignore KEY skips an entry that is known to be dead and unused.
  watchdog_host_health.py node-literals HOST [--allow NODE_ID ...] [--nodered-port P]
      no function / http-request / tcp node in Node-RED's LIVE flows has a private
      IPv4 address typed into its code or URL (comments and disabled nodes are
      ignored): every service address must come from AI_CONFIG.urls. --allow NODE_ID
      exempts a node that cannot (the config loader needs the PostgREST address to
      load the config in the first place).

SSH uses the main server's alias (which carries user and key) with the IP
taken from watchdog_known_hosts.conf via -o HostName, so an IP change only
needs editing there. Exits 0 when healthy; otherwise prints what is wrong
(shown as the check's detail on the dashboard) and exits 1.
"""
import argparse
import concurrent.futures
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import watchdog_hosts  # noqa: E402

AGENT_PORT = 9191
CPU_TEMP_WARN_C = 85


def _threshold(name, default):
    try:
        return int(watchdog_hosts.get(name))
    except (TypeError, ValueError):
        return default


def _ssh(alias, host, command):
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", f"HostName={host}", alias, command],
        capture_output=True, text=True, timeout=7,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ssh {alias} failed: {r.stderr.strip() or f'exit {r.returncode}'}")
    return r.stdout


def raid(args):
    problems, arrays, current = [], [], None
    for line in _ssh(args.ssh, args.host, "cat /proc/mdstat").splitlines():
        m = re.match(r"^(md\d+)\s*:\s*(\S+)", line)
        if m:
            current = m.group(1)
            arrays.append(current)
            if m.group(2) != "active":
                problems.append(f"{current} is {m.group(2)}")
            continue
        if not current:
            continue
        s = re.search(r"\[(\d+)/(\d+)\]\s*\[([U_]+)\]", line)
        if s and (s.group(1) != s.group(2) or "_" in s.group(3)):
            problems.append(f"{current} degraded [{s.group(1)}/{s.group(2)}] [{s.group(3)}]")
        # A scheduled "check" scrub is routine; these mean the array is repairing itself.
        if re.search(r"\b(recovery|resync|reshape)\s*=", line):
            problems.append(f"{current} rebuilding: {line.strip()}")
    if not arrays:
        problems.append("no md arrays found in /proc/mdstat")
    return problems


def health(args):
    with urllib.request.urlopen(f"http://{args.host}:{AGENT_PORT}/metrics", timeout=6) as resp:
        m = json.load(resp)

    root_warn = _threshold("ROOT_DISK_WARN_PERCENT", 80)
    nas_warn = _threshold("NAS_WARN_PERCENT", 80)
    vram_warn = _threshold("GPU_VRAM_WARN_PERCENT", 90)
    problems = []

    for label, temp in (m.get("cpu", {}).get("temps_c") or {}).items():
        if temp is not None and temp >= CPU_TEMP_WARN_C:
            problems.append(f"CPU {label} {temp:.0f}C (warn {CPU_TEMP_WARN_C}C)")

    for d in m.get("disks", []):
        limit = root_warn if d.get("mount") == "/" else nas_warn
        if (d.get("percent_used") or 0) >= limit:
            problems.append(f"{d.get('mount')} {d.get('percent_used')}% full (warn {limit}%)")

    gpus = m.get("gpus") or []
    if len(gpus) < args.gpus:
        problems.append(f"expected {args.gpus} GPU(s), agent reports {len(gpus)}")
    for g in gpus:
        name = g.get("name", "GPU")
        if (g.get("temp_c") or 0) >= args.gpu_temp_warn:
            problems.append(f"{name} {g['temp_c']:.0f}C (warn {args.gpu_temp_warn}C)")
        total, used = g.get("mem_total_mb"), g.get("mem_used_mb")
        if total and used is not None and used / total * 100 >= vram_warn:
            problems.append(f"{name} VRAM {used / total * 100:.0f}% (warn {vram_warn}%)")
    return problems


def sensors(args):
    out = _ssh(args.ssh, args.host,
               'for h in /sys/class/hwmon/hwmon*; do n=$(cat $h/name); '
               'for t in $h/temp*_input; do echo "$n|$(cat ${t%_input}_label 2>/dev/null)|$(cat $t)"; done; done')
    junction, cpu = [], []
    for line in out.splitlines():
        name, label, value = (line.split("|") + ["", "", ""])[:3]
        try:
            c = int(value) / 1000
        except ValueError:
            continue
        if name == "amdgpu" and label == "junction":
            junction.append(c)
        elif (name == "k10temp" and label == "Tctl") or (name == "coretemp" and label.startswith("Package")):
            cpu.append(c)

    problems = []
    if not junction:
        problems.append("no GPU junction sensors found")
    for i, c in enumerate(junction):
        if c >= args.gpu_junction_warn:
            problems.append(f"GPU{i} junction {c:.0f}C (warn {args.gpu_junction_warn}C)")
    if not cpu:
        problems.append("no CPU package sensors found")
    elif max(cpu) >= args.cpu_warn:
        problems.append(f"CPU {max(cpu):.0f}C (warn {args.cpu_warn}C)")
    return problems


def unit(args):
    state = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", f"HostName={args.host}",
         args.ssh, f"systemctl is-active {args.unit}"],
        capture_output=True, text=True, timeout=7,
    ).stdout.strip()
    return [] if state == "active" else [f"{args.unit} is {state or 'unreachable'}"]


def ollama(args):
    with urllib.request.urlopen(f"http://{args.host}:11434/api/tags", timeout=6) as resp:
        names = [m.get("name", "") for m in json.load(resp).get("models", [])]
    if not any(n.split(":")[0] == args.model for n in names):
        return [f"{args.model} not installed"]
    with urllib.request.urlopen(f"http://{args.host}:11434/api/ps", timeout=6) as resp:
        loaded = [m for m in json.load(resp).get("models", []) if m.get("name", "").split(":")[0] == args.model]
    for m in loaded:  # unloaded is fine - Ollama loads on demand
        size, vram = m.get("size") or 0, m.get("size_vram") or 0
        if size and vram / size < 0.9:
            return [f"{args.model} partly on CPU ({vram / size:.0%} in VRAM)"]
    return []


class _NoAnswer(RuntimeError):
    """The request got no reply in time (as opposed to an error reply)."""


def ollama_chat(args):
    """Send a real (tiny) chat request, and an embedding request when --embed-model is given.

    Added after 2026-10-08/09: a GPU lock-up left Ollama dead for 12 hours while the dashboard stayed green, because
    its only Ollama checks were "container is running" and the model list. Options for big, shared models:
      --no-think        send think:false (Qwen3-style models otherwise spend a short reply on hidden thinking and
                        return empty content)
      --only-if-loaded  do nothing if the model is not currently loaded (loading tens of GB just to test it would
                        take far longer than a health check may, and would change what the box keeps in VRAM)
      --busy-gpu-util N a chat that times out is not a failure while Ollama still answers /api/ps and some GPU the
                        monitor agent reports is at least N% busy: the model is working for someone else. A locked
                        GPU reports no load, so that case still fails.
    A model that is not loaded yet answers slowly (Ollama loads it on demand), so a cold start can show up as a
    timeout until the warm-up job has loaded it again."""
    base = f"http://{args.host}:{args.port}"

    def call(path, payload, timeout):  # payload None -> GET
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:  # before URLError: HTTPError is a subclass of it
            detail = ""
            try:
                detail = str(json.load(e).get("error") or "")[:80]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code}" + (f" ({detail})" if detail else ""))
        except urllib.error.URLError as e:  # urlopen wraps timeouts while waiting for the reply headers too
            if isinstance(e.reason, (TimeoutError, socket.timeout)):
                raise _NoAnswer(f"no answer within {timeout:g}s")
            raise RuntimeError(f"no answer ({e.reason})")
        except (TimeoutError, socket.timeout):
            raise _NoAnswer(f"no answer within {timeout:g}s")
        except OSError as e:  # connection reset in the middle of the reply, ...
            raise RuntimeError(f"no answer ({type(e).__name__})")
        except ValueError:
            raise RuntimeError("the reply was not valid JSON")

    def gpus_busy(threshold):
        try:
            with urllib.request.urlopen(f"http://{args.host}:{AGENT_PORT}/metrics", timeout=1.5) as resp:
                gpus = json.load(resp).get("gpus") or []
        except Exception:
            return False
        return any((g.get("util_pct") or 0) >= threshold for g in gpus)

    def is_loaded(names):
        return any(n == args.model or n.startswith(args.model + ":") or n.split(":")[0] == args.model for n in names)

    if args.only_if_loaded or args.busy_gpu_util is not None:
        try:
            loaded = [m.get("name", "") for m in call("/api/ps", None, 1.5).get("models", [])]
        except RuntimeError as e:
            return [f"cannot ask Ollama what is loaded: {e}"]
        if args.only_if_loaded and not is_loaded(loaded):
            return []  # idle: the API answers and the model is simply not in VRAM right now

    problems = []
    payload = {"model": args.model, "stream": False,
               "messages": [{"role": "user", "content": "Reply with the single word OK."}],
               "options": {"num_predict": 8, "temperature": 0}}
    if args.no_think:
        payload["think"] = False
    try:
        data = call("/api/chat", payload, args.timeout)
        reply = str((data.get("message") or {}).get("content") or "").strip()
        if not reply:
            problems.append(f"{args.model} chat answered with nothing")
        elif not re.search(r"\bok(ay)?\b", reply, re.I):
            problems.append(f"{args.model} chat answered something odd: {reply[:30]!r}")
    except _NoAnswer as e:
        # It answered /api/ps a moment ago, so the server is alive; with a GPU busy it is working on another request.
        if not (args.busy_gpu_util is not None and gpus_busy(args.busy_gpu_util)):
            problems.append(f"{args.model} chat failed: {e}")
    except RuntimeError as e:
        problems.append(f"{args.model} chat failed: {e}")
    if args.embed_model:
        try:
            data = call("/api/embeddings", {"model": args.embed_model, "prompt": "hello"}, args.embed_timeout)
            if not data.get("embedding"):
                problems.append(f"{args.embed_model} embedding came back empty")
        except RuntimeError as e:
            problems.append(f"{args.embed_model} embedding failed: {e}")
    return problems


def http_up(args):
    codes = {int(c) for c in args.codes} or {200}
    try:
        with urllib.request.urlopen(urllib.request.Request(args.url, headers={"User-Agent": "ai-watchdog/1.0"}), timeout=6) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    return [] if code in codes else [f"HTTP {code} (expected {'/'.join(map(str, sorted(codes)))})"]


BACKUP_STATUS = "/var/lib/host-backup/status.json"


def backup(args):
    if args.ssh and not args.host:
        return ["backup --ssh needs a HOST"]
    try:
        raw = _ssh(args.ssh, args.host, f"cat {BACKUP_STATUS}") if args.ssh else Path(BACKUP_STATUS).read_text()
        data = json.loads(raw)
    except Exception as e:
        return [f"cannot read the backup status file ({type(e).__name__}: {e})"]
    repos = data.get("repos") or []
    if not repos:
        return ["the backup status file lists no repositories"]
    now = datetime.now(timezone.utc)
    problems = []
    for r in repos:
        name, last, err = r.get("path", "?"), r.get("last_success"), r.get("error")
        try:
            age_h = (now - datetime.fromisoformat(last)).total_seconds() / 3600 if last else None
        except (ValueError, TypeError):
            age_h = None
        if age_h is None:
            problems.append(f"{name}: no successful backup on record" + (f" (latest run: {err})" if err else ""))
        elif age_h > args.max_age_hours:
            problems.append(f"{name}: last good backup {age_h:.0f}h ago" + (f" - latest run: {err}" if err else ""))
    return problems


def _dig(server, name, timeout=2):
    """Sorted IPv4 answers from SERVER for NAME; None when the server gives no reply at all."""
    try:
        r = subprocess.run(["dig", f"@{server}", f"+time={timeout}", "+tries=1", "+short", name, "A"],
                           capture_output=True, text=True, timeout=timeout + 3)
    except FileNotFoundError:
        raise RuntimeError("dig is not installed on the watchdog host")
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:  # dig exits 9 when nothing answered
        return None
    return sorted(w for w in r.stdout.split() if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", w))


def dns(args):
    got = _dig(args.host, args.name)
    if got is None:
        return [f"{args.host} did not answer a DNS lookup for {args.name} - check the AdGuard container on that "
                f"host (docker ps; systemctl status adguardhome-guard)"]
    if not got:
        return [f"{args.host} answered but returned no address for {args.name}"]
    if args.same_as:
        ref = _dig(args.same_as, args.same_name)
        if ref:  # reference down/empty -> its own check reports that; don't blame this server
            mine = _dig(args.host, args.same_name)
            if mine != ref:
                return [f"{args.host} answers {args.same_name} with {', '.join(mine) if mine else 'nothing'} but "
                        f"{args.same_as} says {', '.join(ref)} - its AdGuard config copy is out of date; re-copy "
                        f"AdGuardHome.yaml from {args.same_as} and recreate the container"]
    return []


def _get_json(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ai-watchdog/1.0"}), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _answers(url, timeout):
    """None when something answers the HTTP request (any status), else a short reason."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ai-watchdog/1.0"}), timeout=timeout):
            return None
    except urllib.error.HTTPError:
        return None  # 404 / 405 / 500 are still an answer: the host and port are alive
    except Exception as e:  # refused, unreachable, timed out, malformed URL
        reason = getattr(e, "reason", e)
        return "timed out" if isinstance(reason, TimeoutError) or "timed out" in str(reason) else str(reason)


def config_urls(args):
    problems, urls, why = [], None, None
    try:
        raw = _get_json(f"http://{args.host}:{args.nodered_port}/context/global/AI_CONFIG?store=memory", 2)
        msg = raw.get("msg")
        if isinstance(msg, str) and msg.strip().lower() in ("(undefined)", "undefined", ""):
            # Node-RED answers but the config loader has not (or could not) load AI_CONFIG: every node that reads an
            # address now fails loudly instead of using a hard-coded one, so say so first
            problems.append("Node-RED is up but has NO AI_CONFIG loaded (the config loader failed or has not run) - "
                            "check 'docker logs nodered' for AI00 errors and re-run the 'Init AI config' inject")
        else:
            cfg = json.loads(msg) if isinstance(msg, str) else msg
            urls = (cfg or {}).get("urls")
    except Exception as e:
        why = f"{type(e).__name__}: {e}"
    if not isinstance(urls, dict) or not urls:
        try:  # Node-RED not answering or AI_CONFIG not loaded yet: use the row it is loaded from
            urls = _get_json(f"http://{args.host}:{args.pgrst_port}/config_sections?section_key=eq.urls", 2)[0]["value"]
        except Exception as e:
            return problems + [f"cannot read AI_CONFIG.urls from Node-RED ({why}) or from Postgres ({type(e).__name__}: {e})"]
    todo = {k: v for k, v in urls.items()
            if isinstance(v, str) and v.startswith(("http://", "https://")) and k not in args.ignore}
    if not todo:
        return problems + ["AI_CONFIG.urls has no http(s) entries to check"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(todo))) as ex:
        results = dict(zip(todo, ex.map(lambda u: _answers(u, 2.5), todo.values())))
    bad = [f"{k} ({todo[k]}) does not answer: {r}" for k, r in sorted(results.items()) if r]
    if bad:
        bad.append("fix the value in Postgres memory.config_sections (section 'urls') and re-run the 'Init AI config' inject "
                   "- see runbooks/config-url-stale.md")
    return problems + bad


_ADDR = re.compile(r"(?<![\d.])(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}"
                   r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?::\d{2,5})?(?![\d.])")


def node_literals(args):
    try:
        flows = _get_json(f"http://{args.host}:{args.nodered_port}/flows", 5)
    except Exception as e:
        return [f"cannot read the live flows from Node-RED ({type(e).__name__}: {e})"]
    if isinstance(flows, dict):  # Node-RED API v2 wraps the list
        flows = flows.get("flows", [])
    tabs = {n.get("id"): n.get("label") for n in flows if n.get("type") == "tab"}
    allow = set(args.allow)
    hits = []
    for n in flows:
        if n.get("id") in allow or n.get("d"):
            continue
        if n.get("type") == "function":
            texts = [l for l in (n.get("func") or "").splitlines() if not l.strip().startswith("//")]
        elif n.get("type") in ("http request", "tcp in", "tcp out", "tcp request", "udp in", "udp out", "websocket-client"):
            texts = [str(n.get("url") or ""), str(n.get("host") or "")]
        else:
            continue
        found = sorted({m.group(0) for t in texts for m in _ADDR.finditer(t)})
        if found:
            hits.append(f"{(n.get('name') or n['id']).strip()} [{tabs.get(n.get('z'))}] has {', '.join(found)}")
    if not hits:
        return []
    hits.sort()
    shown = "; ".join(hits[:6]) + ("; ..." if len(hits) > 6 else "")
    return [f"{len(hits)} node(s) with a hard-coded address: {shown} - read it from AI_CONFIG.urls instead "
            f"(see runbooks/node-hardcoded-address.md)"]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("raid"); s.add_argument("--ssh", required=True); s.add_argument("host"); s.set_defaults(fn=raid)
    s = sub.add_parser("health"); s.add_argument("host"); s.add_argument("--gpus", type=int, default=1)
    s.add_argument("--gpu-temp-warn", type=float, default=83); s.set_defaults(fn=health)
    s = sub.add_parser("sensors"); s.add_argument("--ssh", required=True); s.add_argument("host")
    s.add_argument("--gpu-junction-warn", type=float, default=95); s.add_argument("--cpu-warn", type=float, default=CPU_TEMP_WARN_C)
    s.set_defaults(fn=sensors)
    s = sub.add_parser("unit"); s.add_argument("--ssh", required=True); s.add_argument("host"); s.add_argument("unit"); s.set_defaults(fn=unit)
    s = sub.add_parser("ollama"); s.add_argument("host"); s.add_argument("model"); s.set_defaults(fn=ollama)
    s = sub.add_parser("ollama-chat"); s.add_argument("host"); s.add_argument("model"); s.add_argument("--embed-model")
    s.add_argument("--timeout", type=float, default=5.0); s.add_argument("--embed-timeout", type=float, default=2.0)
    s.add_argument("--port", type=int, default=11434); s.add_argument("--no-think", action="store_true")
    s.add_argument("--only-if-loaded", action="store_true"); s.add_argument("--busy-gpu-util", type=float)
    s.set_defaults(fn=ollama_chat)
    s = sub.add_parser("http-up"); s.add_argument("url"); s.add_argument("codes", nargs="*"); s.set_defaults(fn=http_up)
    s = sub.add_parser("backup"); s.add_argument("--ssh"); s.add_argument("host", nargs="?")
    s.add_argument("--max-age-hours", type=float, default=50); s.set_defaults(fn=backup)
    s = sub.add_parser("dns"); s.add_argument("host"); s.add_argument("--name", default="example.com")
    s.add_argument("--same-as"); s.add_argument("--same-name", default="ha.magnumz.com"); s.set_defaults(fn=dns)
    s = sub.add_parser("config-urls"); s.add_argument("host"); s.add_argument("--ignore", action="append", default=[])
    s.add_argument("--nodered-port", type=int, default=1880); s.add_argument("--pgrst-port", type=int, default=3011)
    s.set_defaults(fn=config_urls)
    s = sub.add_parser("node-literals"); s.add_argument("host"); s.add_argument("--allow", action="append", default=[])
    s.add_argument("--nodered-port", type=int, default=1880); s.set_defaults(fn=node_literals)

    args = p.parse_args()
    try:
        problems = args.fn(args)
    except Exception as e:
        print(f"{args.mode} check failed: {type(e).__name__}: {e}")
        return 1
    if problems:
        print("; ".join(problems))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
