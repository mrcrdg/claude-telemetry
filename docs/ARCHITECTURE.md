# How this is built

A telemetry pipeline, structured the same way as any data pipeline: producers,
ingestion, transformation, storage, serving — plus a batch backfill path for
history the streaming path missed.

```
                    STREAMING PATH
Claude Code ──OTLP/gRPC :4317──▶ OTel Collector ──┬── :8889 ◀──scrape── Prometheus ──┐
 (producer)                      (ingest+transform)│   (pull, metrics)    (TSDB)      │
                                                   │                                  ├─▶ Grafana
                                                   └── push ──▶ Loki ─────────────────┘   (serving)
                                                       (logs/events)

                    BATCH PATH (backfill)
~/.claude/projects/*.jsonl ──▶ python ──▶ OpenMetrics ──▶ promtool ──▶ TSDB blocks
        (source of truth)      (transform)   (staging)     (loader)
```

---

## 1. Producers

Claude Code is the instrumented application. It emits two signal types over
**OTLP** (OpenTelemetry Protocol), gRPC to `localhost:4317`:

| signal | what it is | cadence |
| --- | --- | --- |
| **metrics** | 8 counters (tokens, cost, sessions, active time, edits, LOC, commits, PRs) | every 10s (`OTEL_METRIC_EXPORT_INTERVAL`) |
| **logs** (events) | `api_request`, `tool_result`, `api_error`, `tool_decision`, `user_prompt` | every 5s (`OTEL_LOGS_EXPORT_INTERVAL`) |

Turned on per-process by environment variables, read **once at startup** — the
reason a running session can't be switched on mid-flight.

## 2. Ingestion & transformation — the OTel Collector

The collector is the pipeline's transformation layer, in the usual
receiver → processor → exporter shape:

```yaml
receivers:  otlp (gRPC :4317)
processors: attributes/scrub-pii   # governance: drop 5 identifying keys
            batch                  # micro-batching, 10s flush
exporters:  prometheus  (:8889)    # metrics, exposed for pull
            otlphttp/loki          # events, pushed
```

Two things worth naming:

- **Fan-out by signal type.** One receiver, two sinks. Metrics and events have
  different storage shapes, so they diverge here rather than at the source.
- **Governance at the boundary.** `attributes/scrub-pii` deletes `user.email`,
  `user.id`, account IDs and `organization.id` *before* anything is persisted.
  Doing it here rather than at query time means the data never lands.

## 3. Storage — two stores, deliberately

| | Prometheus | Loki |
| --- | --- | --- |
| holds | metrics (numeric time series) | events (log lines + labels) |
| model | `name{labels} value @ts` | `{labels} line @ts` |
| ingest | **pull** (scrapes :8889 every 15s) | **push** (collector sends) |
| query | PromQL | LogQL |
| retention | 90d | default |

**Why not one store?** A metric is a number you aggregate over time; an event
is a record you filter and inspect. Prometheus can't hold `duration_ms` per
request — that's a cardinality explosion. Loki can't cheaply sum 95M tokens.
Different questions, different engines.

### The dimensional model

Metrics are effectively a **fact table with dimensions**:

- **fact** — the counter value (cumulative tokens, cumulative USD)
- **dimensions** — labels: `project`, `model`, `type`, `session_id`,
  `query_source`, `effort`, `terminal_type`

Every label multiplies the series count, so label choice *is* partition design.
That's why `user.email` gets dropped (one value, pure overhead) while
`session_id` is kept (needed to stop separate sessions colliding into one
series and breaking `increase()`).

