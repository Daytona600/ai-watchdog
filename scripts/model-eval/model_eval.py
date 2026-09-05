#!/usr/bin/env python3
"""
model_eval.py - Compare two Ollama models against a fixed set of real
conversation examples pulled from memory.conversations (Postgres).

Built 2026-09-05 to answer one question before swapping any production
model: is the candidate actually better, on OUR real traffic, at an
acceptable speed - not just "scores higher on a public benchmark."

Usage:
    python3 model_eval.py --baseline qwen2.5:14b --candidate qwen3:14b
    python3 model_eval.py --baseline qwen2.5:14b --candidate qwen3:14b --limit 20
    python3 model_eval.py --baseline qwen2.5:14b --candidate qwen3:14b --persona mary

Requires: requests (pip install requests)
Reads test_cases.json + mary_prompt.txt/david_prompt.txt from the same
directory as this script. Writes report.html next to them.

Notes / known limitations, so results aren't over-trusted:
  - Test cases are real (text, reply) pairs pulled from memory.conversations,
    but the ORIGINAL reply may have been produced under an older version of
    the persona prompt or an older model - it's shown for human reference,
    not treated as ground truth to exactly match.
  - Both baseline and candidate are run under TODAY's current persona prompt
    (pulled fresh from config_sections at export time), so the model is the
    only variable being compared - this is the actually meaningful
    comparison, not old-reply-vs-new-model.
  - No temperature override is sent - production's real temperature setting
    for these calls wasn't confirmed in config_sections, so this uses
    Ollama's default rather than guessing a number. If production is later
    found to set an explicit temperature, add it to OPTIONS below to match.
  - Correctness is NOT auto-graded here. For tool-calling/device-command
    cases that could be checked programmatically, that's a reasonable next
    addition - but free-form persona chat quality still needs a human
    (David) reading the side-by-side output. This tool gives you the speed
    numbers automatically and puts the text side by side so that judgment
    is fast, not so it's replaced.
"""
import argparse
import json
import time
import sys
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
OLLAMA_HOST = "10.0.0.35"
OLLAMA_PORT = 11434
TIMEOUT_SECONDS = 120


def load_prompts():
    return {
        "mary": (SCRIPT_DIR / "mary_prompt.txt").read_text(encoding="utf-8"),
        "david": (SCRIPT_DIR / "david_prompt.txt").read_text(encoding="utf-8"),
    }


def load_test_cases(limit=None, persona_filter=None):
    cases = json.loads((SCRIPT_DIR / "test_cases.json").read_text(encoding="utf-8"))
    if persona_filter:
        cases = [c for c in cases if c["persona"] == persona_filter]
    if limit:
        cases = cases[:limit]
    return cases


