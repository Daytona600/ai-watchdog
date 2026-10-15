import { tool } from "@opencode-ai/plugin"

// Read-only look at another home-lab host, for the "fixer" agent (AI Watchdog Fix-it sessions).
// Only fixed command templates run; the one free-text argument is validated and single-quoted,
// so this tool can be allowed without approval while plain `ssh` still asks.
// Added 2026-10-14.

const HOSTS = [
  "apex-agent-server",
  "apex-agent-second",
  "apex-agent-z4",
  "apex-agent-livingroom",
  "apex-agent-bedroom",
  "apex-agent-jellyfin",
  "apex-agent-skycam",
]

const q = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`

// {T} = target (validated), {N} = line count
const CHECKS: Record<string, { cmd: string; needsTarget?: boolean; help: string }> = {
  overview: {
    cmd: "hostname; uptime; df -h -x tmpfs -x overlay -x squashfs; free -h; systemctl --failed --no-legend --plain",
    help: "uptime, disks, memory, failed units",
  },
  service_status: { cmd: "systemctl status {T} --no-pager -n 30", needsTarget: true, help: "systemd unit status (target = unit)" },
  user_service_status: { cmd: "systemctl --user status {T} --no-pager -n 30", needsTarget: true, help: "systemd --user unit status" },
  service_logs: { cmd: "sudo -n journalctl -u {T} -n {N} --no-pager", needsTarget: true, help: "journal of a unit (target = unit)" },
  journal_errors: { cmd: "sudo -n journalctl -p err -n {N} --no-pager --since -24h", help: "errors in the last 24h" },
  dmesg_errors: { cmd: "sudo -n dmesg -T --level=err,warn | tail -n {N}", help: "kernel warnings/errors" },
  docker_ps: { cmd: "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}'", help: "all containers" },
  container_status: {
    cmd: "docker inspect -f 'status={{.State.Status}} restarts={{.RestartCount}} started={{.State.StartedAt}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} compose_dir={{index .Config.Labels \"com.docker.compose.project.working_dir\"}}' {T}",
    needsTarget: true,
    help: "container state + compose directory (target = container)",
  },
  container_logs: { cmd: "docker logs --tail {N} {T} 2>&1", needsTarget: true, help: "container logs (target = container)" },
  disk_usage: { cmd: "df -h; echo; sudo -n du -xh --max-depth=1 {T} 2>/dev/null | sort -h | tail -15", needsTarget: true, help: "df + biggest dirs under target path" },
  mounts: { cmd: "findmnt -t nfs,nfs4,cifs,ext4,xfs,btrfs; echo; cat /proc/mdstat 2>/dev/null", help: "mounts and md RAID" },
  smart: { cmd: "sudo -n smartctl -H -A {T}", needsTarget: true, help: "SMART health (target = /dev/sdX or /dev/nvme0)" },
  ports: { cmd: "sudo -n ss -ltnp", help: "listening TCP ports" },
  processes: { cmd: "ps -eo pid,user,etime,pcpu,pmem,rss,comm --sort=-pcpu | head -n {N}", help: "top processes" },
  process_fds: { cmd: "for p in $(pgrep -f {T}); do echo \"$p $(cat /proc/$p/comm) fds=$(sudo -n ls /proc/$p/fd | wc -l)\"; done", needsTarget: true, help: "open files per matching process" },
  gpu: { cmd: "nvidia-smi 2>/dev/null || rocm-smi 2>/dev/null || echo 'no GPU tool'", help: "GPU status" },
  file_tail: { cmd: "sudo -n tail -n {N} {T}", needsTarget: true, help: "last lines of a log file (target = path)" },
  http_probe: {
    cmd: "curl -sS -o /dev/null -m 8 -w 'HTTP %{http_code} in %{time_total}s\n' {T}",
    needsTarget: true,
    help: "GET a URL from that host and report the HTTP status (target = http://host:port/path)",
  },
  // Home Assistant: always run on the main server, which holds the HA token. Calling HA's API from
  // anywhere without a token gets that machine's IP banned (EPYC, 2026-10-14).
  ha_state: {
    cmd: "set -a; . ~/ai-watchdog/config/ha_token.env; set +a; curl -sS -m 10 -H \"Authorization: Bearer $HA_TOKEN\" \"$HA_BASE_URL/api/states/\"{T}",
    needsTarget: true,
    help: "Home Assistant entity state + attributes (target = entity_id, e.g. light.kitchen); runs on the main server",
  },
  ha_find: {
    cmd: "set -a; . ~/ai-watchdog/config/ha_token.env; set +a; curl -sS -m 10 -H \"Authorization: Bearer $HA_TOKEN\" \"$HA_BASE_URL/api/states\" | python3 -c \"import json,sys; [print(s['entity_id'], '=', s['state']) for s in json.load(sys.stdin) if sys.argv[1] in s['entity_id']]\" {T}",
    needsTarget: true,
    help: "Home Assistant entities whose id contains target, with state; runs on the main server",
  },
  cron: { cmd: "crontab -l 2>/dev/null; ls /etc/cron.d; systemctl list-timers --all --no-pager | head -40", help: "cron jobs and timers" },
}

export default tool({
  description:
    "Read-only inspection of a home-lab host over SSH (no approval needed). Checks: " +
    Object.entries(CHECKS).map(([k, v]) => `${k} (${v.help})`).join("; ") +
    ". For anything else, or anything that changes the system, use bash (which asks David first).",
  args: {
    host: tool.schema.string().describe(`One of: ${HOSTS.join(", ")}`),
    check: tool.schema.string().describe(`One of: ${Object.keys(CHECKS).join(", ")}`),
    target: tool.schema.string().optional().describe("Unit, container, device or path the check needs"),
    lines: tool.schema.number().optional().describe("Line count for logs (default 80, max 400)"),
  },
  async execute(args) {
    if (args.check === "ha_state" || args.check === "ha_find") args.host = "apex-agent-server"
    if (!HOSTS.includes(args.host)) return `Error: unknown host "${args.host}". Valid: ${HOSTS.join(", ")}`
    const c = CHECKS[args.check]
    if (!c) return `Error: unknown check "${args.check}". Valid: ${Object.keys(CHECKS).join(", ")}`
    const target = (args.target ?? "").trim()
    if (c.needsTarget && !target) return `Error: check "${args.check}" needs a target`
    if (target && !/^[A-Za-z0-9@._:\/+-]{1,200}$/.test(target)) return "Error: target may only contain letters, digits and @._:/+-"
    if (args.check === "file_tail" && !(target.startsWith("/var/log/") || target.endsWith(".log")) || target.includes(".."))
      return "Error: file_tail only reads log files (under /var/log/ or ending in .log); use read_remote_file for others"
    const n = Math.min(Math.max(Math.floor(args.lines ?? 80), 1), 400)
    const remote = c.cmd.replaceAll("{T}", q(target)).replaceAll("{N}", String(n))
    try {
      const r = await Bun.$`ssh -o BatchMode=yes -o ConnectTimeout=6 ${args.host} ${remote}`.nothrow().quiet()
      const out = (r.stdout.toString() + r.stderr.toString()).trim()
      return (out.length > 20000 ? out.slice(-20000) : out) + (r.exitCode ? `\n[exit ${r.exitCode}]` : "") || "(no output)"
    } catch (e) {
      return `Error running ${args.check} on ${args.host}: ${e}`
    }
  },
})
