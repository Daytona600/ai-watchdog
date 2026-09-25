#!/usr/bin/env python3
"""Z4 (primary NAS + Frigate host) health checks for the dependency grid.

Usage:
  watchdog_z4_health.py raid <host>    md arrays all active, complete, not rebuilding
  watchdog_z4_health.py health <host>  CPU/GPU temps, disk and VRAM usage via the
                                       system-monitor agent on port 9191

Exits 0 when healthy. Otherwise prints what is wrong (shown as the check's
detail on the dashboard) and exits 1.
"""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import watchdog_hosts  # noqa: E402

AGENT_PORT = 9191
CPU_TEMP_WARN_C = 85  # Xeon W-2125 reports high=93C, crit=103C
GPU_TEMP_WARN_C = 83
# SSH alias on the main server carrying the Z4 user and key; the IP still
# comes from watchdog_known_hosts.conf via HostName.
SSH_ALIAS = "apex-agent-z4"


def _threshold(name, default):
    try:
        return int(watchdog_hosts.get(name))
    except (TypeError, ValueError):
        return default


def raid(host):
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", f"HostName={host}",
         SSH_ALIAS, "cat /proc/mdstat"],
        capture_output=True, text=True, timeout=7,
    )
    if r.returncode != 0:
        return [f"could not read /proc/mdstat: {r.stderr.strip() or f'ssh exit {r.returncode}'}"]

    problems, arrays, current = [], [], None
    for line in r.stdout.splitlines():
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


def health(host):
    with urllib.request.urlopen(f"http://{host}:{AGENT_PORT}/metrics", timeout=6) as resp:
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
    if not gpus:
        problems.append("no GPU reported - Frigate's detector needs the RTX 4070 SUPER")
    for g in gpus:
        name = g.get("name", "GPU")
        if (g.get("temp_c") or 0) >= GPU_TEMP_WARN_C:
            problems.append(f"{name} {g['temp_c']:.0f}C (warn {GPU_TEMP_WARN_C}C)")
        total, used = g.get("mem_total_mb"), g.get("mem_used_mb")
        if total and used is not None and used / total * 100 >= vram_warn:
            problems.append(f"{name} VRAM {used / total * 100:.0f}% (warn {vram_warn}%)")

    return problems


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("raid", "health"):
        print(__doc__.strip())
        return 2
    mode, host = sys.argv[1], sys.argv[2]
    try:
        problems = raid(host) if mode == "raid" else health(host)
    except Exception as e:
        print(f"{mode} check failed: {type(e).__name__}: {e}")
        return 1
    if problems:
        print("; ".join(problems))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