Counters are **cumulative, not deltas** — set via
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`. This matters
more than it sounds: it makes writes **idempotent**, which is what allows the
backfill to be re-run safely.

## 4. Transformation at rest — recording rules

`prometheus/rules/claude-cost.yml` is a **materialized view**, the same idea as
a dbt model: evaluate an expression on a schedule, persist the result as a new
series.

```
claude_code_token_usage{model,type} × rate(model,type) ──▶ claude_code_token_cost_usd
```

It exists because the source metric `claude_code_cost_usage` has no `type`
label and so can't answer "cache writes vs reads". Rather than repeat pricing
arithmetic in every panel, the join happens once, upstream.

Caveat that shaped the design: recording rules only ever evaluate **live
scrapes**. They never touch historical or backfilled data — hence the backfill
script computes cost itself.

## 5. Serving — Grafana

Queries both stores. Dashboard JSON is **provisioned from files** rather than
clicked together, so it's version-controlled and reproducible; Grafana reloads
it from disk on a 30s interval.

## 6. The batch path — backfilling from transcripts

The streaming path only captures sessions that started with telemetry enabled.
Everything else was lost — except Claude Code writes a full transcript to
`~/.claude/projects/*.jsonl` with per-message token counts. That's a **source
of truth the pipeline can be replayed from**.

`scripts/backfill-from-transcripts.py` is a batch ETL job:

| stage | what happens |
| --- | --- |
| **extract** | parse JSONL, pull `usage`, `timestamp`, `sessionId`, `cwd`, `model` |
| **transform** | resolve `project` (git root, worktree-aware, override map); accumulate per-message deltas into cumulative counters; price per token type |
| **dedupe** | per-session **watermark** — only emit samples newer than what's already stored |
| **stage** | write OpenMetrics text |
| **load** | `promtool tsdb create-blocks-from openmetrics` → TSDB blocks → move into the volume |

Recovered 11 sessions and ~$100 of history that had never reached the
dashboard.

### Three data-engineering problems this hit

**Idempotency.** First version deduped *per session*, so a session imported
once could never be topped up — one sat at 20.6M tokens against 143.7M in its
transcript. Fixed with a per-sample watermark. Emitting **cumulative** values
makes re-runs safe: rewriting a stored sample restates the same total, whereas
re-sending a delta would double-count.

**Late-arriving data vs. the write path.** Loading blocks that overlap
Prometheus's in-memory head causes the head to be truncated on restart —
samples still in the WAL are dropped. Mitigated with a `--cutoff-hours` guard,
a volume backup, and verification after load. It still cost one small session,
which the transcripts could restore.

**Schema drift between paths.** Backfilled rows are *thinner* than live ones —
no `query_source`, no `active_time`, no `session_count`, because transcripts
don't record them. That silently broke the dashboard: Grafana expands "All" to
the observed value list (`query_source=~"(main|auxiliary)"`), which matches
nothing when the label is absent. Fixed with `allValue: ".*"`. Classic
schema-evolution bug — old rows lack a column added later.

---

## 7. If you've built data pipelines, the mapping is

| here | usual equivalent |
| --- | --- |
| Claude Code | instrumented producer |
| OTLP | wire protocol |
| OTel Collector | ingestion + transformation tier |
| `batch` processor | micro-batching |
| `attributes/scrub-pii` | PII redaction / governance |
| Prometheus scrape | pull-based ingestion |
| Loki push | push-based ingestion |
| labels | dimensions / partition keys |
| cumulative counters | idempotent writes |
| recording rules | materialized view / dbt model |
| transcripts → OpenMetrics | replayable source + staging format |
| watermark dedupe | incremental load |
| head-block overlap | compaction hazard on concurrent write |

The one genuinely unusual property: **the source of truth outlives the
pipeline**. Transcripts sit on disk regardless of whether telemetry was
running, so history is always replayable. Most pipelines can't recover what
they didn't capture at the time.

---

## 8. What this is NOT

The table above is a mapping of *ideas*, not of tooling. No Kafka, no dbt, no
Airflow — and the analogies are looser than they look:

| Concept | What a data platform uses | What's here | Where the analogy breaks |
| --- | --- | --- | --- |
| Message bus | Kafka / Pulsar / Kinesis | OTel Collector | The collector buffers in memory for ~10s and forwards. It is **not a durable log**: no partitions, no consumer groups, no offset replay, no retention. Stop the collector mid-session and those metrics are gone. |
| Transformation | dbt | Prometheus recording rules | A rule is one expression on a timer. **No DAG, no `ref()`, no tests, no lineage, no docs, no dev/prod targets.** One "model", hand-written. |
| Orchestration | Airflow / Dagster / Prefect | *nothing* | The backfill is run by hand. No schedule, no retries, no dependency graph, no failure alerting, no run history. Prometheus's rule evaluator is a timer, not an orchestrator. |
| Data quality | dbt tests / Great Expectations | *nothing* | Correctness was checked by ad-hoc queries during development. Nothing runs on a schedule to catch drift. |
| Warehouse | Snowflake / BigQuery / Delta | Prometheus TSDB + Loki | Purpose-built time-series stores. No SQL, no joins across sources, no schema evolution support — which is exactly why the thin-backfill-rows problem bit. |
| Deployment | Kubernetes / Terraform | Docker Compose | Single node, no HA, no autoscaling, no secrets management. |

### Is that the wrong call?

For this workload, no. One user, one machine, ~250M tokens, ~100 MB on disk.
Kafka would add a broker to buffer a stream that peaks at a few hundred samples
a second. dbt would add a compiler for a single transformation.

**What it costs:** the gaps are real, and two of them already bit —

- **No durable buffer.** Collector down = data lost, permanently. Kafka's whole
  point is that the producer can keep writing while the consumer is dead.
  Mitigated here only by luck: transcripts happen to be a replayable source.
- **No orchestration.** The backfill is a manual, multi-step, stateful
  procedure with a known hazard (head-block overlap). That is precisely the
  kind of job an orchestrator should own — with retries, a run log, and a
  guard against concurrent runs.
- **No tests.** The `query_source` schema-drift bug silently hid most of the
  history and was found by eye. A single "row count by path should not diverge"
  assertion would have caught it.

### When you'd actually add them

| Add | When |
| --- | --- |
| **Kafka** | More than one producer host, or losing data during a collector restart becomes unacceptable. |
| **Airflow/Dagster** | The backfill needs to run on a schedule, or any second batch job appears. |
| **dbt + a warehouse** | You want to join telemetry against something else — git history, ticket data, CI runs — which Prometheus cannot do. |
| **Data tests** | Done — `scripts/check-data-quality.py`, see below. |

### Why not Great Expectations / dbt tests

Both validate **tabular batches** — a DataFrame, or a table in a warehouse.
The data here is time series in a TSDB, queried with PromQL, which neither
speaks. Wiring GX in would mean PromQL → DataFrame → expectation suite, adding
a Data Context, suites and checkpoints to express what is currently six
assertions.

The checks that actually matter here aren't column constraints (`not_null`,
`unique`) — they're **reconciliations between series**, so they're written as
PromQL:

| check | what it catches |
| --- | --- |
| derived cost vs `claude_code_cost_usage` | wrong rates in the recording rule — Claude Code emits its own cost figure, so we have independent ground truth |
| every model with tokens has cost | a model missing from the rate table, which vanishes from cost panels without erroring |
| stored cost vs tokens × current rates | data written before a rate change; recording rules never revisit old samples |
| label completeness per path | the backfill/live schema drift that hid most of the history |
| freshness | nothing arriving |

The first of those **found a real bug on its first run**: session `24a61c47`
is stored 21.5% under Claude Code's own figure, because it was imported while
`cacheCreation` was still priced at 1.25×. The rate was corrected in both the
rule and the script, but already-written samples were never revisited — which
is exactly the failure mode a reconciliation check exists to catch.

The right heavier tool, if this grew, would be **`promtool test rules`** —
Prometheus's native unit-test framework for recording rules — plus alerting
rules for freshness. Both speak PromQL natively.
