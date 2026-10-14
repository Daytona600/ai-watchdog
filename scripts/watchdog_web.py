#!/usr/bin/env python3
"""AI Watchdog web server: serves public/ on port 8098 plus the AI fix-page buttons.

Replaces `python3 -m http.server 8098` (ai-watchdog-web.service): every GET is the
same static file serving as before. Two POST endpoints back the buttons on
public/fixes/<id>.html (pages written by watchdog_ai_fix.py):

  /api/fix-start      create an OpenCode session on the EPYC box (agent "fixer"),
                      send it the analysis, redirect the browser to that session
  /api/fix-reanalyse  queue a re-analysis with the user's note, back to the page

The house reaches this through Caddy's LAN-only /watchdog/ route (or directly on
the LAN). POSTs are refused unless they come from one of those pages (Origin /
Referer check), so another website open in the same browser cannot press the
buttons. Changes still need approval inside OpenCode; starting a session only
lets it investigate.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(os.environ.get("WATCHDOG_BASE", str(Path.home() / "ai-watchdog")))
sys.path.insert(0, str(BASE / "scripts"))
import watchdog_ai_fix as fix  # noqa: E402

PORT = 8098
OPENCODE_ENV = BASE / "config" / "opencode_web.env"
ALLOWED_ORIGINS = {"ha.magnumz.com", f"{fix.watchdog_hosts.get('MAIN_SERVER_IP')}:{PORT}",
                   f"localhost:{PORT}", f"127.0.0.1:{PORT}"}


def opencode_call(method, path, body=None):
    pw = fix.load_env(OPENCODE_ENV).get("OPENCODE_SERVER_PASSWORD", "")
    host = f"http://{fix.watchdog_hosts.get('EPYC_LLM_IP')}:{fix.conf('AI_FIX_OPENCODE_PORT', '4096')}"
    directory = fix.conf("AI_FIX_OPENCODE_DIR", "/home/davids1/fix-sessions")
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(
        f"{host}{path}{sep}directory={urllib.parse.quote(directory)}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(f"opencode:{pw}".encode()).decode()})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def session_url(session_id):
    directory = fix.conf("AI_FIX_OPENCODE_DIR", "/home/davids1/fix-sessions")
    slug = base64.urlsafe_b64encode(directory.encode()).decode().rstrip("=")  # what OpenCode's web app uses
    return f"http://{fix.watchdog_hosts.get('EPYC_LLM_IP')}:{fix.conf('AI_FIX_OPENCODE_PORT', '4096')}/{slug}/session/{session_id}"


def fixer_prompt(doc):
    item, latest = doc["item"], doc.get("latest") or {}
    a = latest.get("analysis") or {}
    lines = [f"AI Watchdog problem: {item['name']} ({item['severity']}, from the {item['source']}).",
             f"Check output: {item['detail']}"]
    if item.get("type") and item.get("target"):
        lines.append(f"The check: type {item['type']}, target: {item['target']}")
    if a:
        lines += ["", f"First-pass analysis ({latest.get('model')}, confidence {a['confidence']}):",
                  f"Summary: {a['summary']}", f"Likely cause: {a['likely_cause']}"]
        if a.get("needs_human"):
            lines.append(f"Needs a person: {a['needs_human']}")
        lines.append("Proposed steps:")
        for i, s in enumerate(a.get("steps") or [], 1):
            lines.append(f"  {i}. [{s['kind']}] ({s['host']}) {s['why']}\n     {s['command']}")
        if a.get("verify"):
            lines.append(f"Verify: {a['verify']}")
        if a.get("rollback"):
            lines.append(f"Undo: {a['rollback']}")
    else:
        lines.append(f"There is no first-pass analysis ({latest.get('error') or 'not run yet'}); start from the evidence.")
    if latest.get("note"):
        lines.append(f"David's note on the analysis: {latest['note']}")
    for title, text in latest.get("evidence") or []:
        lines += ["", f"--- evidence: {title}", text[:2500]]
    lines += ["",
              "Start by checking whether this diagnosis is right with remote_inspect / read_remote_file (no approval needed), "
              "then follow your instructions: say what you found, propose the fix, make changes one step at a time "
              "(each bash command asks David), and verify.",
              f"To re-run this watchdog check afterwards: ssh apex-agent-server 'python3 ~/ai-watchdog/scripts/watchdog_ai_fix.py --recheck {doc['id']}'"]
    return "\n".join(lines)


class Handler(SimpleHTTPRequestHandler):
    def _page(self, code, title, message):
        body = (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
                f"<title>{fix.esc(title)}</title><style>{fix.CSS}</style></head><body><h1>{fix.esc(title)}</h1>"
                f"<div class='card'>{message}</div></body></html>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/fix-start", "/api/fix-reanalyse"):
            return self.send_error(404)
        source = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if urllib.parse.urlparse(source).netloc not in ALLOWED_ORIGINS:
            return self._page(403, "Refused", "Buttons only work from the watchdog's own pages.")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 20000:
            return self.send_error(413)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        fid = (form.get("id") or [""])[0]
        doc = fix.read_json(fix.FIXES / f"{fid}.json", None) if re.fullmatch(r"[a-z0-9-]{1,80}", fid) else None
        if not doc:
            return self._page(404, "Not found", "That analysis no longer exists.")

        if path == "/api/fix-reanalyse":
            note = (form.get("note") or [""])[0].strip()[:1500]
            if not note:
                return self._redirect(f"../fixes/{fid}.html")
            fix.write_json(fix.REQUESTS / f"{fid}.json", {"note": note, "requested_at": fix.now_iso()})
            fix.render_page(fid)
            log = open(BASE / "logs" / "ai-fix-reanalyse.log", "a")
            subprocess.Popen([sys.executable, str(BASE / "scripts" / "watchdog_ai_fix.py"), "--reanalyse", fid],
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(BASE))
            return self._redirect(f"../fixes/{fid}.html")

        try:
            session = opencode_call("POST", "/session", {"title": f"Fix: {doc['item']['name']}"[:120]})
            opencode_call("POST", f"/session/{session['id']}/prompt_async",
                          {"agent": fix.conf("AI_FIX_OPENCODE_AGENT", "fixer"),
                           "parts": [{"type": "text", "text": fixer_prompt(doc)}]})
        except Exception as e:
            return self._page(502, "Could not start the fix session",
                              f"OpenCode on the EPYC box did not answer ({fix.esc(type(e).__name__)}: {fix.esc(e)}). "
                              "Check <code>systemctl status opencode-web</code> there, then try again.")
        url = session_url(session["id"])
        sessions = fix.read_json(fix.sessions_file(fid), [])
        fix.write_json(fix.sessions_file(fid), sessions + [{"id": session["id"], "url": url, "started": fix.now_iso()}])
        fix.render_page(fid)
        return self._redirect(url)


def main():
    handler = partial(Handler, directory=str(BASE / "public"))
    ThreadingHTTPServer(("0.0.0.0", PORT), handler).serve_forever()


if __name__ == "__main__":
    main()
