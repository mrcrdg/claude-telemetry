#!/usr/bin/env python3
"""Emit a claude_session_info gauge so the dashboard can label opaque
session_id UUIDs with the chat's human title.

Claude Code writes one transcript per session at
  ~/.claude/projects/<slug>/<session_id>.jsonl
Each transcript carries an `ai-title` line (the chat name shown in the UI) and,
on most lines, a `timestamp` and `cwd`. We scan every transcript, pull those
out, and expose one metric per session:

  claude_session_info{session_id, title, started, project} 1

Prometheus scrapes it like any other target. In Grafana the token panels then
join on session_id to show the title instead of the raw UUID.

No third-party deps — stdlib http.server only, so the image stays tiny.
Titles/first-prompts become label values (visible to anyone with dashboard
access); acceptable because the whole stack is bound to localhost.
"""
import glob
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PROJECTS_DIR = os.environ.get("CLAUDE_PROJECTS_DIR", "/data/projects")
PORT = int(os.environ.get("LABELER_PORT", "9105"))
REFRESH_SECONDS = int(os.environ.get("LABELER_REFRESH_SECONDS", "60"))
TITLE_MAX = 80

# Rebuilt every REFRESH_SECONDS by refresh(); served as-is by the HTTP handler.
_metrics_text = "# no scan yet\n"


def escape_label(value):
    """Prometheus text-format label-value escaping: backslash, quote, newline."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def first_user_message(obj):
    """Fallback title: the first line of the first real user prompt."""
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    else:
        return None
    text = text.strip()
    return text.split("\n", 1)[0] if text else None


def scan_transcript(path):
    """Return (session_id, title, started, project) or None if unusable."""
    session_id = None
    title = None
    fallback_title = None
    started = None
    cwd = None
    for line in _read_lines(path):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = session_id or obj.get("sessionId") or obj.get("session_id")
        if obj.get("type") == "ai-title" and obj.get("aiTitle"):
            title = obj["aiTitle"]
        if started is None and obj.get("timestamp"):
            started = obj["timestamp"]
        if cwd is None and obj.get("cwd"):
            cwd = obj["cwd"]
        if fallback_title is None and obj.get("type") == "user":
            fallback_title = first_user_message(obj)
    if not session_id:
        # Last resort: the filename is the session UUID.
        session_id = os.path.splitext(os.path.basename(path))[0]
    title = (title or fallback_title or "(untitled)")[:TITLE_MAX]
    started = (started or "")[:16]  # "2026-07-27T10:45"
    project = os.path.basename(cwd) if cwd else ""
    return session_id, title, started, project


def _read_lines(path):
    with open(path, "r", errors="replace") as fh:
        return fh.readlines()


def build_metrics():
    lines = [
        "# HELP claude_session_info Human title for each Claude Code session_id.",
        "# TYPE claude_session_info gauge",
    ]
    seen = set()
    pattern = os.path.join(PROJECTS_DIR, "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        try:
            session_id, title, started, project = scan_transcript(path)
        except OSError:
            continue
        if session_id in seen:
            continue
        seen.add(session_id)
        lines.append(
            'claude_session_info{{session_id="{sid}",title="{title}",'
            'started="{started}",project="{project}"}} 1'.format(
                sid=escape_label(session_id),
                title=escape_label(title),
                started=escape_label(started),
                project=escape_label(project),
            )
        )
    lines.append("")
    return "\n".join(lines)


def refresh():
    global _metrics_text
    _metrics_text = build_metrics()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/"):
            self.send_response(404)
            self.end_headers()
            return
        body = _metrics_text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # quiet — no per-request stderr spam


def main():
    refresh()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.timeout = 1
    print("session-labeler on :%d, scanning %s every %ds"
          % (PORT, PROJECTS_DIR, REFRESH_SECONDS), flush=True)
    next_scan = time.monotonic() + REFRESH_SECONDS
    while True:
        server.handle_request()  # returns after `timeout` if idle
        if time.monotonic() >= next_scan:
            refresh()
            next_scan = time.monotonic() + REFRESH_SECONDS


if __name__ == "__main__":
    main()
