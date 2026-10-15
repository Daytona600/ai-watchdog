import { tool } from "@opencode-ai/plugin"

const VALID_HOSTS = [
  "apex-agent-server",
  "apex-agent-second",
  "apex-agent-z4",
  "apex-agent-livingroom",
  "apex-agent-bedroom",
  "apex-agent-jellyfin",
  "apex-agent-skycam",
]

// The remote shell re-parses the command, so quote the path for it (2026-10-14: a path with ; or $() ran commands).
const q = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`

export default tool({
  description:
    "Read a file from one of the other home-lab servers (main server, EPYC, Z4, living room/bedroom/kitchen satellites, sky camera) via SSH. Use this when a fix or investigation needs to look at a file on a DIFFERENT box than the one you're currently working on.",
  args: {
    host: tool.schema
      .string()
      .describe(
        "Target host alias: apex-agent-server, apex-agent-second, apex-agent-z4, apex-agent-livingroom, apex-agent-bedroom, apex-agent-jellyfin (kitchen satellite + Jellyfin), or apex-agent-skycam (sky camera)"
      ),
    path: tool.schema.string().describe("Absolute path to the file on the target host"),
  },
  async execute(args) {
    if (!VALID_HOSTS.includes(args.host)) {
      return `Error: unknown host "${args.host}". Valid hosts: ${VALID_HOSTS.join(", ")}`
    }
    try {
      const result = await Bun.$`ssh ${args.host} ${"cat -- " + q(args.path)}`.text()
      return result
    } catch (e) {
      return `Error reading ${args.path} on ${args.host}: ${e}`
    }
  },
})
