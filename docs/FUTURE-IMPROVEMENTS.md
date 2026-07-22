# Future improvements

Ideas for extending this stack, roughly in order of value-for-effort. Nothing
here is implemented yet.

Contents:
1. [Gaps in what we already collect](#1-gaps-in-what-we-already-collect)
2. [Metrics Claude Code emits that we never display](#2-metrics-claude-code-emits-that-we-never-display)
3. [Events: the whole missing half](#3-events-the-whole-missing-half)
4. [Known rough edges](#4-known-rough-edges)
5. [Comparison with the community "Claude Code Metrics" dashboard](#5-comparison-with-the-community-claude-code-metrics-dashboard)

---

## 1. Gaps in what we already collect

These need no new pipeline — the labels are already in Prometheus, we just
don't put them on screen.

### Split active time into `user` vs `cli`

`claude_code_active_time_total` carries `type` = `user` (you typing) or `cli`
(Claude working). Our *Active time* stat tile sums both, which hides the more
interesting number.

```promql
sum by (type) (increase(claude_code_active_time_total{project=~"$project"}[$__range]))
```

### Attribution by agent / skill / plugin / MCP server

Claude Code attaches `agent.name`, `skill.name`, `plugin.name`,
`marketplace.name`, `mcp_server.name` and `mcp_tool.name` to the token and cost
counters. They only appear when that machinery is actually used, so they're
absent from a plain session — but they answer "which subagent/skill/MCP server
is eating my budget", which nothing else can.

```promql
topk(10, sum by (agent_name) (increase(claude_code_cost_usage[$__range])))
```

Note the OTel `.` → `_` conversion: `agent.name` becomes `agent_name` in
Prometheus.

### `start_type` breakdown

`claude_code_session_count` carries `start_type` (`fresh` / `resume` /
`continue` / `agents_view`). A fresh session re-reads context from scratch; a
long chain of `resume` behaves very differently on cache. Cheap panel, useful
signal.

### `speed` (fast mode)

The `speed="fast"` label appears on token/cost counters when fast mode is on.
Worth a filter or a split, since it changes the cost profile.

---

## 2. Metrics Claude Code emits that we never display

All of these are documented and would flow through the existing collector with
no config change — they simply haven't shown up yet because nothing in the
tracked sessions triggered them.

| Metric | Why it's interesting |
| --- | --- |
| `claude_code_lines_of_code_count` | `type` = added/removed. Output volume, not just token spend. |
| `claude_code_commit_count` | Commits made through Claude Code. |
| `claude_code_pull_request_count` | PRs opened through Claude Code. |
| `claude_code_code_edit_tool_decision` | `decision` = accept/reject, plus `tool_name`, `source`, `language`. **Acceptance rate is the single best quality signal available** — it says how often the suggested edit was actually kept. |

A panel for edit acceptance rate:

```promql
sum(increase(claude_code_code_edit_tool_decision{decision="accept"}[$__range]))
  /
sum(increase(claude_code_code_edit_tool_decision[$__range])) * 100
```

Caveat: these counters only exist once the corresponding action happens, so
panels will show "No data" on a fresh install. Set the panel's *No value* to
`0` rather than leaving it blank.

---

## 3. Events: the whole missing half

We only export **metrics** (`OTEL_METRICS_EXPORTER=otlp`). Claude Code also
exports **events/logs**, which carry everything metrics structurally cannot —
per-request timings, tool outcomes, and errors.

Relevant events and their payloads:

| Event | Notable attributes |
| --- | --- |
| `claude_code.api_request` | `duration_ms`, `input_tokens`, `output_tokens`, `cost_usd`, `model`, `request_id` |
| `claude_code.api_error` | `duration_ms`, `error`, `status_code`, `attempt` |
| `claude_code.tool_result` | `tool_name`, `duration_ms`, `success`, `error_type`, result size |
| `claude_code.tool_decision` | `tool_name`, `decision`, `source` |
| `claude_code.user_prompt` | `prompt_length` (content redacted unless opted in) |
| `claude_code.skill_activated`, `mcp_server_connection`, `auth`, … | lifecycle |

Everything from one prompt shares a `prompt.id`, so events can be correlated
into a single timeline.

**Why this matters:** the per-step duration shown in the CLI ("Thinking… 15s")
lives in `api_request.duration_ms`. There is **no metric** carrying it — the
closest metric proxy is `active_time_total{type="cli"}`, which is an aggregate,
not a per-request latency. Time-to-first-token (`ttft_ms`) exists only on trace
spans, gated behind `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`.

Implementing this means adding a log store — Prometheus cannot hold events:

1. Add Loki to `docker-compose.yml`.
2. Add a `logs` pipeline to `otel-collector-config.yaml` with a `loki` exporter.
3. Add `OTEL_LOGS_EXPORTER=otlp` to `claude-env.sh`.
4. Add Loki as a second Grafana datasource.

Unlocks: p50/p95 request latency, slowest tools, tool failure rate, API error
and retry rate, prompt-length distribution.

**Privacy warning.** Keep `OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS`,
`OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_CONTENT` and
`OTEL_LOG_RAW_API_BODIES` **off**. They ship prompt and response text, and file
contents, into the log store. The `labeldrop` rule in `prometheus.yml` protects
metrics only — it does nothing for logs, so events need their own scrubbing via
a collector `attributes` processor.

---

## 4. Known rough edges

### `increase()` reports 0 for very short sessions

Every panel uses `increase()`, which needs the counter to *grow* inside the
window. A session that starts and finishes inside a single export interval
(common with `claude -p "..."`) publishes its counter already at the final
value, so it never grows and `increase()` returns 0 — even though the raw
counter plainly holds the data.

Interactive sessions span many exports and are unaffected. Options if this
becomes annoying:

- Shorten `OTEL_METRIC_EXPORT_INTERVAL` further (helps a little, never fully).
- Add companion "lifetime total" tiles using raw counters, accepting that they
  ignore the time picker (this is what the community dashboard does — see
  below).
- Prefer `sum by (session_id)` + `max_over_time` when a per-session total is
  what you actually want.

### Retention

Prometheus runs with defaults (15 days). For cost trends over months, set
`--storage.tsdb.retention.time` in `docker-compose.yml`, and consider recording
rules to pre-aggregate daily cost per project so long-range queries stay cheap.

### Alerting

Nothing is wired up. An obvious first rule: daily spend per project crossing a
threshold. Grafana's built-in alerting is enough — no extra components needed.

---

## 5. Comparison with the community "Claude Code Metrics" dashboard

Assessment of the `dashboard.grafana.app/v2beta1` dashboard (`claude-code-metrics`,
Grafana 12.3.0) against ours.

### Blockers to importing it as-is

Three reasons it will not drop into this stack unchanged:

1. **Metric names don't match.** It queries
   `claude_code_token_usage_tokens_total`, `claude_code_cost_usage_USD_total`,
   `claude_code_active_time_seconds_total` — the OTel Prometheus exporter's
   default unit/`_total` suffixes. Our collector sets
   `add_metric_suffixes: false`, so ours are `claude_code_token_usage`,
   `claude_code_cost_usage`, `claude_code_active_time_total`. Every query needs
   renaming (or we'd have to flip that collector setting, which would break all
   our existing panels).
2. **Schema version.** `apiVersion: dashboard.grafana.app/v2beta1` needs
   Grafana 12.x. We run 11.4.0, whose provisioning expects the classic JSON
   model.
3. **The layout is empty.** `spec.layout.spec.items` is `[]` while 20+ panels
   are defined in `spec.elements`. As shared, nothing has a position — the
   panels exist but aren't placed on the grid.

So this is a source of *ideas*, not a file to import.

### Worth adopting

| Idea | Verdict |
| --- | --- |
| **Cache efficiency gauge** — `cacheRead / (cacheRead + input) * 100` | **Take it.** Best idea in the file. Turns our raw cache numbers into one readable "am I reusing context" figure. Thresholds red<50<yellow<80<green are sensible. |
| **Distinct session count** — `count(count by (session_id)(claude_code_token_usage))` | **Take it.** Genuinely better than our `increase(claude_code_session_count)`: counting distinct `session_id` values sidesteps the short-session `increase()` problem entirely. |
| **Cost per 1K output tokens** | **Take it.** A unit-economics number that's comparable across time even as volume changes. |
| **Active time split `user` vs `cli`** | **Take it** — same gap already noted in §1. |
| **Separate stat tiles per token type** | **Maybe.** Four tiles is a lot of space for what our donut already shows; useful only if you want the exact numbers at a glance. |
| **`rate()` for time series instead of `increase()`** | **Maybe.** Smoother curves and no short-session zeroes, but the y-axis becomes "tokens/sec", which is less intuitive than "tokens in this bucket". |
| **Lines of code / commits panels** | **Take the metrics, not the queries** — see the bug below. |

### Not worth adopting

| Idea | Verdict |
| --- | --- |
| **`sum(sum_over_time(claude_code_lines_of_code_count_total[$__range]))`** | **Bug — don't copy.** `sum_over_time` adds up every scraped *sample* of a cumulative counter. At a 15s scrape over 1h that's ~240 samples, inflating the result by roughly 240×. It should be `increase()`. |
| **"Productivity Ratio" — `active_time{cli} / active_time{user}`** | **Skip.** It's a ratio of two cumulative counters, so it converges toward a flat lifetime average and stops responding to anything. Framing "Claude spent more seconds than I did" as productivity is also a stretch — a slow tool call inflates it. |
| **"Peak Leverage" — `max_over_time` of that ratio** | **Skip.** The max of a converging ratio is dominated by early-session noise, when the user-time denominator is near zero. It measures startup order, not a peak. |
| **`lastNotNull` on raw counters for every total** | **Skip as the default.** It reports lifetime totals while the dashboard shows a time picker defaulted to `now-1h`, so the numbers silently ignore the selected range. Our `increase()` approach is honest about the window; the tradeoff is the short-session zero in §4. Worth adding as clearly-labelled "all time" tiles, not as the main tiles. |
| **Fixed cost thresholds (yellow $10, red $50)** | **Skip.** On a subscription, `cost.usage` is API-equivalent pricing, not your bill. Colouring it like a budget invites misreading — the README already flags this. |
| **No template variables (`variables: []`)** | **Skip.** It has no filtering at all. Our Project / Model / Query source variables are the more useful design. |
| **No PII handling** | **Keep ours.** That dashboard doesn't address it, and Claude Code attaches `user.email`, `user.id`, account and org IDs to every data point. Our `labeldrop` rule in `prometheus.yml` strips them at scrape time. Don't regress this when borrowing panels. |

### Suggested order of work

1. Cache efficiency gauge + cost per 1K output (small, high value).
2. Distinct-session count to replace the current Sessions tile.
3. Active time split by `type`.
4. Edit acceptance rate + lines of code, using `increase()` — not `sum_over_time`.
5. Agent / skill / MCP attribution panels.
6. Loki + events pipeline, for latency and tool-failure visibility.
