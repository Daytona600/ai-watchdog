#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
HINTS_FILE="$BASE/config/watchdog_action_hints.tsv"
IGNORE_FILE="$BASE/config/watchdog_alert_ignore_patterns.txt"
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

python3 - "$REPORT" "$HINTS_FILE" "$IGNORE_FILE" "$OUT_MD" "$OUT_JSON" <<'PY'
from pathlib import Path
import json
import re
import sys
from datetime import datetime

report_path = Path(sys.argv[1])
hints_path = Path(sys.argv[2])
ignore_path = Path(sys.argv[3])
out_md = Path(sys.argv[4])
out_json = Path(sys.argv[5])

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

ignore_patterns = []
if ignore_path.exists():
    for raw in ignore_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ignore_patterns.append(line)

def ignored_problem(text: str) -> bool:
    for pat in ignore_patterns:
        try:
            if re.search(pat, text, flags=re.IGNORECASE):
                return True
        except re.error:
            if pat.lower() in text.lower():
                return True
    return False

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
    if ignored_problem(line):
        continue
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

matched_problem_keys = {m["problem"].lower() for m in matches}
unmatched = [p for p in deduped if p.lower() not in matched_problem_keys]

status = "ok" if not matches else "hints"
updated = datetime.now().astimezone().isoformat(timespec="seconds")

md = []
md.append("# Watchdog Action Hints")
md.append("")
md.append(f"Updated: {updated}")
md.append(f"Master report: `{report_path}`")
md.append("")

if not deduped:
    md.append("No current watchdog problems found.")
elif not matches:
    md.append("Watchdog problems were found, but no action hints matched them yet.")
    md.append("")
    md.append("## Problems without hints")
    md.append("")
    for problem in deduped:
        md.append(f"- {problem}")
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
    "unmatched": unmatched,
}, indent=2) + "\n")

print(f"Action hints written to: {out_md}")
print(f"Action hints JSON:       {out_json}")
print(f"Matched hint groups:     {len(matches)}")
PY
