# This stack vs. the SigNoz reference

Compared against [SigNoz's Claude Code monitoring
guide](https://signoz.io/blog/claude-code-monitoring-with-opentelemetry/) and
their [degradation-measurement
post](https://signoz.io/blog/claude-code-measure-degradation-opentelemetry/) —
the most complete public write-ups on Claude Code telemetry.

Three sections: [what both have](#1-what-both-have), [what only we
have](#2-what-only-we-have), [what only they have](#3-what-only-they-have).

---

## 1. What both have

Both collect the same underlying metrics — Claude Code only emits eight, so
there's no room to differ. The difference is what gets built on top.

| Concept | Theirs | Ours | What it means |
| --- | --- | --- | --- |
| Token usage | "Total input/output token usage", "Token usage trends over time" | *Total tokens*, *Tokens over time by type*, *TOKEN share by type* | Volume by `type` (input / output / cacheRead / cacheCreation). On its own it's misleading — see *Cost share*, below. |
| Cost | "Total cost in USD" | *Cost (API-equivalent)*, *Cost over time by model* | API-equivalent spend. On a subscription this is **not** your bill — it's what the same work would cost pay-as-you-go. |
| Sessions | "Sessions and conversations count" | *Sessions*, *Sessions (distinct)*, *Sessions per day* | One session = one `claude` launch. Subagents do **not** create sessions; they share the parent's `session_id` and appear as `query_source="subagent"`. |
| Model split | "Model distribution" | *Cost over time by model*, *Cost by model & source* | Which model spent the money. Opus costs 5× Haiku per token across every lane. |
| Cache hit rate | `cacheRead / (input + cacheRead)`, warn below 60% | *Cache hit rate (SigNoz)* — same formula, same threshold | Share of needed context served from cache. **Saturates near 100% on Claude Code** (uncached `input` is negligible), so it's good for cross-team comparison and useless as a daily signal. Ours reads 99.98%. |
| 5-hour window | "5-hour rolling quota usage" | *Rolling 5h token usage*, *Tokens in the last 5h* | Claude's session limit resets on a 5-hour cycle, so this is the closest proxy for "am I burning the window fast". **Not a quota percentage** — that exists only in `/usage`. |
| Active time | "Session duration" | *Active time* | `type=user` (you typing) vs `type=cli` (Claude working). Live sessions only here — see the caveat in §2. |

---

## 2. What only we have

### Cost split by token type — the core addition

`claude_code_cost_usage` has **no `type` label**, so neither their dashboard nor
any other built on the raw metric can answer *"how much am I paying for cache
writes versus cache reads?"* — the question that actually drives spend.

`prometheus/rules/claude-cost.yml` re-prices `claude_code_token_usage` per
`(model, type)` into `claude_code_token_cost_usd`, which powers:

| Panel | What it means |
| --- | --- |
| *COST share by type* | The same four types as the token pie, priced. The two pies never match: cacheRead is ~91% of tokens but ~27% of cost. |
| *Cost by token type* | Same data, ranked. |
| *Cache write % of cost* | Cache writes cost **2×** input, reads **0.1×** — a 20× spread. Green under 30%. |

### Cache amortization — our headline metric

`cacheRead / cacheCreation`: how many times you reused what you paid to cache.
Break-even is **2.11 reuses** (arithmetic from the published multipliers), so
below 2× caching costs more than sending fresh. Unlike the SigNoz cache hit
rate this one actually moves — ours ranges 2.3× to 73.8× across sessions.

The strongest driver is session length: one-shot `claude -p` runs land at 3–5×,
long interactive sessions at 20–74×.

### Generation intensity & uncached input

`output / cacheRead` (output is the 5× lane, and thinking is billed as output)
and `input / total` (should be ~0%; higher means the prefix cache is being
defeated).

### Per-project attribution

Claude Code emits **no** project, cwd or git-branch label — deliberately, to
avoid unbounded cardinality. We inject one via `OTEL_RESOURCE_ATTRIBUTES`,
resolved by git repository root (worktree-aware) with a `project-map.json`
override. Powers the *Project* filter, *Cost over time by project* and
*Tokens & cost by project*.

### Per-session efficiency table

One row per session with its amortization ratio, so the difference between
working styles is visible rather than inferred.

### Recovery of untracked sessions

A session launched without the OTel env vars exports nothing — but its
transcript still records per-message token counts.
`scripts/backfill-from-transcripts.py` reconstructs them. Recovered 139.7M
tokens across projects that had never appeared on the dashboard.

**Caveat:** recovered data is thinner than live data. Transcripts carry no
`query_source`, no `active_time`, and no `session_count`, so those panels only
ever reflect telemetry-enabled sessions.

### Stricter PII handling

The SigNoz guide gives **no** cardinality or PII controls. We drop
`user.email`, `user.id`, `user.account_id`, `user.account_uuid` and
`organization_id` at scrape time via `labeldrop`, keeping only `session_id`
(needed to keep each session's counters distinct).

### Thresholds with stated provenance

Every band on the scorecard is labelled as either arithmetic from published
pricing, an external reference, or this machine's own percentiles — so you
know which numbers to trust. See §3.4 of the metrics guide.

---

## 3. What only they have

| Their panel | Why we don't have it | Could we? |
| --- | --- | --- |
| **P95 command duration** | Duration lives in the `claude_code.api_request` **event**, not any metric. Events need a log store; Prometheus holds metrics only. | Only with Loki + a logs pipeline. |
| **Request success rate** | Same — needs `api_request` / `api_error` events. | Same. |
| **Tool types usage** | Same — needs `tool_result` events. | Same. |
| **Requests per user** | Deliberate omission. Those attributes are PII and, on a single-user setup, identical across every series — cardinality with no signal. | Yes, by removing the `labeldrop` rule. Not recommended. |
| **Terminal type distribution** | The `terminal_type` label **is** available; we just don't plot it. Single-valued here (`gnome-terminal`), so it can't split anything. | Yes, trivially — useful only in a team setting. |
| **User decision tracking (accept/reject)** | Not built yet. `claude_code_code_edit_tool_decision` has data (labels `decision`, `tool_name`, `source`, `language`). | **Yes — best remaining candidate.** Acceptance rate is the strongest quality signal available from metrics alone. SigNoz suggests warning above 30% rejection. |
| **Prompt logging** (`OTEL_LOG_USER_PROMPTS=1`) | Ships prompt text into the log store. | Deliberately off. |

### Metrics we collect but nobody has data for yet

`claude_code_pull_request_count` and `claude_code_commit_count` are exported by
Claude Code and would flow through unchanged — they're empty here simply
because no PR or commit has been made from a telemetry-enabled session.

---

## 4. The naming difference — read this before copying their queries

| | SigNoz | Here |
| --- | --- | --- |
| Tokens | `claude_code_token_usage_tokens_total` | `claude_code_token_usage` |
| Cost | `claude_code_cost_usage_USD_total` | `claude_code_cost_usage` |
| Active time | `claude_code_active_time_seconds_total` | `claude_code_active_time_total` |

We set `add_metric_suffixes: false` on the collector's Prometheus exporter, so
names stay predictable and don't pick up unit/`_total` mangling. The trade-off
is that **PromQL copied from their posts will not run here unmodified** — strip
the suffixes first.
