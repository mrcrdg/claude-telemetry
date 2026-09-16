#!/usr/bin/env python3
"""One-shot: add the $session title dropdown + a session-directory table to the
Claude Code usage dashboard. Idempotent — safe to re-run."""
import json

PATH = "grafana/dashboards/claude-code-usage.json"

SESSION_VAR = {
    "current": {"selected": False, "text": "All", "value": "$__all"},
    "datasource": {"type": "prometheus", "uid": "prometheus"},
    "definition": "query_result(claude_session_info)",
    "includeAll": True,
    "label": "Session (chat title)",
    "multi": True,
    "name": "session",
    "options": [],
    "query": {
        "qryType": 3,
        "query": "claude_session_info",
        "refId": "PrometheusVariableQueryEditor-VariableQuery",
    },
    "refresh": 1,
    # Grafana named capture groups: value = the id we filter on, text = shown label.
    "regex": r'/session_id="(?<value>[^"]+)".*title="(?<text>[^"]+)"/',
    "sort": 1,
    "type": "query",
    "allValue": ".*",
}

DIRECTORY_PANEL = {
    "id": 112,
    "type": "table",
    "title": "Session directory  ·  which UUID is which chat",
    "description": "Maps each session_id to its Claude Code chat title (read "
    "from transcripts by the session-labeler sidecar). Use the "
    "'Session (chat title)' filter at the top to jump to yours.",
    "datasource": {"type": "prometheus", "uid": "prometheus"},
    "gridPos": {"h": 8, "w": 24, "x": 0, "y": 33},
    "targets": [
        {
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "editorMode": "code",
            "expr": 'claude_session_info{session_id=~"$session"}',
            "format": "table",
            "instant": True,
            "range": False,
            "refId": "A",
            "legendFormat": "__auto",
        }
    ],
    "transformations": [
        {
            "id": "organize",
            "options": {
                "excludeByName": {
                    "Time": True,
                    "Value": True,
                    "__name__": True,
                    "job": True,
                    "instance": True,
                },
                "indexByName": {
                    "title": 0,
                    "started": 1,
                    "project": 2,
                    "session_id": 3,
                },
                "renameByName": {
                    "title": "Chat",
                    "started": "Started",
                    "project": "Project",
                    "session_id": "Session id",
                },
            },
        }
    ],
    "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": []},
    "options": {
        "showHeader": True,
        "sortBy": [{"displayName": "Started", "desc": True}],
    },
}


def main():
    d = json.load(open(PATH))

    tvars = d["templating"]["list"]
    if not any(v.get("name") == "session" for v in tvars):
        tvars.append(SESSION_VAR)

    panels = d["panels"]
    if not any(p.get("id") == 112 for p in panels):
        idx = next(
            i for i, p in enumerate(panels)
            if p.get("title") == "Per-session efficiency" and p.get("type") == "table"
        )
        panels.insert(idx + 1, DIRECTORY_PANEL)

    json.dump(d, open(PATH, "w"), indent=2, ensure_ascii=False)
    open(PATH, "a").write("\n")
    print("dashboard updated: %d vars, %d panels" % (len(tvars), len(panels)))


if __name__ == "__main__":
    main()
