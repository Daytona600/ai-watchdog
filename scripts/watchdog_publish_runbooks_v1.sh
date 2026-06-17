#!/usr/bin/env bash
set -u

BASE="$HOME/ai-watchdog"
SRC="$BASE/runbooks"
DST="$BASE/public/runbooks"

mkdir -p "$DST"

python3 - "$SRC" "$DST" <<'PY'
from pathlib import Path
from datetime import datetime
import html
import re
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
dst.mkdir(parents=True, exist_ok=True)

def title_from_md(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback

def render_inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text

def render_markdown_simple(text: str) -> str:
    out = []
    in_code = False
    code_lines = []
    in_ul = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in text.splitlines():
        line = raw.rstrip()

        if line.startswith("```"):
            if not in_code:
                close_ul()
                in_code = True
                code_lines = []
            else:
                out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                in_code = False
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not line.strip():
            close_ul()
            continue

        if line.startswith("# "):
            close_ul()
            out.append(f"<h1>{render_inline(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            close_ul()
            out.append(f"<h2>{render_inline(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            close_ul()
            out.append(f"<h3>{render_inline(line[4:].strip())}</h3>")
        elif line.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{render_inline(line[2:].strip())}</li>")
        else:
            close_ul()
            out.append(f"<p>{render_inline(line)}</p>")

    close_ul()
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")

    return "\n".join(out)

def page(title: str, body: str) -> str:
    updated = datetime.now().astimezone().isoformat(timespec="seconds")
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
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
      line-height: 1.5;
      max-width: 980px;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .top {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px;
      margin-bottom: 14px;
    }}
    .muted {{ color: var(--muted); font-size: .9rem; }}
    code {{
      background: #10131a;
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 1px 4px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #10131a;
      border: 1px solid var(--line);
      padding: 12px;
      border-radius: 10px;
      overflow-x: auto;
    }}
    h1 {{ margin-top: 0; }}
  </style>
</head>
<body>
  <div class="top">
    <a href="../dashboard.html">← Watchdog Dashboard</a> ·
    <a href="index.html">Runbook Index</a> ·
    <a href="../latest.html">Full Report</a>
    <div class="muted">Generated: {html.escape(updated)}</div>
  </div>
  {body}
</body>
</html>
"""

items = []

if not src.exists():
    raise SystemExit(f"Runbook source folder not found: {src}")

for md in sorted(src.glob("*.md")):
    text = md.read_text(errors="replace")
    title = title_from_md(text, md.stem)
    html_name = md.stem + ".html"
    rendered = render_markdown_simple(text)

    (dst / md.name).write_text(text)
    (dst / html_name).write_text(page(title, rendered))

    if md.name != "index.md":
        items.append((title, html_name))

index_body = ["<h1>AI Watchdog Recovery Runbooks</h1>", "<ul>"]
for title, html_name in sorted(items):
    index_body.append(f'<li><a href="{html.escape(html_name)}">{html.escape(title)}</a></li>')
index_body.append("</ul>")

(dst / "index.html").write_text(page("AI Watchdog Recovery Runbooks", "\n".join(index_body)))

print(f"Runbooks published to: {dst}")
print(f"Runbook count: {len(items)}")
PY
