#!/usr/bin/env python3
"""AI Watchdog News Brief v1.

Pulls current world/USA headlines via the local SearXNG instance,
restricted to an allowlist of wire-service domains (config/
watchdog_news_brief.conf). Domain filtering happens before any result is
written out — there is no algorithmic "is this reliable" judgment call,
just an allowlist, same approach as the AI-diagnose web search step.
"""
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(os.environ.get("WATCHDOG_BASE", str(Path.home() / "ai-watchdog")))
CONF = BASE / "config" / "watchdog_news_brief.conf"
PUBLIC = BASE / "public"
REPORTS = BASE / "reports"


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


def http_get_json(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def trusted_domains(conf: dict) -> list[str]:
    raw = conf.get("WATCHDOG_NEWS_TRUSTED_DOMAINS", "")
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def is_trusted(url: str, domains: list[str]) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith(f".{d}") for d in domains)


def search_headlines(conf: dict, query: str, domains: list[str], max_results: int) -> tuple[list[dict], int]:
    url = f"{conf['SEARXNG_URL']}/search?q={urllib.parse.quote(query)}&format=json&categories=news"
    try:
        result = http_get_json(url, int(conf["WATCHDOG_NEWS_TIMEOUT_SEC"]))
    except (urllib.error.URLError, json.JSONDecodeError):
        return [], 0

    headlines = []
    dropped = 0
    for item in result.get("results", []):
        link = item.get("url", "")
        if not domains or not is_trusted(link, domains):
            dropped += 1
            continue
        headlines.append({
            "title": item.get("title", ""),
            "content": item.get("content", ""),
            "url": link,
            "source": urllib.parse.urlparse(link).netloc,
        })
        if len(headlines) >= max_results:
            break
    return headlines, dropped


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def main() -> int:
    conf = load_conf(CONF)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    updated = datetime.now().astimezone().isoformat(timespec="seconds")

    if conf.get("WATCHDOG_NEWS_ENABLED") != "1":
        print("News brief disabled (WATCHDOG_NEWS_ENABLED != 1). Skipping.")
        return 0

    domains = trusted_domains(conf)
    queries = [q.strip() for q in conf.get("WATCHDOG_NEWS_QUERIES", "").split(",") if q.strip()]
    max_per_query = int(conf.get("WATCHDOG_NEWS_MAX_PER_QUERY", "6"))

    sections = []
    total_dropped = 0
    for query in queries:
        headlines, dropped = search_headlines(conf, query, domains, max_per_query)
        total_dropped += dropped
        sections.append({"query": query, "headlines": headlines, "dropped": dropped})

    data = {
        "updated": updated,
        "trusted_domains": domains,
        "sections": sections,
        "total_dropped": total_dropped,
    }
    (PUBLIC / "news.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report = REPORTS / f"watchdog-news-{stamp}.md"
    lines = ["# AI Watchdog News Brief", "", f"Date: {updated}", ""]
    for section in sections:
        lines.append(f"## {section['query']}")
        lines.append("")
        if section["headlines"]:
            for h in section["headlines"]:
                lines.append(f"- [{h['title']}]({h['url']}) — {h['source']}")
        else:
            lines.append("No trusted-source headlines found.")
        lines.append(f"_Dropped {section['dropped']} untrusted-domain result(s)._")
        lines.append("")
    report.write_text("\n".join(lines) + "\n")

    section_html = ""
    for section in sections:
        if section["headlines"]:
            items = "".join(
                f'<li><a href="{esc(h["url"])}">{esc(h["title"])}</a>'
                f'<div class="muted">{esc(h["source"])} — {esc(h["content"])}</div></li>'
                for h in section["headlines"]
            )
            body = f"<ul>{items}</ul>"
        else:
            body = "<p>No trusted-source headlines found.</p>"
        section_html += f"""
    <div class="card">
      <h2>{esc(section['query'])}</h2>
      {body}
      <p class="muted">Dropped {section['dropped']} untrusted-domain result(s).</p>
    </div>"""

    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="1800">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Watchdog News Brief</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f1115;
      --card: #171a21;
      --text: #e8e8e8;
      --muted: #a8b0bf;
      --line: #2a2f3a;
      --link: #8ab4ff;
    }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      line-height: 1.45;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .muted {{ color: var(--muted); font-size: .85rem; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin-bottom: 14px;
    }}
    ul {{ padding-left: 1.1rem; }}
    li {{ margin-bottom: 10px; }}
  </style>
</head>
<body>
  <h1>AI Watchdog News Brief</h1>
  <div class="muted">Updated: {esc(updated)} · Sources: {esc(", ".join(domains))}</div>
  <p>
    <a href="dashboard.html">Dashboard</a> ·
    <a href="brief.html">Morning Brief</a> ·
    <a href="news.json">News JSON</a>
  </p>
  {section_html}
</body>
</html>
"""
    (PUBLIC / "news.html").write_text(html_doc)

    print(f"News report saved to: {report}")
    print(f"News page written to: {PUBLIC / 'news.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
