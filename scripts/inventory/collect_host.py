#!/usr/bin/env python3
"""Read-only inventory of THIS machine for the Home AI infrastructure page.

Prints one JSON document: OS, CPU, RAM, GPUs, disks, filesystems, RAID, network, Docker containers, custom systemd
services and timers, cron schedules. It only reads. No environment variables, no unit-file contents and no full cron
command lines are collected (cron is reduced to schedule + script name), so nothing secret can end up in the output.

Run on the machine itself:   python3 inventory_collect.py
From a PC:                   ssh <host> python3 - < inventory_collect.py
"""
import datetime
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess


def sh(cmd, timeout=25, sudo=False, any_rc=False):
    """Run a shell command, return its stdout ('' on any failure; any_rc=True keeps stdout of non-zero exits)."""
    if sudo and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = "sudo -n " + cmd
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if (r.returncode == 0 or any_rc) else ""
    except Exception:
        return ""


def gib(n):
    return round(int(n) / 1024 ** 3, 1)


def os_release():
    d = {}
    try:
        for line in open("/etc/os-release"):
            k, _, v = line.strip().partition("=")
            d[k] = v.strip('"')
    except OSError:
        pass
    return d


def cpu():
    d = {}
    for line in sh("LC_ALL=C lscpu").splitlines():
        k, _, v = line.partition(":")
        d[k.strip()] = v.strip()
    return {
        "model": d.get("Model name"),
        "sockets": d.get("Socket(s)"),
        "cores_per_socket": d.get("Core(s) per socket"),
        "threads": d.get("CPU(s)"),
        "max_mhz": d.get("CPU max MHz"),
    }


def memory():
    m = {}
    for line in open("/proc/meminfo"):
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            m[parts[0].rstrip(":")] = int(parts[1]) * 1024
    total, avail = m.get("MemTotal", 0), m.get("MemAvailable", 0)
    return {"total_gib": gib(total), "used_gib": gib(total - avail), "swap_gib": gib(m.get("SwapTotal", 0))}


def gpus():
    out = []
    if shutil.which("nvidia-smi"):
        raw = sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits")
        for line in raw.splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) == 3 and p[1].isdigit():
                out.append({"name": p[0], "vram_gib": round(int(p[1]) / 1024, 1), "driver": p[2], "vendor": "NVIDIA"})
    if shutil.which("rocm-smi"):
        try:
            data = json.loads(sh("rocm-smi --showproductname --showmeminfo vram --json", timeout=45))
            for card, v in sorted(data.items()):
                if card.startswith("card"):
                    name = v.get("Card Series") or v.get("Card series") or v.get("Card Model") or "AMD GPU"
                    vram = v.get("VRAM Total Memory (B)")
                    out.append({"name": name, "vram_gib": gib(vram) if vram else None, "vendor": "AMD"})
        except Exception:
            pass
    return out


def display_adapters():
    names = []
    for line in sh("lspci -mm").splitlines():
        try:
            f = shlex.split(line)
        except ValueError:
            continue
        if len(f) >= 4 and f[1] in ("VGA compatible controller", "3D controller", "Display controller"):
            names.append("%s %s" % (f[2], f[3]))
    return names


def physical_disks():
    try:
        devs = json.loads(sh("lsblk -J -d -b -o NAME,SIZE,MODEL,TYPE,ROTA,TRAN") or "{}").get("blockdevices", [])
    except ValueError:
        devs = []
    return [{"name": d["name"], "size_gib": gib(d["size"]), "model": (d.get("model") or "").strip(),
             "rotational": bool(d.get("rota")), "transport": d.get("tran")}
            for d in devs if d.get("type") == "disk" and not d["name"].startswith(("zram", "loop", "ram"))]


def filesystems():
    out = []
    raw = sh("df -B1 -x tmpfs -x devtmpfs -x squashfs -x efivarfs -x overlay --output=source,fstype,size,used,target")
    for line in raw.splitlines()[1:]:
        p = line.split(None, 4)
        if len(p) == 5 and p[2].isdigit():
            out.append({"source": p[0], "fstype": p[1], "size_gib": gib(p[2]), "used_gib": gib(p[3]), "target": p[4],
                        "network": p[1] in ("nfs", "nfs4", "cifs", "smb3")})
    return out


