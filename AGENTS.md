# AGENTS.md

Guidance for AI coding agents working in this repository. Human contributors may
find it useful too. (This is the `AGENTS.md` convention that agentic dev tools
read automatically.)

## What this project is

A **local, self-hosted observability stack** for Claude Code token usage. Claude
Code exports OpenTelemetry metrics; this repo runs an OTel Collector, Prometheus,
and Grafana on `localhost` and ships a provisioned dashboard. There is **no
application code** — it's Docker Compose plus config and dashboard JSON.

The dashboard **leads with tokens**. Cost is secondary and is API-equivalent
pricing, not a real bill — keep that framing in any copy you write.

## Repository layout

```
docker-compose.yml                         # the whole stack; all ports bound to 127.0.0.1
otel-collector-config.yaml                 # OTLP gRPC :4317 in, Prometheus :8889 out
prometheus/prometheus.yml                  # scrapes the collector every 15s
grafana/provisioning/datasources/          # auto-wires the Prometheus datasource
grafana/provisioning/dashboards/           # tells Grafana to load dashboards from disk
grafana/dashboards/claude-code-usage.json  # THE dashboard (checked in, source of truth)
claude-env.sh                              # source to set Claude Code's telemetry env vars
```

## How to validate changes (no live daemon required)

Run these before committing. They catch most breakage without needing Docker up:

```bash
# Dashboard JSON must parse
python3 -c "import json; json.load(open('grafana/dashboards/claude-code-usage.json'))"

# Compose file must be structurally valid
docker compose config --quiet

# All YAML must parse
python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in \
  ['otel-collector-config.yaml','prometheus/prometheus.yml'] + \
  glob.glob('grafana/provisioning/**/*.yml', recursive=True)]"
```

Full end-to-end test (needs Docker running):

```bash
docker compose up -d
source ./claude-env.sh && claude          # generate some traffic
curl -s http://localhost:8889/metrics | grep claude_code   # confirm metrics arrived
# then check panels at http://localhost:3000 (admin/admin)
```

## Non-obvious constraints — do not regress these

1. **Temporality must be cumulative.** Claude Code defaults to *delta*
   temporality, which Prometheus mishandles. `claude-env.sh` sets
   `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`. If you touch
   the env script, keep that line — without it, counters break.

2. **Keep the PII `labeldrop` rule in `prometheus/prometheus.yml`.** Claude Code
   attaches `user_email`, `user_id`, `user_account_id`, `user_account_uuid`, and
   `organization_id` to every metric as **data-point** attributes (not resource
   attributes — so `resource_to_telemetry_conversion` does NOT strip them; it's
   left `false` only because we don't need resource attrs as labels). The
   `metric_relabel_configs` → `labeldrop` rule removes them at scrape time.
   **Do not drop `session_id`** — it distinguishes each session's cumulative
   counters; removing it collides separate sessions into one series and breaks
   `increase()`. The dashboard only needs `type`, `model`, `query_source`.

3. **Metric names have no `_total`/unit suffix.** The Prometheus exporter is set
   with `add_metric_suffixes: false`, so `claude_code.token.usage` becomes
   `claude_code_token_usage` (dots → underscores, nothing else). PromQL in the
   dashboard depends on these exact names.

4. **Everything binds to `127.0.0.1` only.** Keep it that way. Do not change port
   mappings to `0.0.0.0` or expose services to the network — this is a
   local-only tool and `admin/admin` Grafana creds assume that.

5. **All metrics are counters; query with `increase(...)`.** Panels use
   `increase(metric[$__rate_interval])` (or `[$__range]` for totals), which
   handles counter resets when Claude Code restarts. Don't switch to raw values.

## Metric reference

| Prometheus metric                 | Unit    | Key attributes                                         |
| --------------------------------- | ------- | ------------------------------------------------------ |
| `claude_code_token_usage`         | tokens  | `type` (input/output/cacheRead/cacheCreation), `model`, `query_source`, `speed`, `effort` |
| `claude_code_cost_usage`          | USD     | `model`, `query_source`, `speed`, `effort`             |
| `claude_code_session_count`       | count   | `start_type` (fresh/resume/continue/agents_view)       |
| `claude_code_active_time_total`   | seconds | `type` (user/cli)                                      |
| `claude_code_lines_of_code_count` | count   | `type` (added/removed)                                 |
| `claude_code_commit_count`        | count   | —                                                      |
| `claude_code_pull_request_count`  | count   | —                                                      |

- `query_source` (always present): `main`, `subagent`, `auxiliary`.
- `speed` and `effort` are conditional — omitted when not applicable. Don't
  assume every series carries them.

## Editing the dashboard

- Edit `grafana/dashboards/claude-code-usage.json` directly, or edit in the
  Grafana UI and export the JSON back over that file (the provider has
  `allowUiUpdates: true`, but UI edits are wiped by `docker compose down -v`
  unless exported and committed).
- Keep the datasource UID as `prometheus` (matches the provisioned datasource).
- Preserve the two template variables `$model` and `$query_source`; several
  panels filter on them.
- After editing, re-run the JSON parse check above.

## Conventions

- Pin image versions in `docker-compose.yml` (no `:latest`).
- Keep config files commented — they are the product here; explain *why*, not
  just *what*.
- Don't add cloud exporters, remote endpoints, or telemetry that leaves the
  machine. The entire value proposition is "stays local."
