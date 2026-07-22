# What can I ask this dashboard?

Common Claude Code monitoring questions, and where — or whether — this stack
answers them. Most published question lists assume a *team* deployment; the
solo rewrite is given where it differs.

Legend: **A** answerable now · **B** small build · **C** needs the events
pipeline · **D** not applicable solo.

---

## A. Answerable now

| Question | Panel | Notes |
| --- | --- | --- |
| Which repos/projects am I using Claude in? | *Cost over time by project*, *Tokens & cost by project*, **Project** filter | Project isn't emitted by Claude Code — `claude-env.sh` injects it. |
| Is my usage growing? | *Tokens over time by type*, *Sessions per day* | Needs a few days of history to read as a trend. |
| How many sessions per day? | *Sessions per day* | One session = one `claude` launch. Subagents don't create sessions. |
| How much am I spending? | *Cost (API-equivalent)* | **Not your bill** on a subscription — see the caveat below. |
| Which models cost the most? | *Cost by model & source*, *Cost over time by model* | Opus is 5× Haiku per token in every lane. |
| Which models am I using? | **Model** filter | |
| **Am I using prompt caching effectively?** | *Cache amortization*, *Cache write % of cost*, *COST share by type* | **The strongest thing this stack does.** The built-in cost metric has no `type` label, so it can't separate cache writes from reads; the recording rule can. |
| Am I close to a rate limit? | *Rolling 5h token usage* | Shape only, not a percentage. `/usage` in Claude Code is authoritative. |
| Which sessions were efficient? | *Per-session efficiency* | Amortization per session; scan for outliers. |
| Where does my money actually go? | *COST share by type* vs *TOKEN share by type* | The two never match — that's the point. |

> All costs are **API-equivalent**: what the same work would cost pay-as-you-go.
> On a subscription your marginal cost is zero. Use these to compare projects,
> sessions and habits — and as a proxy for rate-limit pressure — never as a
> budget figure.

---

## B. Edit decisions — BUILT

Backed by `claude_code_code_edit_tool_decision` (labels `decision`,
`tool_name`, `source`, `language`).

Panels: *Edit acceptance rate*, *Edit decisions by tool*, *Edits by language*.

| Question | Caveat |
| --- | --- |
| Do I accept Claude's suggested edits? | |
| How often do I reject them? | SigNoz suggests warning above 30% rejection. |
| Which edit tools do I use most? | **Edit / Write / NotebookEdit only** — not Read, Bash or Grep. |
| Which languages does it struggle with? | Via the `language` label. |

---

## C. Latency & reliability — BUILT (events pipeline)

Claude Code exports **events** as well as metrics, carrying everything metrics
structurally cannot. Prometheus stores metrics only, so these go to **Loki**
(`OTEL_LOGS_EXPORTER=otlp` -> collector logs pipeline -> Loki).

Two caveats: only sessions started with logs export enabled appear here, and
**there is no backfill** — transcripts carry no timing data, so recovered
sessions contribute nothing.

| Question | Panel | Event field |
| --- | --- | --- |
| How long do API requests take? | *API request latency (p50/p95)* | `api_request.duration_ms` |
| What errors am I hitting? | *API errors* | `api_error.status_code` |
| Which tools are slowest? | *Tool execution time (p95 by tool)* | `tool_result.duration_ms` |
| Which tools do I use most *overall*? | *Tool usage (all tools)* | `tool_result.tool_name` |
| Are any tools consistently failing? | *Tool failure rate* | `tool_result.success` |

---

## D. Not applicable to a solo setup

| Team question | Why | Solo rewrite |
| --- | --- | --- |
| How many engineers are active? | n = 1 | — |
| Total spend per user | = your total | *Cost by project* |
| Requests per user | `user.email` / `user.id` dropped at scrape as PII | *Cost by project* |

---

## Choosing what to look at

Match the panel to the goal, rather than reading everything:

| Goal | Look at |
| --- | --- |
| **Spend less** | *COST share by type* → then [OPTIMIZATION-PLAYBOOK.md](OPTIMIZATION-PLAYBOOK.md). 61% of cost is context re-reads; session hygiene is second at 19.6%. |
| **Avoid throttling** | *Rolling 5h token usage*, plus `/usage` for the real number. |
| **Work faster** | *Tool execution time*, *Tool failure rate*, *API request latency*. |
| **Sanity-check habits** | *Per-session efficiency*, *Cache amortization*. |

### "All" is not the same as ticking every item

They produce different queries, and the difference is load-bearing:

| selection | Grafana sends | matches a series with **no** such label? |
| --- | --- | --- |
| **All** (with `allValue: ".*"`) | `query_source=~".*"` | **yes** |
| every item ticked individually | `query_source=~"(main\|auxiliary)"` | **no** |

A regex listing concrete values cannot match a label that is absent. Sessions
recovered from transcripts carry no `query_source` at all, so ticking every
item hides them while **All** shows them. If a filter change makes rows vanish,
this is usually why — reset to All.

Two metrics are **not** worth watching here: *Cache hit rate (SigNoz)* sits
near 100% and cannot move, and *Uncached input* is 0.1% of cost.

---

## Privacy note on events

Claude Code attaches `user.email`, `user.id`, account IDs and `organization.id`
to **events** as well as metrics. The `labeldrop` rule in `prometheus.yml`
protects metrics only — events bypass it entirely and would land in Loki as
structured metadata.

`otel-collector-config.yaml` therefore carries an `attributes/scrub-pii`
processor on the logs pipeline that deletes those five keys before export.
Verify with:

```bash
curl -s -G 'http://127.0.0.1:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={service_name="claude-code"}' --data-urlencode 'limit=1'
```

Prompt and response text stay out by default: `OTEL_LOG_USER_PROMPTS`,
`OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_ASSISTANT_RESPONSES`, `OTEL_LOG_TOOL_CONTENT`
and `OTEL_LOG_RAW_API_BODIES` are all deliberately unset.
