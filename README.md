# claude-telemetry

Local observability for **Claude Code token usage**. Claude Code exports
OpenTelemetry metrics to a self-hosted stack running entirely on `localhost` —
no cloud services, no external egress — and you query them in Grafana to track
token consumption over time, per model and per query source.

The dashboard **leads with tokens**. Cost is shown too, but on a Claude
subscription `claude_code.cost.usage` is *API-equivalent* pricing, **not your
actual bill** — treat it as a relative signal, not a dollar figure.

![Claude Code token usage dashboard in Grafana](imgs/grafana-dashboard-1.png)

```
Claude Code ──OTLP gRPC :4317──▶ OTel Collector ──:8889──▶ Prometheus ──PromQL──▶ Grafana
```

## Components

| Service        | Port (localhost) | Purpose                                    |
| -------------- | ---------------- | ------------------------------------------ |
| OTel Collector | 4317             | Receives OTLP/gRPC metrics from Claude Code |
| OTel Collector | 8889             | Prometheus scrape endpoint                  |
| Prometheus     | 9090             | Stores metrics (90-day retention)           |
| Grafana        | 3000             | Dashboards (admin / admin)                  |

## Quick start

1. **Start the stack** (Docker must be running):

   ```bash
   docker compose up -d
   ```

2. **Point Claude Code at the collector**, then launch it. Run this once per
   terminal, from any directory — the argument is the project name that shows
   up on the dashboard:

   ```bash
   source ~/path/to/claude-telemetry/claude-env.sh my-project
   claude
   ```

   Omit the name and the current directory name is used. It must be `source`d,
   not executed — a script run normally sets variables in its own process,
   which exits immediately and leaves your shell untouched.

   This sets:

   ```bash
   CLAUDE_CODE_ENABLE_TELEMETRY=1
   OTEL_METRICS_EXPORTER=otlp
   OTEL_EXPORTER_OTLP_PROTOCOL=grpc
   OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
   OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
   OTEL_METRIC_EXPORT_INTERVAL=10000   # 10s while testing; default is 60s
   ```

3. **Use Claude Code as normal**, then open the dashboard:

   <http://localhost:3000> → *Claude Code* folder → **Claude Code — Token Usage**

   Metrics appear within one or two export intervals (10–20s with the snippet
   above). If panels are empty, give it a moment for the first export.

## Dashboard panels

**Overview** — total tokens, cost, sessions, active time (stat tiles).

**Efficiency scorecard** — six colour-coded tiles that say whether a number is
good or bad, not just what it is:
- *Cache amortization* (`cacheRead/cacheCreation`) — the headline number. Green
  above 10x, red below 2x (the arithmetic break-even).
- *Cache write % of cost* — green under 30%, red over 50%. Writes cost 2x
  input (Claude Code uses the 1-hour cache TTL); reads cost 0.1x.
- *Generation intensity* (`output/cacheRead`) — green under 3%.
- *Uncached input* — green under 1%; higher means the prefix cache is being
  defeated.
- *Cost per session* and *Sessions (distinct)*.

**Where the money goes** — the token-share and cost-share pies side by side.
They never match (cacheRead is ~90% of tokens but ~30% of cost), which is the
single most important thing to understand about these metrics.

**Per-session efficiency** — one row per session with its amortization ratio,
so you can see which working styles were efficient.

Panel descriptions (hover the ⓘ) carry each metric's target range and what to
do when it drifts. The full writeup — price table, ratio derivations, threshold
provenance — is linked from the dashboard header and lives in
**[docs/METRICS-GUIDE.md](docs/METRICS-GUIDE.md)**.

**Tokens**
- *Tokens over time by type* — stacked series for `input`, `output`,
  `cacheRead`, `cacheCreation`.

**Cost & activity** (secondary)
- *Cost over time by model* — stacked cost series per model.
- *Breakdown by query_source* — cost split by `main` / `subagent` /
  `auxiliary`.
- *Sessions per day* — bar chart.
- *Cost by model & source* — sortable table.

**By project**
- *Cost over time by project* — stacked cost series per project.
- *Tokens & cost by project* — sortable table.

Three template variables at the top — **Project**, **Model** and **Query
source** — filter every panel.

## Metrics reference

