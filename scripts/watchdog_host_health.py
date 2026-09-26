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
  watchdog_host_health.py http-up URL [CODE ...]
      URL answers with one of CODE (default 200) - e.g. 401 for a
      password-protected page that is up

SSH uses the main server's alias (which carries user and key) with the IP
taken from watchdog_known_hosts.conf via -o HostName, so an IP change only
needs editing there. Exits 0 when healthy; otherwise prints what is wrong
(shown as the check's detail on the dashboard) and exits 1.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
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


def http_up(args):
    codes = {int(c) for c in args.codes} or {200}
    try:
        with urllib.request.urlopen(urllib.request.Request(args.url, headers={"User-Agent": "ai-watchdog/1.0"}), timeout=6) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    return [] if code in codes else [f"HTTP {code} (expected {'/'.join(map(str, sorted(codes)))})"]


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
    s = sub.add_parser("http-up"); s.add_argument("url"); s.add_argument("codes", nargs="*"); s.set_defaults(fn=http_up)

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
