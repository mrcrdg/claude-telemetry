# Reading the metrics: what to aim for

What each number means, what a healthy value looks like, and what to actually
*do* when one moves. Written against real numbers from this setup, not theory.

Contents:
1. [The four elementary metrics](#1-the-four-elementary-metrics)
2. [Price is the whole story](#2-price-is-the-whole-story)
3. [The ratios that matter](#3-the-ratios-that-matter)
4. [Diagnosing a bad number](#4-diagnosing-a-bad-number)
5. [What you can actually control](#5-what-you-can-actually-control)
6. [Plan usage limits — why they're not here](#6-plan-usage-limits--why-theyre-not-here)

---

## 1. The four elementary metrics

Yes — everything on the dashboard reduces to four token types on
`claude_code_token_usage{type=...}`. Every other panel is these four, sliced or
priced.

| type | what it is |
| --- | --- |
| `input` | Fresh tokens sent to the model that were **not** served from cache. Your new prompt, plus anything the cache couldn't cover. |
| `output` | Tokens the model generated. **Extended thinking counts here** — thinking is not free and not separate. |
| `cacheCreation` | Tokens **written** into the prompt cache so later turns can reuse them. |
| `cacheRead` | Tokens **read back** from the cache instead of re-sent as input. |

The mental model: a conversation has a large, mostly-stable prefix (system
prompt, tools, files you've read, history). Re-sending it every turn at full
`input` price would be ruinous. Instead it's written once (`cacheCreation`) and
replayed cheaply on every subsequent turn (`cacheRead`).

That's why `cacheRead` is normally the overwhelming majority of your tokens —
and why that's a sign of health, not waste.

---

## 2. Price is the whole story

Token counts are misleading on their own. What matters is that the four types
have wildly different prices. Per token, relative to `input`:

| type | multiplier | Opus 4.8 $/MTok | Haiku 4.5 $/MTok |
| --- | --- | --- | --- |
| `cacheRead` | **0.1×** | $0.50 | $0.10 |
| `input` | 1× | $5.00 | $1.00 |
| `cacheCreation` | **2×** | $10.00 | $2.00 |
| `output` | **5×** | $25.00 | $5.00 |

Two consequences worth internalizing:

- **A cacheRead token is 50× cheaper than an output token**, and 20× cheaper
  than a cacheCreation token. Volume in the cheap lane is nearly free; volume
  in the expensive lanes is not.
- **Token share ≠ cost share.** Measured here: `cacheRead` is ~91% of tokens
  but **27% of cost**, while `cacheCreation` is ~8% of tokens and **46% of
  cost**. Reading a token-count chart alone points you at exactly the wrong
  thing.

> The `cacheCreation` multiplier is 2× — the **1-hour** cache TTL rate (the
> 5-minute rate would be 1.25×). Verified against local transcripts: all
> 4,196,742 cache-creation tokens across 1,262 messages were
> `ephemeral_1h_input_tokens`, none were 5m. Re-check on your own machine with
> `jq -r '.message.usage.cache_creation | select(.)' ~/.claude/projects/*/*.jsonl`.

Everything is **API-equivalent pricing**. On a subscription this is not your
bill. It's a consistent yardstick for comparing sessions, projects, and habits.

---

## 3. The ratios that matter

Absolute totals mostly tell you how much you worked. Ratios tell you how
*efficiently*. There are three worth watching.

### 3.1 `cacheRead / cacheCreation` — cache amortization

**The single most useful number.** How many times you got to reuse what you
paid to cache.

Caching isn't free: writing N tokens costs 2N instead of the 1.0N you'd pay
sending them fresh. You're N in the hole, and each later read of that prefix
saves you 0.9N. So:

```
cached:   2N + 0.1N x (R-1)      uncached: 1.0N x R      (R = requests sharing the prefix)
break-even at R ≈ 2.11
```

**Caching pays for itself from the third turn onward.** After that every read
is nearly pure savings — by R=12 you pay 3.1N instead of 12N.

| ratio | reading |
| --- | --- |
| **< 2×** | Bad — below break-even. Caching is costing you more than sending fresh would. |
| **2–5×** | Weak. Typical of very short one-shot sessions. |
| **5–15×** | Healthy. Normal interactive work. |
| **> 15×** | Excellent — a long session reusing a stable context. |

Real numbers from this setup:

| session | cacheRead | cacheCreation | ratio |
| --- | ---: | ---: | ---: |
| long interactive session | 1,229,574 | 101,453 | **12.1×** |
| earlier interactive session | 107,889 | 10,347 | **10.4×** |
| `claude -p` one-shot | 35,953 | 7,286 | 4.9× |
| `claude -p` one-shot | 15,120 | 4,965 | 3.0× |

The pattern is visible immediately: **interactive sessions amortize at ~2–4×
the rate of one-shots.** Aim to stay above ~5×.

### 3.2 `output / cacheRead` — generation intensity

Output is the priciest lane (5×), so even a small ratio carries real cost.

| ratio | reading |
| --- | --- |
| **< 1%** | Reading/analysis-heavy work. Cheap. |
| **1–3%** | Normal mixed coding. |
| **> 5%** | Generation-heavy — writing lots of code/prose, or thinking hard. Justify it or dial effort down. |

Observed here: 0.7% and 2.2%. Both fine.

### 3.3 `input / total` — is caching even working?

`input` should be **near zero** — a rounding error. It's the fresh, uncached
content, and the cache should be covering almost everything.

| share | reading |
| --- | --- |
| **< 1%** | Correct. Caching is doing its job. |
| **> 5%** | Something is defeating the cache. Investigate. |

Observed here: 0.2%. Healthy.

### 3.4 `cacheRead / (input + cacheRead)` — the community cache hit rate

This is the definition **SigNoz** uses, and the closest thing to a shared
industry metric. Their guidance: warn below **60%**, or on a drop of more than
**15 points week-over-week**. Reported team values span under 15% to over 60%.

It's on the dashboard for cross-team comparability — but expect it to read
~100% for Claude Code, because uncached `input` is almost always negligible.
Measured here: **99.98%**. Excellent by the community bar, and useless as a
daily signal, because it can't move. Use amortization (§3.1) for that.

### Where these thresholds come from — and their limits

Three different kinds of number are mixed together on that scorecard. Only the
first is authoritative:

**1. Arithmetic from published pricing (trustworthy).** The **2.11× break-even**
for amortization falls straight out of the published multipliers — 2× to write,
0.1× to read. It's why the red band sits below 2×, and it isn't a matter of
opinion. The ordering of the four lanes (cacheRead ≪ input < cacheCreation ≪
output) is likewise just the price table.

**2. A published community threshold (one external source).** The 60% cache-hit
warning comes from SigNoz. It's a real external reference, but it's one vendor's
guidance, not a standard — and as noted above it doesn't discriminate here.

**3. This machine's own baseline (what the green/yellow bands actually are).**
Measured across 14 recovered sessions:

| | min | p25 | median | p75 | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| amortization | 2.3× | 9.1× | **14.6×** | 27.8× | 73.8× |
| generation intensity | — | — | **1.00%** | 1.97% | 12.52% |

The bands are set from those percentiles: amortization yellow at p25 (9×) and
green at the median (15×); generation intensity yellow at p75 (2%). So "green"
means *at or better than how you normally work* — not that you've hit some
external standard.

**Every source found says the same thing:** establish your own baseline over
two to four weeks and alert on drift from it, rather than trusting absolute
targets. Re-run the percentile query as your history grows and move the bands.
There is no published "correct" number to reach for.

### Summary: what to maximize and minimize

| metric | direction | why |
| --- | --- | --- |
| `cacheRead` | **maximize** (as a share of tokens) | Cheapest lane, 0.1×. High share = context reuse working. |
| `cacheRead / cacheCreation` | **maximize**, keep > 5× | Cache amortization. The headline efficiency number. |
| `cacheCreation` share of **cost** | **minimize**, keep < 30–40% | Paying to build context you don't reuse enough. Writes are 2× input, the priciest lane after output. |
| `output` | **minimize** for a given amount of work | 5× price. Includes thinking tokens. |
| `input` | **minimize**, should be ~0 | Non-zero means cache isn't covering your context. |

Don't chase `cacheCreation` to zero — it's the **cost of admission**, not
waste. Zero cache writes means zero cache reads and a much larger bill. The
goal is a high read/write ratio, not a low write count.

---

## 4. Diagnosing a bad number

### Cache write share of cost is climbing (> 50%)

You're repeatedly paying to build context you don't reuse enough. Ranked by
likely impact:

1. **Too many short sessions.** Every fresh session rebuilds the cache from
   scratch. This is the #1 cause. Fix: `claude --continue` instead of starting
   over.
2. **Long idle gaps.** The cache has a TTL. Walk away long enough and the next
   turn re-writes the whole prefix. Fix: finish a thread, then stop — don't
   leave a session parked mid-task for hours.
3. **Context churn.** The cache is a *prefix* match — a change early in the
   prefix invalidates everything after it. Repeatedly reading and editing large
   files that sit in the cached prefix forces re-writes.
4. **Model switching mid-task.** Caches are per-model. Switching abandons the
   cache you paid for.

### Output cost share is climbing

1. **Effort level.** Higher effort means more thinking, and thinking is billed
   as output at 5×. Match effort to task difficulty rather than defaulting high.
2. **Regenerating whole files** instead of targeted edits. A full-file rewrite
   is output tokens; a small edit is not.
3. **Verbose responses.** Asking for summaries, explanations, and recaps you
   don't read is directly billed at the most expensive rate.

### `input` share is climbing above a few percent

Something is defeating the prefix cache — most likely context being rebuilt in
a different order or shape each turn. Rare in normal Claude Code use; if you
see it, correlate with `start_type` and model switches.

### Cost per session is climbing

Check it against **Sessions (distinct)** on the same row. If session count fell
while cost/session rose, that's *fine* — you consolidated work into fewer,
longer sessions, which is what you want. Cost per session is only a bad signal
when total cost rises with it.

---

## 5. What you can actually control

Being honest about which levers exist, since some efficiency advice assumes API
control that Claude Code doesn't expose:

| lever | control | effect |
| --- | --- | --- |
| Continue vs. restart sessions | **Direct** (`--continue` / `--resume`) | Biggest single lever on cache write cost. |
| Model choice | **Direct** | Haiku is 5× cheaper per token than Opus across the board. |
| Effort level | **Direct** | Drives thinking tokens, billed as output at 5×. |
| Scope of what gets read | **Indirect** (how you phrase requests) | Pulling in fewer large files means a smaller prefix to cache. |
| Output verbosity | **Indirect** (prompting) | Ask for what you'll read. |
| Idle gaps | **Behavioural** | Avoid parking a session for hours mid-task. |
| `cache_control` placement | **None** | Claude Code manages this. Not your knob. |
| Cache TTL | **None** | Not exposed or reported. |

The highest-leverage habit change, given the data above: **fewer, longer
sessions**. It improves the read/write ratio, which is the ratio that moves
cost.

---

## 6. Plan usage limits — why they're not here

Your Claude settings panel shows things like:

```
Max (5x) — Current session: 9% used, resets in 2h 58m
Weekly — All models: 28% used  |  Fable: 46% used
```

**None of that is available to this stack.** Verified three ways:

1. **Not in the metrics.** The complete exported set is `session.count`,
   `lines_of_code.count`, `pull_request.count`, `commit.count`, `cost.usage`,
   `token.usage`, `code_edit_tool.decision`, `active_time.total`. No quota, no
   rate limit, no plan tier.
2. **Not in the events** either — no quota or limit event type exists.
3. **Not stored locally.** Session transcripts under `~/.claude/projects/`
   contain no quota fields (only `usage.service_tier: standard`), and there is
   no usage/limit/quota cache file under `~/.claude/`.

The `/usage` panel queries Anthropic live at display time; the number is never
persisted or exported, so there's nothing for the collector to scrape.

### Can't you correlate it?

Not reliably. `claude_code_cost_usage` is **API-equivalent pricing** — what the
same work would cost on the pay-as-you-go API. Plan limits are consumption
against a subscription quota, and the conversion between them isn't published.
Any percentage this dashboard displayed would be invented.

### What you can do instead

**Treat token volume as a relative early-warning signal.** Plan consumption
tracks tokens, so a spike in tokens-per-hour means you're approaching the
window limit faster than usual — even though you can't convert that to a
percentage. The *shape* transfers; the *scale* doesn't.

If you want a real gauge, the DIY route is to note your observed `/usage`
percentage at a few points alongside the token counter for the same window,
then fit a local scale factor. That gives you a personal, empirical
tokens-to-quota conversion. It's manual, it's specific to your plan, and it
would break whenever the plan changes — but it's the only honest way to get a
percentage onto this dashboard.

### Should the project link to them?

For the "am I about to get rate limited?" question — no, and it can't. Check
`/usage` in Claude Code for that; it's authoritative and live.

This dashboard answers a different and complementary question: **where does my
consumption go, and is it efficient?** `/usage` tells you *how much runway is
left*. This tells you *what's burning it*. Improving the ratios in §3 is what
makes the runway last longer, and that's the loop worth closing.