Claude Code emits these metrics (names shown as they appear in Prometheus, after
`.` → `_` conversion; the collector is configured with `add_metric_suffixes:
false` so there's no `_total`/unit suffix mangling):

| Prometheus metric              | Unit    | Key attributes                                  |
| ------------------------------ | ------- | ----------------------------------------------- |
| `claude_code_token_usage`      | tokens  | `type` = input \| output \| cacheRead \| cacheCreation; also `model`, `query_source`, `speed`, `effort` |
| `claude_code_cost_usage`       | USD     | `model`, `query_source`, `speed`, `effort`      |
| `claude_code_session_count`    | count   | `start_type` = fresh \| resume \| continue \| agents_view |
| `claude_code_active_time_total`| seconds | `type` = user \| cli                            |
| `claude_code_lines_of_code_count` | count | `type` = added \| removed                       |
| `claude_code_commit_count`     | count   | —                                               |
| `claude_code_pull_request_count` | count | —                                               |
| `claude_code_token_cost_usd`   | USD     | **recording rule**, not from Claude Code — `claude_code_token_usage` priced per token type. Same labels as the source metric. |

- `query_source` (always present): `main` (your direct prompts), `subagent`
  (spawned agents), `auxiliary` (internal/background calls).
- `speed` (`fast`) and `effort` (`low`…`max`) are **conditional** — the
  attribute is omitted when it doesn't apply, so don't assume every series has
  them.
- `project` is **not** emitted by Claude Code. Neither is cwd or git branch —
  they're deliberately left out to avoid unbounded cardinality. `claude-env.sh`
  adds it via `OTEL_RESOURCE_ATTRIBUTES=project=<dir name>`, which lands on
  every metric as a data-point label. Override with `CLAUDE_TELEMETRY_PROJECT`:

  ```bash
  CLAUDE_TELEMETRY_PROJECT=my-app source ./claude-env.sh
  ```

  Only data captured *after* tagging carries the label; older series keep an
  empty `project`, and the dashboard's "All" selection still includes them.
- **`claude_code_cost_usage` has no `type` label**, so it can't answer "how much
  am I paying for cache writes vs reads". That's why
  `prometheus/rules/claude-cost.yml` re-prices the token counter per type. Both
  are API-equivalent pricing — on a subscription, neither is your bill.
- **Recording rules are forward-only.** They price whatever is being scraped at
  evaluation time, so history recorded before a rule existed gets no cost
  series. Backfill it with `promtool tsdb create-blocks-from rules`:

  ```bash
  docker exec claude-prometheus promtool tsdb create-blocks-from rules \
    --start=<RFC3339> --end=<RFC3339> --url=http://localhost:9090 \
    --output-dir=/prometheus/backfill /etc/prometheus/rules/claude-cost.yml
  # then stop Prometheus, move the blocks into /prometheus, and start it again
  ```

  ⚠️ **Set `--end` to at least a few hours in the past, never `now`.** Blocks
  that overlap Prometheus's in-memory head cause it to be truncated on restart,
  destroying recent samples that hadn't been flushed to disk yet. Backfilling to
  `now` will silently lose the last hours of raw metrics.
- **Missed sessions can be recovered.** A session launched without the OTel env
  vars exports nothing, but its transcript under `~/.claude/projects/` still
  records per-message token counts. `scripts/backfill-from-transcripts.py`
  reconstructs them into Prometheus:

  ```bash
  python3 scripts/backfill-from-transcripts.py --dry-run   # preview
  python3 scripts/backfill-from-transcripts.py -o /tmp/backfill.om
  ```

  It skips sessions Prometheus already has (no double-counting) and, by
  default, anything newer than 3 hours — see the block-overlap warning above.
  Loading instructions are in the script's docstring.
- **No latency metric exists.** Per-request duration (the "15s" the CLI shows
  while working) lives in the `claude_code.api_request` *event*, not in any
  metric — and events need a log store, which this stack doesn't run.
  `active_time_total{type="cli"}` is the nearest metric-side proxy, but it's an
  aggregate rather than a per-request timing. See
  [docs/FUTURE-IMPROVEMENTS.md](docs/FUTURE-IMPROVEMENTS.md).

**Temporality:** Claude Code exports **delta** temporality by default, but this
stack forces **cumulative** via `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`
(set in `claude-env.sh`) because Prometheus and the collector's Prometheus
exporter expect cumulative counters. The dashboard uses `increase(...)` over the
panel interval, which handles counter resets when Claude Code restarts. If your
counters look flat or wrong, this env var is the first thing to check.

## Common tasks

**Stop everything (keep data):**
```bash
docker compose down
```

**Wipe all stored metrics/dashboards state:**
```bash
docker compose down -v
```

**Turn telemetry off in your shell:**
```bash
source ./claude-env.sh --unset
```

**Add an already-running session to telemetry** — you can't. Claude Code reads
the OTel env vars once at startup, so a session launched without them exports
nothing and can't be switched on mid-flight. Restart it, keeping the
conversation:
```bash
# in that terminal: exit Claude, then
source /path/to/claude-telemetry/claude-env.sh
claude --continue    # resumes the most recent conversation in this directory
```
Only usage from the restart onward is recorded — tokens already spent were
never exported and can't be recovered.

**Confirm the collector is receiving data** — raw metrics should show up here
once Claude Code has exported at least once:
```bash
curl -s http://localhost:8889/metrics | grep claude_code
```

## Notes

- Everything binds to `127.0.0.1` only. Nothing is reachable from other hosts.
- **Privacy:** Claude Code labels every metric with identifying attributes
  (`user_email`, `user_id`, account IDs, `organization_id`). Prometheus drops
  these at scrape time via a `labeldrop` rule in `prometheus/prometheus.yml`, so
  they're never stored. `session_id` is kept (needed to keep each session's
  counters distinct). Nothing leaves your machine regardless.
- Grafana uses `admin/admin` — fine for a local-only tool; change it if you
  expose the port.
- The dashboard JSON is provisioned from `grafana/dashboards/` and checked into
  the repo, so edits in the Grafana UI won't persist across `down -v` unless you
  export and commit them back.

## License

MIT — see [LICENSE](LICENSE).

This is an independent, community project. It is not affiliated with, endorsed
by, or sponsored by Anthropic. "Claude" and "Claude Code" are trademarks of
Anthropic; they're used here only to describe what this tool integrates with.
