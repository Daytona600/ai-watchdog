#!/usr/bin/env python3
"""AI Watchdog AI Diagnose v1.

Reads public/action-hints.json, and for problems with no existing hint
(unmatched) or no past incident in Qdrant, asks the local Ollama model to
diagnose the problem and draft a fix, optionally grounding it with a
SearXNG web search. Writes a draft runbook under runbooks/ following the
existing runbook convention. Never edits any other file. Review-only:
generated runbooks are clearly marked as AI drafts for a human to check.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get("WATCHDOG_BASE", str(Path.home() / "ai-watchdog")))
CONF = BASE / "config" / "watchdog_llm.conf"
HINTS_JSON = BASE / "public" / "action-hints.json"
RUNBOOKS_DIR = BASE / "runbooks"
REPORTS_DIR = BASE / "reports"


def load_conf(path: Path) -> dict:
    conf = {}
    if not path.exists():
        return conf
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        conf[key.strip()] = value.strip().strip('"')
    return conf


def http_post_json(url: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def http_get_json(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "watchdog-problem"


def ollama_embed(conf: dict, text: str) -> list[float] | None:
    try:
        result = http_post_json(
            f"{conf['OLLAMA_URL']}/api/embeddings",
            {"model": conf["OLLAMA_EMBED_MODEL"], "prompt": text},
            int(conf["WATCHDOG_LLM_TIMEOUT_SEC"]),
        )
        return result.get("embedding")
    except (urllib.error.URLError, KeyError, json.JSONDecodeError):
        return None


def qdrant_search(conf: dict, vector: list[float]) -> dict | None:
    url = f"{conf['QDRANT_URL']}/collections/{conf['QDRANT_COLLECTION']}/points/search"
    try:
        result = http_post_json(
            url,
            {"vector": vector, "limit": 1, "with_payload": True},
            int(conf["WATCHDOG_LLM_TIMEOUT_SEC"]),
        )
        hits = result.get("result", [])
        if hits and hits[0]["score"] >= float(conf["QDRANT_SIMILARITY_THRESHOLD"]):
            return hits[0]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError):
        pass
    return None


def qdrant_upsert(conf: dict, point_id: int, vector: list[float], payload: dict) -> None:
    url = f"{conf['QDRANT_URL']}/collections/{conf['QDRANT_COLLECTION']}/points"
    try:
        http_post_json(
            url,
            {"points": [{"id": point_id, "vector": vector, "payload": payload}]},
            int(conf["WATCHDOG_LLM_TIMEOUT_SEC"]),
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError):
        pass


def trusted_domains(conf: dict) -> list[str]:
    raw = conf.get("WATCHDOG_LLM_TRUSTED_DOMAINS", "")
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def is_trusted(url: str, domains: list[str]) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith(f".{d}") for d in domains)


def searxng_search(conf: dict, query: str) -> tuple[list[str], int]:
    """Returns (snippets from allowlisted domains, count of results dropped as untrusted)."""
    if conf.get("WATCHDOG_LLM_WEB_SEARCH") != "1":
        return [], 0
    domains = trusted_domains(conf)
    url = f"{conf['SEARXNG_URL']}/search?q={urllib.parse.quote(query)}&format=json"
    try:
        result = http_get_json(url, int(conf["WATCHDOG_LLM_TIMEOUT_SEC"]))
        snippets = []
        dropped = 0
        for item in result.get("results", []):
            link = item.get("url", "")
            if not domains or not is_trusted(link, domains):
                dropped += 1
                continue
            snippets.append(f"- {item.get('title', '')}: {item.get('content', '')} ({link})")
            if len(snippets) >= 5:
                break
        return snippets, dropped
    except (urllib.error.URLError, KeyError, json.JSONDecodeError):
        return [], 0


def ollama_diagnose(conf: dict, problem: str, search_context: list[str]) -> str:
    context = "\n".join(search_context) if search_context else "No web search results."
    prompt = (
        "You are an assistant diagnosing a home server / Home Assistant / "
        "Node-RED / Frigate monitoring alert. Given the problem below and any "
        "web search context, write a short markdown runbook with this exact "
        "structure: a one-line description, a 'First checks' bullet list, and "
        "a 'Likely fix' section with concrete commands if applicable. Be concise.\n\n"
        f"Problem: {problem}\n\nWeb search context:\n{context}\n"
    )
    result = http_post_json(
        f"{conf['OLLAMA_URL']}/api/generate",
        {"model": conf["OLLAMA_MODEL"], "prompt": prompt, "stream": False},
        int(conf["WATCHDOG_LLM_TIMEOUT_SEC"]),
    )
    return result.get("response", "").strip()


def write_runbook(problem: str, body: str) -> Path:
    slug = slugify(problem)
    path = RUNBOOKS_DIR / f"{slug}.md"
    if path.exists():
        return path
    title = problem if problem[:1].isupper() else problem.capitalize()
    content = (
        f"# {title}\n\n"
        f"_AI-generated draft runbook — review before relying on it._\n\n"
        f"{body}\n"
    )
    path.write_text(content)
    return path


def main() -> int:
    conf = load_conf(CONF)
    if conf.get("WATCHDOG_LLM_ENABLED") != "1":
        print("AI diagnose disabled (WATCHDOG_LLM_ENABLED != 1). Skipping.")
        return 0

    if not HINTS_JSON.exists():
        print(f"No action-hints.json found at {HINTS_JSON}. Skipping.")
        return 0

    hints = json.loads(HINTS_JSON.read_text(errors="replace"))
    unmatched = hints.get("unmatched", [])

    RUNBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = REPORTS_DIR / f"watchdog-ai-diagnose-{stamp}.md"
    report_lines = ["# AI Watchdog AI Diagnose v1", "", f"Date: {datetime.now()}", ""]

    if not unmatched:
        report_lines.append("No unmatched problems found; nothing to diagnose.")
    else:
        for problem in unmatched:
            report_lines.append(f"## {problem}")
            vector = ollama_embed(conf, problem)
            reused = qdrant_search(conf, vector) if vector else None

            if reused:
                payload = reused.get("payload", {})
                report_lines.append(
                    f"Reused past incident (score {reused['score']:.3f}): "
                    f"`{payload.get('runbook', 'unknown')}`"
                )
                report_lines.append("")
                continue

            search_context, dropped = searxng_search(conf, problem)
            if dropped:
                report_lines.append(f"Dropped {dropped} untrusted-domain search result(s).")
            if search_context:
                report_lines.append(f"Used {len(search_context)} trusted-source result(s):")
                report_lines.extend(search_context)
            else:
                report_lines.append("No trusted-source search results; diagnosing from problem text alone.")

            try:
                body = ollama_diagnose(conf, problem, search_context)
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
                report_lines.append(f"Diagnosis failed: {e!r}")
                report_lines.append("")
                continue

            runbook_path = write_runbook(problem, body)
            report_lines.append(f"Draft runbook written: `{runbook_path}`")
            report_lines.append("")

            if vector:
                qdrant_upsert(
                    conf,
                    point_id=abs(hash(problem)) % (2**63),
                    vector=vector,
                    payload={
                        "problem": problem,
                        "runbook": str(runbook_path),
                        "created": datetime.now().isoformat(timespec="seconds"),
                    },
                )

    report_path.write_text("\n".join(report_lines) + "\n")
    print(f"AI diagnose report saved to: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
