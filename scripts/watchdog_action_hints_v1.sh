#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
HINTS_FILE="$BASE/config/watchdog_action_hints.tsv"
PUBLIC="$BASE/public"
mkdir -p "$PUBLIC"

REPORT="${1:-}"
if [ -z "$REPORT" ]; then
  REPORT="$(ls -t "$BASE"/reports/watchdog-master-*.md 2>/dev/null | head -1 || true)"
fi

OUT_MD="$PUBLIC/action-hints.md"
OUT_JSON="$PUBLIC/action-hints.json"

if [ -z "${REPORT:-}" ] || [ ! -f "$REPORT" ]; then
  cat > "$OUT_MD" <<EOF
# Watchdog Action Hints

No master report found.
EOF
  printf '{ "status": "attention", "message": "No master report found.", "hints": [] }\n' > "$OUT_JSON"
  echo "No master report found."
  exit 0
fi

python3 - "$REPORT" "$HINTS_FILE" "$OUT_MD" "$OUT_JSON" <<'PY'
from pathlib import Path
import json
import re
import sys
from datetime import datetime

report_path = Path(sys.argv[1])
hints_path = Path(sys.argv[2])
out_md = Path(sys.argv[3])
out_json = Path(sys.argv[4])

report = report_path.read_text(errors="replace")
lines = report.splitlines()

rules = []
if hints_path.exists():
    for raw in hints_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" not in raw:
            continue
        pattern, hint = raw.split("\t", 1)
        pattern = pattern.strip()
        hint = hint.strip()
        if pattern and hint:
            rules.append((pattern, hint))

# Pull likely problem lines from the report.
problem_lines = []
capture = False

interesting_headers = (
    "### Main Server Attention Needed",
    "### HA Critical Entity Problems",
    "### Frigate Camera Attention Needed",
    "### Node-RED Attention Needed",
    "### Storage/NAS Attention Needed",
    "## New Unavailable Entities",
    "## Critical Entity Problems Latest",
)

for line in lines:
    stripped = line.strip()

    if any(stripped.startswith(h) for h in interesting_headers):
        capture = True
        continue

    if capture and stripped.startswith("#"):
        capture = False

    if capture:
        if not stripped:
            continue
        if stripped.startswith("```"):
            continue
        if stripped.lower().startswith("no "):
            continue
        if stripped.startswith("- ") or "unavailable" in stripped or "unknown" in stripped:
            problem_lines.append(stripped)

# De-duplicate while preserving order.
seen = set()
deduped = []
for line in problem_lines:
    key = line.lower()
    if key not in seen:
        seen.add(key)
        deduped.append(line)

matches = []
for problem in deduped:
    problem_low = problem.lower()
    matched_hints = []
    for pattern, hint in rules:
        if pattern.lower() in problem_low:
            matched_hints.append(hint)
    if matched_hints:
        matches.append({
            "problem": problem,
            "hints": list(dict.fromkeys(matched_hints)),
        })

status = "ok" if not matches else "hints"
updated = datetime.now().astimezone().isoformat(timespec="seconds")

md = []
md.append("# Watchdog Action Hints")
md.append("")
md.append(f"Updated: {updated}")
md.append(f"Master report: `{report_path}`")
md.append("")

if not matches:
    md.append("No action hints matched current watchdog problems.")
else:
    for item in matches:
        md.append(f"## {item['problem']}")
        md.append("")
        for hint in item["hints"]:
            md.append(f"- {hint}")
        md.append("")

out_md.write_text("\n".join(md).rstrip() + "\n")

out_json.write_text(json.dumps({
    "status": status,
    "updated": updated,
    "master_report": str(report_path),
    "hints": matches,
}, indent=2) + "\n")

print(f"Action hints written to: {out_md}")
print(f"Action hints JSON:       {out_json}")
print(f"Matched hint groups:     {len(matches)}")
PY
