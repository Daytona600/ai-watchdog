#!/usr/bin/env python3
"""Build the 'Home AI Infrastructure Inventory' page from inventory-data/*.json (written by inventory_collect.py,
ha_inventory.py). Reuses the CSS of the page that is already published so the look does not change.
Output: a page fragment (title + style + body); the artifact service adds the document skeleton on publish."""
import html
import json
import re
from pathlib import Path

import argparse
import datetime

HERE = Path(__file__).resolve().parent
_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--data", default="inventory-data", help="folder with main.json, epyc.json, ... ha.json (see README.md)")
_ap.add_argument("--css", default=str(HERE / "page.css"), help="stylesheet of the page")
_ap.add_argument("--out", default="inventory_page.html", help="where to write the page")
ARGS = _ap.parse_args()
DATA, CSS, OUT = Path(ARGS.data), Path(ARGS.css), Path(ARGS.out)
SNAPSHOT_DATE = datetime.date.today().isoformat()


def esc(s):
    return html.escape(str(s), quote=True)


def load(name):
    return json.loads((DATA / (name + ".json")).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ formatting helpers
def size(gib):
    return "%.1f GiB" % gib if gib < 1024 else "%.2f TiB" % (gib / 1024)


def pct(used, total):
    return int(round(100.0 * used / total)) if total else 0


def clean_cpu(model):
    m = model or "unknown CPU"
    m = re.sub(r"\((R|TM)\)", "", m)
    m = re.sub(r"\b\d+(st|nd|rd|th) Gen\b", "", m)
    m = re.sub(r"\s+@\s*[\d.]+GHz", "", m)
    m = re.sub(r"\b(CPU|Processor|\d+-Core)\b", "", m)
    return re.sub(r"\s+", " ", m).strip()


def clean_gpu(name):
    n = re.sub(r"^(NVIDIA|AMD)\s+", "", name or "")
    return re.sub(r"^GeForce\s+", "", n)


def drive_label(d):
    tb = d["size_gib"] * 1.073741824 / 1000.0
    nominal = ("%d TB" % round(tb)) if tb >= 0.95 else ("%d GB" % round(tb * 1000))
    model = re.sub(r"\s+", " ", d.get("model") or "").strip()
    if not model and d["name"].startswith("vd"):
        return "virtual disk, %d GiB" % round(d["size_gib"])
    kind = "NVMe" if d.get("transport") == "nvme" else ("HDD" if d.get("rotational") else "SSD")
    return "%s %s (%s)" % (model or "disk", nominal, kind)


def nice_next(ts):
    m = re.match(r"^(\w{3}) \d{4}-\d\d-\d\d (\d\d:\d\d)", ts or "")
    return "%s %s" % (m.group(1), m.group(2)) if m else ""


def cron_text(sched):
    f = sched.split()
    if len(f) != 5:
        return sched
    mi, ho, dom, mon, dow = f
    if sched == "* * * * *":
        return "every minute"
    if mi.startswith("*/") and ho == dom == mon == dow == "*":
        return "every %s min" % mi[2:]
    if mi.isdigit() and ho.isdigit() and dom == mon == dow == "*":
        return "daily %02d:%02d" % (int(ho), int(mi))
    return sched


def short_image(img):
    img = re.sub(r"^(docker\.io|ghcr\.io|lscr\.io|quay\.io)/", "", img or "")
    return re.sub(r"@sha256:.*$", "", img)


# ------------------------------------------------------------------ what each container is for
CONTAINER_DESC = {
    "adguardhome": "DNS and ad-blocking",
    "caddy-ha": "Reverse proxy for ha.magnumz.com",
    "cedalo_platform-management-center-1": "MQTT broker stack (management UI)",
    "cedalo_platform-mosquitto-1": "MQTT broker",
    "cedalo_platform-streamsheets-1": "MQTT broker stack (Streamsheets)",
    "command-parser": "Home AI backend: voice command parser",
    "local-mcp-agent": "Home AI backend: local MCP agent",
    "vision-intake": "Home AI backend: camera and vision intake",
    "inventory-service": "Food inventory and shopping list",
    "kokoro-tts": "Text-to-speech voices (Kokoro)",
    "nodered": "Node-RED voice and automation brain",
    "ollama": "Local LLM inference",
    "openwakeword-bedroom": "Wake-word detection, bedroom",
    "openwakeword-kitchen": "Wake-word detection, kitchen",
    "openwakeword-livingroom": "Wake-word detection, living room",
    "openwebui": "LLM chat UI",
    "parakeet-stt": "Speech-to-text (Parakeet, on the GPU)",
    "pgadmin": "PostgreSQL admin UI",
    "postgres-main": "Shared PostgreSQL / TimescaleDB database",
    "postgrest": "REST API over the Postgres config and memory tables",
    "searxng": "Local web search",
    "speaker-id": "Speaker identification",
    "frigate": "Frigate NVR",
    "ollama-code": "Local coding LLM (qwen3.6-coder)",
    "nodered-sandbox": "Node-RED test instance",
    "living-room-satellite": "Voice satellite pipeline",
    "bedroom-satellite": "Voice satellite pipeline",
    "kitchen-satellite": "Kitchen voice satellite pipeline",
    "jellyfin": "Media server",
    "openwakeword": "Old on-box wake-word container (the main server does this now)",
}

# running services that are plain operating-system plumbing; everything else is worth a mention
BORING = re.compile(
    r"^(ModemManager|NetworkManager|accounts-daemon|acpid|atd|avahi-daemon|bluetooth|chrony|colord|com\.system76\..*|"
    r"containerd|cron|cups|cups-browsed|dbus|dbus-broker|fwupd|gdm|getty@.*|serial-getty@.*|irqbalance|low-memory-monitor|"
    r"multipathd|networkd-dispatcher|nfs-blkmap|packagekit|polkit|pop-upgrade|rsyslog|rtkit-daemon|snapd|switcheroo-control|"
    r"system76-.*|systemd-.*|thermald|touchegg|udisks2|unattended-upgrades|upower|user@.*|uuidd|wpa_supplicant|"
    r"cosmic-greeter.*|fsidd|nvidia-persistenced|rpc-statd|rpcbind|ssh|smartmontools|docker|"
    r"cloud-.*|unattended.*|lxd.*|snap\..*|apparmor|gpu-manager|ufw|kmod.*|lm-sensors|mdmonitor-oneshot)$")


def other_services(d, custom_names):
    names = []
    for s in d.get("running_services", []):
        n = s["unit"].replace(".service", "")
        if n in custom_names or BORING.match(n):
            continue
        names.append(n)
    return sorted(set(names))


# ------------------------------------------------------------------ host definitions (order = order on the page)
HOSTS = [
    dict(key="main", sid="main-server", title="Main server", nav="Main server", user="davids", alias="apex-agent-server",
         role="Voice brain, MQTT, DNS, Postgres, Node-RED, speech and local LLM"),
    dict(key="epyc", sid="epyc", title="EPYC box", nav="EPYC", user="davids1", alias="apex-agent-second",
         role="Local coding LLM on two GPUs; was the X99 until 2026-09-25", note_extra="Supermicro H11SSL-i board, BMC at 10.0.0.159."),
    dict(key="z4", sid="z4", title="Z4", nav="Z4", user="davids2", alias="apex-agent-z4",
         role="NAS (RAID5), Frigate NVR, replica DNS, backup hub",
         note_extra="Serves NFS and Samba. Exports: frigate, Public, backup, skycam2."),
    dict(key="livingroom", sid="living-room", title="Living room satellite", nav="Living room", user="david",
         alias="apex-agent-livingroom", role="Voice satellite (David)"),
    dict(key="bedroom", sid="bedroom", title="Bedroom satellite", nav="Bedroom", user="david",
         alias="apex-agent-bedroom", role="Voice satellite (Mary) with headset push-to-talk"),
    dict(key="jellyfin", sid="jellyfin", title="Jellyfin and kitchen satellite", nav="Jellyfin", user="david",
         alias="apex-agent-jellyfin", role="Media server and kitchen voice satellite"),
    dict(key="skycam", sid="skycam", title="Sky camera", nav="Sky camera", user="david", alias="apex-agent-skycam",
         role="Night-sky camera: capture, anomaly detection, timelapses",
         note_extra="Camera: The Imaging Source DFM 37UX178-ML on USB. Last 12 days of frames stay local, older nights are archived to the NAS."),
    dict(key="vps", sid="vps", title="VPS", nav="VPS", user="root", alias="searxng-vps",
         role="Web, mail and DNS host; SOCKS egress for web search; WireGuard to home", hide_ip=True),
]

SATELLITE_BORING_SERVICES = {"host-backup"}


def spec(label, value, tag=None, mono=False):
    t = ' <span class="tag">%s</span>' % esc(tag) if tag else ""
    return '    <div class="spec"><div class="label">%s</div><div class="value%s">%s%s</div></div>' % (
        esc(label), " mono" if mono else "", value, t)


def table(rows, header=None):
    out = ['  <div class="tablewrap"><table class="detail">']
    if header:
        out.append("    <thead><tr>%s</tr></thead>" % "".join("<th>%s</th>" % esc(h) for h in header))
    out.append("    <tbody>")
    out.extend(rows)
    out.append("    </tbody>")
    out.append("  </table></div>")
    return "\n".join(out)


def host_section(h, d):
    cpu = d["cpu"]
    sockets = int(cpu["sockets"] or 1)
    cores = sockets * int(cpu["cores_per_socket"] or 0)
    threads = int(cpu["threads"] or 0)
    mem = d["memory"]
    nets = [f for f in d["filesystems"] if f["network"]]
    local = [f for f in d["filesystems"] if not f["network"] and f["target"] not in ("/boot/efi", "/recovery")]
    root = next((f for f in local if f["target"] == "/"), None)
    addr = next((a for a in d["addresses"] if not a["interface"].startswith(("docker", "br-", "veth", "wg"))), None)
    ip = addr["address"].split("/")[0] if addr else "?"
    head_ip = "" if h.get("hide_ip") else "%s %s " % (esc(ip), "&middot;")
    wg = next((a for a in d["addresses"] if a["interface"].startswith("wg")), None)

    cards = [spec("CPU", esc(clean_cpu(cpu["model"])) + ("" if sockets == 1 else " &times; %d" % sockets)),
             spec("Threads", "%d (%dc)" % (threads, cores))]
    cards.append(spec("RAM", size(mem["total_gib"]).replace(".0 GiB", " GiB"), "%s used" % size(mem["used_gib"])))
    if d["gpus"]:
        g = {}
        for x in d["gpus"]:
            g.setdefault((clean_gpu(x["name"]), x["vram_gib"]), 0)
            g[(clean_gpu(x["name"]), x["vram_gib"])] += 1
        gtxt = "<br>".join("%s&times; %s %d GiB" % ("%d" % n if n > 1 else "1", esc(nm), round(v or 0)) for (nm, v), n in g.items())
        cards.append(spec("GPU", gtxt.replace("1&times; ", "")))
    elif any("Intel" in a or "AMD" in a for a in d["display_adapters"]):
        cards.append(spec("GPU", "none (integrated graphics)"))
    else:
        cards.append(spec("GPU", "none"))
    if root:
        cards.append(spec("Disk /", size(root["size_gib"]), "%d%% used" % pct(root["used_gib"], root["size_gib"])))
    if d["physical_disks"]:
        grouped = {}
        for x in d["physical_disks"]:
            grouped.setdefault(drive_label(x), 0)
            grouped[drive_label(x)] += 1
        dtxt = "<br>".join(("%d&times; " % n if n > 1 else "") + esc(lbl) for lbl, n in grouped.items())
        cards.append('    <div class="spec"><div class="label">Drives</div><div class="value" style="font-size:0.82rem">%s</div></div>' % dtxt)
    for r in d.get("raid", []):
        if r["level"] == "raid5":
            big = next((f for f in local if f["fstype"] == "ext4" and f["size_gib"] > 4000), None)
            extra = (" - %s, %s used" % (size(big["size_gib"]), size(big["used_gib"]))) if big else ""
            cards.append(spec("RAID", "RAID5 &middot; %d disks%s" % (r["members"], esc(extra)), r.get("status", "").strip() or None))
    if nets:
        seen = []
        for n in nets:
            src = n["source"].split(":")[0]
            if src not in [s[0] for s in seen]:
                seen.append((src, n))
        label = ", ".join("%s (%s, %d%% used)" % ({"10.0.0.60": "Z4 NAS", "10.0.0.6": "NAS 10.0.0.6"}.get(s, s), size(n["size_gib"]), pct(n["used_gib"], n["size_gib"])) for s, n in seen)
        cards.append('    <div class="spec"><div class="label">NAS mounts</div><div class="value mono" style="font-size:0.8rem">%s</div></div>' % esc(label))

    parts = ['<section id="%s">' % h["sid"],
             '  <h2>%s <span class="role">%s%s &middot; alias <code class="mono">%s</code> &middot; hostname <span class="mono">%s</span></span></h2>' % (
                 esc(h["title"]), head_ip, esc(d.get("user") or h["user"]), esc(h["alias"]), esc(d["hostname"])),
             '  <div class="note" style="margin-top:0">%s &middot; kernel %s &middot; %s%s%s</div>' % (
                 esc(d["os"]), esc(d["kernel"]), esc(d["uptime"]),
                 (" &middot; WireGuard %s" % esc(wg["address"].split("/")[0])) if wg else "",
                 (" &middot; " + esc(h["note_extra"])) if h.get("note_extra") else ""),
             '  <div class="spec-grid" style="margin-top:0.9rem">'] + cards + ["  </div>"]

    # containers
    if d["containers"]:
        rows = []
        for c in d["containers"]:
            stopped = c["state"] != "running"
            dot = "status-dot stopped" if stopped else "status-dot"
            desc = CONTAINER_DESC.get(c["name"], short_image(c["image"]))
            rows.append('      <tr><td><span class="%s"></span>%s</td><td>%s</td><td class="muted">%s</td></tr>' % (
                dot, esc(c["name"]), esc(desc), esc(c["status"])))
        run = sum(1 for c in d["containers"] if c["state"] == "running")
        parts.append('  <div class="subhead">Docker containers (%d running, %d stopped)</div>' % (run, len(d["containers"]) - run))
        parts.append(table(rows))
    else:
        parts.append('  <div class="subhead">Docker containers</div>\n  <div class="note" style="margin-top:0">None. This machine does not use Docker.</div>')

    # custom services
    custom_names = {s["unit"].replace(".service", "") for s in d["services"] + d["user_services"]}
    active = [s for s in d["services"] if s["active"] == "active"]
    uactive = [s for s in d["user_services"] if s["active"] == "active"]
    if active or uactive:
        parts.append('  <div class="subhead">Services set up on this machine (running)</div>\n  <ul class="svc-list">')
        for s in active:
            parts.append("    <li><b>%s</b>%s</li>" % (esc(s["unit"].replace(".service", "")), (" &mdash; " + esc(s["description"])) if s["description"] else ""))
        for s in uactive:
            parts.append("    <li><b>%s</b> (user)%s</li>" % (esc(s["unit"].replace(".service", "")), (" &mdash; " + esc(s["description"])) if s["description"] else ""))
        parts.append("  </ul>")
    others = other_services(d, custom_names | SATELLITE_BORING_SERVICES)
    if others:
        parts.append('  <div class="note">Other server software running: <span class="mono">%s</span></div>' % esc(", ".join(others)))

    # schedules
    sched = []
    for t in d["timers"]:
        if t["active"] == "active":
            n = nice_next(t.get("next"))
            sched.append("<li><b>%s</b>%s%s</li>" % (esc(t["unit"].replace(".timer", "")), (" &mdash; " + esc(t["description"])) if t["description"] else "", (" (next %s)" % esc(n)) if n else ""))
    for c in d["cron"]:
        if re.fullmatch(r"[0-9a-f]{32}\.log", c["script"]):
            continue
        sched.append("<li>Cron %s: <span class=\"mono\">%s</span></li>" % (esc(cron_text(c["schedule"])), esc(c["script"])))
    if h["key"] == "vps":
        n = sum(1 for c in d["cron"] if re.fullmatch(r"[0-9a-f]{32}\.log", c["script"]))
        if n:
            sched.append("<li>%d scheduled tasks managed by the aaPanel control panel</li>" % n)
    if sched:
        parts.append('  <div class="subhead">Scheduled jobs</div>\n  <ul class="svc-list">\n    %s\n  </ul>' % "\n    ".join(sched))
    parts.append("</section>")
    return "\n".join(parts)


def summary_row(h, d):
    cpu = d["cpu"]
    sockets = int(cpu["sockets"] or 1)
    threads = int(cpu["threads"] or 0)
    addr = next((a for a in d["addresses"] if not a["interface"].startswith(("docker", "br-", "veth", "wg"))), None)
    ip = "" if h.get("hide_ip") else addr["address"].split("/")[0] if addr else ""
    root = next((f for f in d["filesystems"] if f["target"] == "/"), None)
    gpu = ("%d&times; %s" % (len(d["gpus"]), esc(clean_gpu(d["gpus"][0]["name"]))) if len(d["gpus"]) > 1 else esc(clean_gpu(d["gpus"][0]["name"]))) if d["gpus"] else "&mdash;"
    big = max((f for f in d["filesystems"] if not f["network"]), key=lambda f: f["size_gib"], default=root)
    disk = size(root["size_gib"]) if root else "&mdash;"
    if big and big is not root and big["size_gib"] > 4000:
        disk += " + %s RAID" % size(big["size_gib"])
    return ("        <tr>\n          <td class=\"host\">%s%s</td>\n          <td>%s</td>\n          <td>%s<br>%d threads</td>\n"
            "          <td>%s</td>\n          <td>%s</td>\n          <td>%s</td>\n          <td>%s</td>\n        </tr>") % (
        esc(h["title"]), ('<span class="ip">%s</span>' % esc(ip)) if ip else "", esc(h["role"]),
        esc(clean_cpu(cpu["model"])) + ("" if sockets == 1 else " &times; %d" % sockets), threads,
        size(d["memory"]["total_gib"]).replace(".0 GiB", " GiB"), gpu, disk,
        esc(re.sub(r"GNU/Linux ", "", d["os"]).replace(" (trixie)", "").replace(" LTS", "")))


# ------------------------------------------------------------------ the three hosts without the same collector
def ha_section(ha):
    sm = {k.replace("sensor.system_monitor_", ""): v for k, v in ha["system_monitor"].items()}
    used = float(sm.get("memory_use", 0)) / 1024.0
    free = float(sm.get("memory_free", 0)) / 1024.0
    host, core, osr, sup = ha["host"], ha["core"], ha["os"], ha["supervisor"]
    addons = sorted(ha["addons"], key=lambda a: (a["state"] != "started", a["name"]))
    rows = []
    for a in addons:
        stopped = a["state"] != "started"
        word = {"started": "running", "stopped": "stopped", "error": "ERROR - not running"}.get(a["state"], a["state"])
        rows.append('      <tr><td><span class="status-dot%s"></span>%s</td><td class="muted">%s</td><td class="muted">%s</td></tr>' % (
            " stopped" if stopped else "", esc(a["name"]), esc(a["version"] or ""), esc(word)))
    return "\n".join([
        '<section id="ha">',
        '  <h2>Home Assistant <span class="role">10.0.0.30 &middot; dedicated PC, HAOS &middot; alias <code class="mono">apex-agent-ha</code></span></h2>',
        '  <div class="note" style="margin-top:0">%s &middot; kernel %s &middot; HA Core %s &middot; Supervisor %s &middot; %d entities, %d components loaded</div>' % (
            esc(host["operating_system"]), esc(host["kernel"]), esc(core["version"]), esc(sup["version"]), ha["entities"], ha["ha"]["components"]),
        '  <div class="spec-grid" style="margin-top:0.9rem">',
        spec("CPU", "Intel Core i5-8400"), spec("Threads", "6 (6c)"),
        spec("RAM", "7.6 GiB", "%.1f GiB used" % used),
        spec("GPU", "none (integrated graphics)"),
        spec("Disk", "%d GB" % round(host["disk_total"]), "%d%% used" % pct(host["disk_used"], host["disk_total"])),
        spec("Media mount", "Z4 Public share (mount still named PrimaryEX4100_Media)", "%d%% used" % int(float(sm.get("disk_usage_media_primaryex4100_media", 0)))),
        "  </div>",
        '  <div class="subhead">Add-ons (%d)</div>' % len(addons),
        table(rows),
        '  <div class="note">CPU and RAM are read from /proc inside the SSH add-on, which shares the host kernel. Frigate and Node-RED are stopped on purpose: Frigate runs on the Z4 and Node-RED on the main server. Cloudflared (the tunnel behind ha.magnumz.com) runs with its watchdog turned on, so Home Assistant restarts it if it stops.</div>',
        "</section>"])


def nas_section(d_nas_mount):
    return "\n".join([
        '<section id="nas2">',
        '  <h2>Second NAS <span class="role">10.0.0.6 &middot; network storage, no shell access set up</span></h2>',
        '  <div class="note" style="margin-top:0">CPU, RAM and drive details cannot be read remotely. WD My Cloud style firmware (shares live under /mnt/HD/HD_a2). Open ports: SSH, HTTP, SMB, NFS.</div>',
        '  <div class="spec-grid" style="margin-top:0.9rem">',
        spec("Capacity", size(d_nas_mount["size_gib"]), "%d%% used" % pct(d_nas_mount["used_gib"], d_nas_mount["size_gib"])),
        spec("NFS shares", "backup, HAbackup, Public, frigate, skycam2", None, mono=True),
        spec("Role", "Second copy of every backup and of the Z4 data"),
        "  </div>",
        "</section>"])


# Hand-maintained (update when backups or findings change): BACKUPS and NEEDS_A_LOOK below.
BACKUPS = [
    ("Main server", "02:00", "Z4 + 10.0.0.6", "home folder, /opt, system configs (minus ComfyUI models and wake-word training data)", "68 GiB"),
    ("EPYC box", "02:45", "Z4 + 10.0.0.6", "configs, scripts, OpenCode state", "0.7 GiB"),
    ("Z4", "03:30", "its RAID + 10.0.0.6", "configs, compose projects, Frigate database", "0.8 GiB"),
    ("Living room satellite", "03:50", "Z4 + 10.0.0.6", "voice pipeline code and config", "146 MiB"),
    ("Bedroom satellite", "04:10", "Z4 + 10.0.0.6", "voice pipeline, headset and mic/speaker servers", "173 MiB"),
    ("Jellyfin and kitchen PC", "04:30", "Z4 + 10.0.0.6", "Jellyfin settings, database and artwork, kitchen satellite", "1.8 GiB"),
    ("Sky camera", "10:15", "Z4 + 10.0.0.6", "software, settings, calibration (frames are archived by retention)", "2.9 GiB"),
]


def backups_section():
    rows = ["      <tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td class=\"muted\">%s</td></tr>" % tuple(esc(x) for x in r) for r in BACKUPS]
    return "\n".join([
        '<section id="backups">',
        "  <h2>Backups <span class=\"role\">restic, two copies each, 7 daily / 4 weekly / 3 monthly</span></h2>",
        table(rows, ["Machine", "Runs", "Copies on", "Saves", "First snapshot"]),
        '  <div class="note">Also: Home Assistant makes its own daily backups (kept locally, on the Z4 and on 10.0.0.6). Postgres is dumped nightly to the VPS. The Z4\'s recordings, Public share and the skycam archive are mirrored nightly to 10.0.0.6 (a mirror, without history). The watchdog dashboard checks every backup and warns after two missed days.</div>',
        "</section>"])


NEEDS_A_LOOK = [
    ("Main server", "postfix@-.service (mail) is in a failed state."),
    ("Living room and bedroom satellites", "The login script luna-output.service, which forces the eMeet Luna as the default audio output, has failed on both (last run on the living room: 27 Aug). The satellites themselves run normally."),
    ("Bedroom and sky camera", "NetworkManager-wait-online.service fails at boot. Harmless unless something waits for it."),
]


def attention_section():
    items = []
    for where, what in NEEDS_A_LOOK:
        items.append('    <div class="flag">\n      <div class="where">%s</div>\n      <div class="what">%s</div>\n    </div>' % (esc(where), esc(what)))
    leftovers = ("Stopped containers left behind: frigate on the EPYC box, parakeet-stt on the Z4, and the old openwakeword container on both satellites. "
                 "The Frigate and Node-RED add-ons in Home Assistant are stopped on purpose.")
    return "\n".join(['<section id="attention">', '  <h2>Needs a look <span class="role">found while collecting this snapshot</span></h2>',
                      '  <div class="flag-list">', *items, "  </div>", '  <div class="note">%s</div>' % esc(leftovers), "</section>"])


# ------------------------------------------------------------------ assemble
def main():
    css = "<style>\n" + CSS.read_text(encoding="utf-8") + "</style>"
    data = {h["key"]: load(h["key"]) for h in HOSTS}
    ha = load("ha")
    nas_mount = next(f for f in data["main"]["filesystems"] if f["target"] == "/mnt/nas2")

    nav = ['    <a href="#summary">Summary</a>', '    <a href="#attention">Needs a look</a>']
    for h in HOSTS[:7]:
        nav.append('    <a href="#%s">%s</a>' % (h["sid"], esc(h["nav"])))
    nav += ['    <a href="#ha">Home Assistant</a>', '    <a href="#nas2">NAS 10.0.0.6</a>', '    <a href="#vps">VPS</a>', '    <a href="#backups">Backups</a>']

    rows = [summary_row(h, data[h["key"]]) for h in HOSTS[:7]]
    # Home Assistant, NAS and VPS rows in the same table
    rows.insert(7, ("        <tr>\n          <td class=\"host\">Home Assistant<span class=\"ip\">10.0.0.30</span></td>\n          <td>Smart-home hub (Z-Wave, Matter, Music Assistant)</td>\n"
                    "          <td>Intel Core i5-8400<br>6 threads</td>\n          <td>7.6 GiB</td>\n          <td>&mdash;</td>\n          <td>916 GB</td>\n          <td>HAOS %s</td>\n        </tr>") % esc(ha["os"]["version"]))
    rows.insert(8, ("        <tr>\n          <td class=\"host\">Second NAS<span class=\"ip\">10.0.0.6</span></td>\n          <td>Second copy of backups and Z4 data</td>\n"
                    "          <td colspan=\"3\">not readable remotely</td>\n          <td>%s</td>\n          <td>NAS firmware</td>\n        </tr>") % size(nas_mount["size_gib"]))

    vps = next(h for h in HOSTS if h["key"] == "vps")
    vps_row = rows[6]  # the vps row was built as the 7th host (index 6)
    # reorder: hosts in HOSTS order are main, epyc, z4, livingroom, bedroom, jellyfin, skycam, (vps is index 7 in HOSTS)
    ordered_rows = [summary_row(h, data[h["key"]]) for h in HOSTS if h["key"] != "vps"]
    ordered_rows += [rows[7], rows[8], summary_row(vps, data["vps"])]

    body = []
    for h in HOSTS:
        if h["key"] == "vps":
            continue
        body.append(host_section(h, data[h["key"]]))
    body.append(ha_section(ha))
    body.append(nas_section(nas_mount))
    body.append(host_section(vps, data["vps"]))

    subtitle = ("Snapshot taken %s &middot; read-only capture of every machine over SSH and Home Assistant's own API &middot; "
                "EPYC is the box that used to be called X99" % SNAPSHOT_DATE)
    page = "\n".join([
        "<title>Home AI Infrastructure Inventory</title>",
        css, "",
        '<header class="page">', "  <h1>Home AI Infrastructure Inventory</h1>", '  <div class="sub">%s</div>' % subtitle,
        '  <nav class="jump">', *nav, "  </nav>", "</header>", "",
        '<section id="summary">', "  <h2>At a glance</h2>", '  <div class="summary-wrap">', '    <table class="summary mono">', "      <thead>",
        "        <tr><th>Host</th><th>Role</th><th>CPU</th><th>RAM</th><th>GPU</th><th>Disk</th><th>OS</th></tr>", "      </thead>", "      <tbody>",
        *ordered_rows, "      </tbody>", "    </table>", "  </div>", "</section>", "",
        attention_section(), "",
        *[b + "\n" for b in body], backups_section(), "",
        "<footer>",
        "  Read-only snapshot gathered on %s over SSH, plus Home Assistant's own API for the HA box. Nothing was changed while collecting. "
        "Sizes use binary units (GiB, TiB); drive sizes are the nominal labels." % SNAPSHOT_DATE,
        "</footer>", ""])
    OUT.write_text(page, encoding="utf-8")
    print("written", OUT, len(page), "chars;", page.count("<section"), "sections")


if __name__ == "__main__":
    main()
