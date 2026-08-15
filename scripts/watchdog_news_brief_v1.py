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


def engine_bangs(conf: dict, key: str) -> str:
    # SearXNG has no "exclude engine" request param, only per-request engine
    # *selection* via query-embedded bangs (e.g. "!duckduckgo !brave ..."),
    # which replaces the default engine set for that request only — no
    # change to SearXNG's global settings.
    raw = conf.get(key, "")
    names = [e.strip() for e in raw.split(",") if e.strip()]
    # Bang tokens can't contain a literal space (the query tokenizer would
    # split "!bing news" into two tokens); SearXNG's bang parser converts
    # underscores back to spaces before matching against the engine name.
    return "".join(f"!{name.replace(' ', '_')} " for name in names)


def local_resources(conf: dict) -> list[dict]:
    # Static links, not search results — a food-pantry schedule PDF's
    # filename embeds a revision date and moves when the org updates it, so
    # this points at the stable page that always has the current one rather
    # than a link that will eventually 404.
    raw = conf.get("WATCHDOG_LOCAL_RESOURCES", "")
    items = []
    for part in raw.split(","):
        part = part.strip()
        if not part or "|" not in part:
            continue
        label, _, url = part.partition("|")
        items.append({"label": label.strip(), "url": url.strip()})
    return items


def is_trusted(url: str, domains: list[str]) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == d or host.endswith(f".{d}") for d in domains)


def search_headlines(
    conf: dict, query: str, domains: list[str], max_results: int, engines_key: str
) -> tuple[list[dict], int]:
    # Bake the allowlist into the query itself (site:a.com OR site:b.com ...)
    # rather than relying on generic trending results to happen to include a
    # wire service. The post-filter below still applies as a safety net in
    # case the search engine ignores/loosens the site: filter.
    if domains:
        site_filter = " OR ".join(f"site:{d}" for d in domains)
        query = f"({site_filter}) {query}"
    query = engine_bangs(conf, engines_key) + query
    url = f"{conf['SEARXNG_URL']}/search?q={urllib.parse.quote(query)}&format=json"
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
        headlines, dropped = search_headlines(conf, query, domains, max_per_query, "WATCHDOG_NEWS_ENGINES")
        total_dropped += dropped
        sections.append({"query": query, "headlines": headlines, "dropped": dropped})

    local_enabled = conf.get("WATCHDOG_LOCAL_NEWS_ENABLED") == "1"
    local_label = conf.get("WATCHDOG_LOCAL_NEWS_LABEL", "Local")
    local_domains = [d.strip().lower() for d in conf.get("WATCHDOG_LOCAL_NEWS_TRUSTED_DOMAINS", "").split(",") if d.strip()]
    local_queries = [q.strip() for q in conf.get("WATCHDOG_LOCAL_NEWS_QUERIES", "").split(",") if q.strip()]
    local_max = int(conf.get("WATCHDOG_LOCAL_NEWS_MAX_PER_QUERY", "6"))
    resources = local_resources(conf)

    local_sections = []
    if local_enabled:
        for query in local_queries:
            headlines, dropped = search_headlines(
                conf, query, local_domains, local_max, "WATCHDOG_LOCAL_NEWS_ENGINES"
            )
            total_dropped += dropped
            local_sections.append({"query": query, "headlines": headlines, "dropped": dropped})

    data = {
        "updated": updated,
        "trusted_domains": domains,
        "sections": sections,
        "total_dropped": total_dropped,
        "local": {
            "label": local_label,
            "trusted_domains": local_domains,
            "sections": local_sections,
            "resources": resources,
        },
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

    if local_enabled:
        lines.append(f"# Local — {local_label}")
        lines.append("")
        for section in local_sections:
            lines.append(f"## {section['query']}")
            lines.append("")
            if section["headlines"]:
                for h in section["headlines"]:
                    lines.append(f"- [{h['title']}]({h['url']}) — {h['source']}")
            else:
                lines.append("No trusted-source headlines found.")
            lines.append(f"_Dropped {section['dropped']} untrusted-domain result(s)._")
            lines.append("")
        if resources:
            lines.append("## Resources")
            lines.append("")
            for r in resources:
                lines.append(f"- [{r['label']}]({r['url']})")
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

    local_section_html = ""
    if local_enabled:
        for section in local_sections:
            if section["headlines"]:
                items = "".join(
                    f'<li><a href="{esc(h["url"])}">{esc(h["title"])}</a>'
                    f'<div class="muted">{esc(h["source"])} — {esc(h["content"])}</div></li>'
                    for h in section["headlines"]
                )
                body = f"<ul>{items}</ul>"
            else:
                body = "<p>No trusted-source headlines found.</p>"
            local_section_html += f"""
    <div class="card">
      <h2>{esc(section['query'])}</h2>
      {body}
      <p class="muted">Dropped {section['dropped']} untrusted-domain result(s).</p>
    </div>"""
        if resources:
            res_items = "".join(
                f'<li><a href="{esc(r["url"])}">{esc(r["label"])}</a></li>' for r in resources
            )
            local_section_html += f"""
    <div class="card">
      <h2>Resources</h2>
      <ul>{res_items}</ul>
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
  {f'<h1 style="margin-top:28px">Local — {esc(local_label)}</h1>' if local_enabled else ''}
  {local_section_html}
</body>
</html>
"""
    (PUBLIC / "news.html").write_text(html_doc)

    print(f"News report saved to: {report}")
    print(f"News page written to: {PUBLIC / 'news.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
