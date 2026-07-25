#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
OUT="$BASE/snapshots/nodered/$STAMP"
REPORT="$BASE/reports/watchdog-nodered-$STAMP.md"
CRITICAL_TABS="$BASE/config/nodered_critical_tabs.txt"
CONTAINER="${NODERED_CONTAINER:-nodered}"

mkdir -p "$OUT" "$BASE/reports"

ATTENTION="$OUT/attention-needed.txt"
: > "$ATTENTION"

add_attention() {
  echo "- $1" >> "$ATTENTION"
}

echo "# AI Watchdog Node-RED Report v1" > "$REPORT"
echo "" >> "$REPORT"
echo "Date: $(date)" >> "$REPORT"
echo "Host: $(hostname)" >> "$REPORT"
echo "Container: $CONTAINER" >> "$REPORT"
echo "" >> "$REPORT"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  add_attention "Node-RED container is not running: $CONTAINER"
  echo "Node-RED container is not running."
else
  docker cp "$CONTAINER:/data/flows.json" "$OUT/flows.raw.json" 2>"$OUT/docker-cp-error.txt" || true

  if [ ! -s "$OUT/flows.raw.json" ]; then
    add_attention "Could not read /data/flows.json from Node-RED container."
  fi
fi

if [ -s "$OUT/flows.raw.json" ]; then
  python3 - "$OUT/flows.raw.json" "$OUT/flows.sanitized.json" "$OUT/summary.txt" "$OUT/tabs.txt" "$OUT/types.txt" "$OUT/disabled-nodes.txt" "$CRITICAL_TABS" "$ATTENTION" <<'PY'
from pathlib import Path
import json
import sys
from collections import Counter, defaultdict

raw_path = Path(sys.argv[1])
sanitized_path = Path(sys.argv[2])
summary_path = Path(sys.argv[3])
tabs_path = Path(sys.argv[4])
types_path = Path(sys.argv[5])
disabled_path = Path(sys.argv[6])
critical_tabs_path = Path(sys.argv[7])
attention_path = Path(sys.argv[8])

data = json.loads(raw_path.read_text(errors="replace"))

secret_keys = {
    "credentials",
    "credential",
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
    "secret",
    "authorization",
    "bearer",
    "api_key",
    "apikey",
}

def sanitize(obj):
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            if k.lower() in secret_keys:
                clean[k] = "<redacted>"
            else:
                clean[k] = sanitize(v)
        return clean
    if isinstance(obj, list):
        return [sanitize(x) for x in obj]
    return obj

safe = sanitize(data)
sanitized_path.write_text(json.dumps(safe, indent=2, sort_keys=True))

tabs = [n for n in data if n.get("type") == "tab"]
tab_by_id = {n.get("id"): n.get("label", "") for n in tabs}
tab_labels = [n.get("label", "") for n in tabs]

nodes = [n for n in data if n.get("type") != "tab"]
types = Counter(n.get("type", "unknown") for n in nodes)
disabled = [n for n in nodes if n.get("disabled") is True]

tab_counts = defaultdict(int)
for n in nodes:
    z = n.get("z")
    tab_counts[tab_by_id.get(z, "<no tab>")] += 1

critical_terms = []
if critical_tabs_path.exists():
    for raw in critical_tabs_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            critical_terms.append(line)

missing = []
for term in critical_terms:
    if not any(term.lower() in label.lower() for label in tab_labels):
        missing.append(term)

with tabs_path.open("w") as f:
    for label in sorted(tab_labels, key=str.lower):
        f.write(label + "\n")

with types_path.open("w") as f:
    for typ, count in types.most_common():
        f.write(f"{count:5d}  {typ}\n")

with disabled_path.open("w") as f:
    for n in disabled:
        name = n.get("name") or n.get("label") or n.get("id")
        typ = n.get("type")
        tab = tab_by_id.get(n.get("z"), "<no tab>")
        f.write(f"{tab} | {typ} | {name}\n")

with summary_path.open("w") as f:
    f.write(f"Total objects: {len(data)}\n")
    f.write(f"Tabs: {len(tabs)}\n")
    f.write(f"Nodes/config objects: {len(nodes)}\n")
    f.write(f"Disabled nodes: {len(disabled)}\n")
    f.write("\nNodes per tab:\n")
    for tab, count in sorted(tab_counts.items(), key=lambda x: x[0].lower()):
        f.write(f"{count:5d}  {tab}\n")
    f.write("\nCritical tab check:\n")
    if missing:
        for term in missing:
            f.write(f"MISSING: {term}\n")
    else:
        f.write("All critical tab terms found.\n")

if missing:
    with attention_path.open("a") as f:
        for term in missing:
            f.write(f"- Node-RED critical tab term missing: {term}\n")
PY

  sha256sum "$OUT/flows.sanitized.json" > "$OUT/flows.sanitized.sha256"

  PREV="$(find "$BASE/snapshots/nodered" -mindepth 2 -maxdepth 2 -name flows.sanitized.sha256 2>/dev/null | sort | grep -v "$OUT/flows.sanitized.sha256" | tail -1 || true)"

  if [ -n "${PREV:-}" ] && [ -f "$PREV" ]; then
    PREV_DIR="$(dirname "$PREV")"

    echo "Previous snapshot: $PREV_DIR" > "$OUT/diff-summary.txt"
    echo "" >> "$OUT/diff-summary.txt"

    if cmp -s "$OUT/flows.sanitized.sha256" "$PREV"; then
      echo "No sanitized flow change detected." >> "$OUT/diff-summary.txt"
    else
      echo "Sanitized flow changed since previous snapshot." >> "$OUT/diff-summary.txt"
      echo "" >> "$OUT/diff-summary.txt"

      echo "Tab changes:" >> "$OUT/diff-summary.txt"
      diff -u "$PREV_DIR/tabs.txt" "$OUT/tabs.txt" 2>/dev/null | sed -n '1,120p' >> "$OUT/diff-summary.txt" || true

      echo "" >> "$OUT/diff-summary.txt"
      echo "Type-count changes:" >> "$OUT/diff-summary.txt"
      diff -u "$PREV_DIR/types.txt" "$OUT/types.txt" 2>/dev/null | sed -n '1,120p' >> "$OUT/diff-summary.txt" || true
    fi
  else
    echo "No previous Node-RED snapshot found. This is the first baseline." > "$OUT/diff-summary.txt"
  fi
fi

echo "## Summary" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$OUT/summary.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/summary.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No Node-RED flow summary generated." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Flow Diff" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$OUT/diff-summary.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/diff-summary.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No diff summary generated." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Disabled Nodes" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$OUT/disabled-nodes.txt" ]; then
  echo '```' >> "$REPORT"
  cat "$OUT/disabled-nodes.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
else
  echo "No disabled nodes found." >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Top Node Types" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$OUT/types.txt" ]; then
  echo '```' >> "$REPORT"
  head -40 "$OUT/types.txt" >> "$REPORT"
  echo '```' >> "$REPORT"
fi
echo "" >> "$REPORT"

echo "## Attention Needed" >> "$REPORT"
echo "" >> "$REPORT"
if [ -s "$ATTENTION" ]; then
  cat "$ATTENTION" >> "$REPORT"
else
  echo "No Node-RED attention items found." >> "$REPORT"
fi

echo ""
echo "Done."
echo "Node-RED snapshot saved to: $OUT"
echo "Node-RED report saved to:   $REPORT"
