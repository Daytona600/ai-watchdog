#!/usr/bin/env bash
# Print the path, inside the Node-RED container, of the flows file Node-RED is
# actually running.
#
# Node-RED runs in projects mode, so the live flows live in
# /data/projects/<activeProject>/<flowFile> (the project's git repo), not
# /data/flows.json. The old top-level file was a stale pre-projects copy and was
# renamed to flows-old.json on 2026-09-27. Falls back to /data/flows.json if
# projects mode is ever turned off.
#
# Usage: nodered_flows_path.sh [container]   (default: $NODERED_CONTAINER or nodered)
set -u

CONTAINER="${1:-${NODERED_CONTAINER:-nodered}}"

docker exec "$CONTAINER" node -e '
const fs = require("fs");
let path = "/data/flows.json";
try {
  const project = JSON.parse(fs.readFileSync("/data/.config.projects.json", "utf8")).activeProject;
  if (project) {
    let flowFile = "flows.json";
    try {
      const pkg = JSON.parse(fs.readFileSync(`/data/projects/${project}/package.json`, "utf8"));
      flowFile = pkg["node-red"]?.settings?.flowFile || flowFile;
    } catch (e) {}
    const candidate = `/data/projects/${project}/${flowFile}`;
    if (fs.existsSync(candidate)) path = candidate;
  }
} catch (e) {}
process.stdout.write(path);
'