def call_model(model: str, system_prompt: str, user_text: str) -> dict:
    """One Ollama /api/chat call. Returns dict with reply text + timing, or
    an 'error' key on failure - callers must check for it rather than assume
    success."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
    }
    wall_start = time.monotonic()
    try:
        resp = requests.post(
            f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat",
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "wall_seconds": time.monotonic() - wall_start}

    wall_seconds = time.monotonic() - wall_start
    eval_count = data.get("eval_count", 0)
    eval_duration_ns = data.get("eval_duration", 0)
    tokens_per_sec = (eval_count / (eval_duration_ns / 1e9)) if eval_duration_ns else None
    total_duration_ns = data.get("total_duration", 0)

    return {
        "reply": data.get("message", {}).get("content", ""),
        "wall_seconds": wall_seconds,
        "total_seconds": total_duration_ns / 1e9 if total_duration_ns else wall_seconds,
        "eval_count": eval_count,
        "tokens_per_sec": tokens_per_sec,
    }


def html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def build_report(results: list, baseline: str, candidate: str) -> str:
    def fmt_stats(runs, key):
        vals = [r[key] for r in runs if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    baseline_runs = [r["baseline"] for r in results if "error" not in r["baseline"]]
    candidate_runs = [r["candidate"] for r in results if "error" not in r["candidate"]]

    baseline_errors = sum(1 for r in results if "error" in r["baseline"])
    candidate_errors = sum(1 for r in results if "error" in r["candidate"])

    avg_b_tps = fmt_stats(baseline_runs, "tokens_per_sec")
    avg_c_tps = fmt_stats(candidate_runs, "tokens_per_sec")
    avg_b_sec = fmt_stats(baseline_runs, "total_seconds")
    avg_c_sec = fmt_stats(candidate_runs, "total_seconds")

    rows_html = []
    for r in results:
        b, c = r["baseline"], r["candidate"]
        b_text = html_escape(b.get("reply", b.get("error", "")))
        c_text = html_escape(c.get("reply", c.get("error", "")))
        b_meta = f"{b['total_seconds']:.2f}s, {b['tokens_per_sec']:.1f} tok/s" if b.get("tokens_per_sec") else b.get("error", "")
        c_meta = f"{c['total_seconds']:.2f}s, {c['tokens_per_sec']:.1f} tok/s" if c.get("tokens_per_sec") else c.get("error", "")
        rows_html.append(f"""
        <tr>
          <td class="meta">#{r['id']}<br>{html_escape(r['persona'])}/{html_escape(r['room'])}</td>
          <td class="input">{html_escape(r['text'])}</td>
          <td class="historical">{html_escape(r['historical_reply'])}</td>
          <td class="baseline">{b_text}<div class="timing">{b_meta}</div></td>
          <td class="candidate">{c_text}<div class="timing">{c_meta}</div></td>
        </tr>""")

    summary = f"""
    <div class="summary">
      <p><b>Baseline:</b> {html_escape(baseline)} - avg {avg_b_sec:.2f}s/reply, {avg_b_tps:.1f} tok/s ({baseline_errors} errors)</p>
      <p><b>Candidate:</b> {html_escape(candidate)} - avg {avg_c_sec:.2f}s/reply, {avg_c_tps:.1f} tok/s ({candidate_errors} errors)</p>
      <p>{len(results)} test cases from memory.conversations. Read each row and judge whether the candidate's reply is as good as (or better than) the baseline's - speed numbers are automatic, correctness judgment is not.</p>
    </div>""" if avg_b_tps and avg_c_tps else "<div class='summary'><p>Some calls failed - check the error text in the table below.</p></div>"

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Model eval: {html_escape(baseline)} vs {html_escape(candidate)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 20px; background: #fafafa; }}
table {{ border-collapse: collapse; width: 100%; background: white; }}
td, th {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; font-size: 13px; }}
th {{ background: #333; color: white; position: sticky; top: 0; }}
.meta {{ width: 90px; color: #666; }}
.input {{ width: 18%; font-weight: bold; }}
.historical {{ width: 18%; color: #888; font-style: italic; }}
.baseline {{ width: 22%; background: #eef; }}
.candidate {{ width: 22%; background: #efe; }}
.timing {{ margin-top: 6px; font-size: 11px; color: #666; }}
.summary {{ background: white; border: 1px solid #ddd; padding: 12px 16px; margin-bottom: 16px; }}
</style></head>
<body>
<h2>Model comparison: {html_escape(baseline)} (baseline) vs {html_escape(candidate)} (candidate)</h2>
{summary}
<table>
<tr><th>ID / context</th><th>User said</th><th>Historical reply (reference only)</th><th>Baseline reply</th><th>Candidate reply</th></tr>
{''.join(rows_html)}
</table>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, help="Ollama model tag currently in production, e.g. qwen2.5:14b")
    ap.add_argument("--candidate", required=True, help="Ollama model tag to evaluate, e.g. qwen3:14b")
    ap.add_argument("--limit", type=int, default=None, help="Only run the first N test cases (default: all)")
    ap.add_argument("--persona", choices=["mary", "david"], default=None, help="Only test cases for this persona")
    ap.add_argument("--output", default=str(SCRIPT_DIR / "report.html"))
    args = ap.parse_args()

    prompts = load_prompts()
    cases = load_test_cases(limit=args.limit, persona_filter=args.persona)
    print(f"Running {len(cases)} test cases: {args.baseline} vs {args.candidate}")

    results = []
    for i, case in enumerate(cases, 1):
        system_prompt = prompts.get(case["persona"], prompts["david"])
        print(f"[{i}/{len(cases)}] ({case['persona']}) {case['text'][:60]!r}", end=" ", flush=True)

        baseline_result = call_model(args.baseline, system_prompt, case["text"])
        candidate_result = call_model(args.candidate, system_prompt, case["text"])

        b_ok = "error" not in baseline_result
        c_ok = "error" not in candidate_result
        print(f"- baseline {'OK' if b_ok else 'FAIL'}, candidate {'OK' if c_ok else 'FAIL'}")

        results.append({**case, "baseline": baseline_result, "candidate": candidate_result})

    report = build_report(results, args.baseline, args.candidate)
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"\nReport written to {args.output}")


if __name__ == "__main__":
    main()