def raid():
    out, cur = [], None
    try:
        lines = open("/proc/mdstat").read().splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"^(md\d+) : (\S+) (\S+) (.*)$", line)
        if m:
            cur = {"name": m.group(1), "state": m.group(2), "level": m.group(3),
                   "members": len(re.findall(r"\w+\[\d+\]", m.group(4)))}
            out.append(cur)
        elif cur and "blocks" in line:
            cur["status"] = line.strip().split("[", 1)[-1] if "[" in line else ""
            cur["status"] = "[" + cur["status"] if cur["status"] else ""
            cur = None
    return out


def addresses():
    out = []
    for line in sh("ip -4 -o addr show scope global").splitlines():
        p = line.split()
        if len(p) >= 4:
            out.append({"interface": p[1], "address": p[3], "dynamic": "dynamic" in line})
    return out


def containers():
    raw = sh("docker ps -a --format '{{json .}}'") or sh("docker ps -a --format '{{json .}}'", sudo=True)
    out = []
    for line in raw.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        out.append({"name": d.get("Names"), "image": d.get("Image"), "status": d.get("Status"), "state": d.get("State")})
    return sorted(out, key=lambda c: c["name"] or "")


def unit_description(path):
    try:
        for line in open(path):
            if line.startswith("Description="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def custom_units(pattern, user=False):
    out = []
    env = "XDG_RUNTIME_DIR=/run/user/%d " % os.getuid() if user else ""
    sysctl = env + ("systemctl --user" if user else "systemctl")
    for f in sorted(glob.glob(pattern)):
        if os.path.islink(f) or not os.path.isfile(f):
            continue
        name = os.path.basename(f)
        entry = {"unit": name, "description": unit_description(f),
                 "active": sh("%s is-active %s" % (sysctl, name), any_rc=True) or "unknown",
                 "enabled": sh("%s is-enabled %s" % (sysctl, name), any_rc=True) or "unknown"}
        if name.endswith(".timer"):
            entry["next"] = sh("%s show %s -p NextElapseUSecRealtime --value" % (sysctl, name))
        out.append(entry)
    return out


def running_services():
    out = []
    for line in sh("systemctl list-units --type=service --state=running --no-legend --plain --no-pager").splitlines():
        p = line.split(None, 4)
        if p:
            out.append({"unit": p[0], "description": p[4] if len(p) > 4 else ""})
    return out


def cron_entries():
    out = []
    owners = [("user", False)] + ([] if os.geteuid() == 0 else [("root", True)])
    for owner, as_root in owners:
        for line in sh("crontab -l", sudo=as_root).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" in line.split()[0]:
                continue
            f = line.split(None, 5)
            if len(f) < 6:
                continue
            paths = re.findall(r"(/[\w./-]+)", f[5])
            pick = [x for x in paths if x.endswith((".sh", ".py"))] or [x for x in paths if not x.startswith(("/bin/", "/usr/bin/"))] or paths
            out.append({"owner": owner, "schedule": " ".join(f[:5]),
                        "script": os.path.basename(pick[-1]) if pick else f[5].split()[0]})
    return out


osr = os_release()
doc = {
    "collected": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "hostname": socket.gethostname(),
    "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
    "os": osr.get("PRETTY_NAME"),
    "kernel": os.uname().release,
    "uptime": sh("uptime -p"),
    "load": os.getloadavg(),
    "cpu": cpu(),
    "memory": memory(),
    "gpus": gpus(),
    "display_adapters": display_adapters(),
    "physical_disks": physical_disks(),
    "filesystems": filesystems(),
    "raid": raid(),
    "addresses": addresses(),
    "containers": containers(),
    "services": custom_units("/etc/systemd/system/*.service"),
    "timers": custom_units("/etc/systemd/system/*.timer"),
    "user_services": custom_units(os.path.expanduser("~/.config/systemd/user/*.service"), user=True),
    "running_services": running_services(),
    "cron": cron_entries(),
}
print(json.dumps(doc, indent=1))
