# Optimization playbook

Actions to take from what the dashboard shows, ranked by what would actually
save money **on this machine's measured data** — not generic advice.

> **Correction to earlier guidance.** The metrics guide says the biggest lever
> is fewer, longer sessions. Measured on real data that is **wrong**, or at
> least incomplete: cache *writes* are only 19.6% of cost while cache *reads*
> are 61.2%. Long sessions do reduce writes, but they grow context, and context
> is what reads scale with. §1 explains the real optimum.

---

## 0. Where the money actually goes

| type | cost | share | what drives it |
| --- | ---: | ---: | --- |
| `cacheRead` | $128.41 | **61.2%** | context size × number of turns |
| `cacheCreation` | $41.13 | 19.6% | number of sessions × context size |
| `output` | $40.13 | 19.1% | response length + thinking (effort) |
| `input` | $0.13 | 0.1% | negligible — ignore |

**Nearly two thirds of spend is re-reading context you already sent.** Every
lever below is ranked against that.

---

## 1. Keep context small — the dominant lever (61% of cost)

Each turn re-reads the whole conversation prefix. Cost per turn is
*linear in context size*:

| context | cost per turn | 100 turns |
| ---: | ---: | ---: |
| 25,000 | $0.0125 | $1.25 |
| 100,000 | $0.0500 | $5.00 |
| 200,000 | $0.1000 | $10.00 |
| 600,000 | $0.3000 | $30.00 |

Measured here: **median 99,448 tokens per turn** ($0.05), **p90 586,505**
($0.29), max 694,685. And context **grows 4.8× within a session** — first
quarter of turns averages 40,215 tokens, last quarter 192,558.

That growth is the whole problem: late turns cost ~5× what early turns cost,
for the same work.

### Actions

| do | why |
| --- | --- |
| **`/compact` when the session gets long** | Cuts the prefix every later turn pays for. See the arithmetic below. |
| **Don't read files you don't need** | A file read once is re-read on *every* subsequent turn. A 20k-token file over 100 remaining turns costs $1.00, not $0.01. |
| **Prefer targeted reads** (grep, specific line ranges) over whole files | Same reason. |
| **Avoid dumping large command output** into the conversation | `| head`, `| wc -l`, redirect to a file — output you paste is context you rent for the rest of the session. |
| **Finish a topic, then start fresh** | A new session resets context to ~0 instead of carrying an irrelevant 200k prefix. |

### When compacting pays

Compacting costs one cache rewrite (2× input) and saves on every later read:

```
context 200,000, 50 turns remaining     -> $5.00 in reads
compact to 60,000: rewrite ~$0.60, save $3.50   -> net +$2.90
compact to 100,000: rewrite ~$1.00, save $2.50  -> net +$1.50
```

**Rule of thumb: if context is above ~150k and you have more than ~20 turns of
work left, compacting pays.** Below that, don't bother — the rewrite costs more
than it saves.

---

## 2. Session hygiene — second lever (19.6% of cost)

Every new session pays to rebuild the cache from scratch (2× input rate). This
is where *Cache amortization* comes in: `cacheRead / cacheCreation`, break-even
at 2.11×.

Measured here: **min 2.3×, median 14.6×, max 73.8×** — with one-shot
`claude -p` runs at 3–5× and long interactive sessions at 20–74×. And
`claude-telemetry` has **9 sessions**, the most fragmented project.

### Actions

| do | why |
| --- | --- |
| **`claude --continue`** instead of relaunching for the same task | Reuses the warm cache instead of paying to rebuild it. |
| **Don't restart just to "clear your head"** | That's a $0.30–$1.00 decision, not a free one. |
| **Batch one-off questions** into an existing session | A `claude -p` one-shot amortizes at 3–5×, barely above break-even. |
| **But don't chain forever** — see §1 | Past ~150k context, the read cost of continuing exceeds the write cost of starting fresh. |

**The optimum is a middle:** long enough to amortize the cache write, short
enough that context stays lean. In practice: continue within a task, compact or
restart between tasks.

---

## 3. Output and effort — third lever (19.1% of cost)

Output is the 5× lane, and **extended thinking is billed as output**. Measured
here: generation intensity (`output / cacheRead`) median **1.00%**, p75 1.97%,
max 12.52%.

### Actions

| do | why |
| --- | --- |
| **Match effort to task difficulty** | Higher effort means more thinking tokens at 5× input. Reserve high effort for genuinely hard problems. |
| **Ask for targeted edits, not whole-file rewrites** | A rewritten 500-line file is 500 lines of output tokens; a 5-line edit is 5. |
| **Don't ask for summaries you won't read** | Recaps, explanations and "walk me through it" are billed at the most expensive rate. |
| **Say when you want brevity** | Response length responds to instructions. |

---

## 4. Model choice — currently unused (99.9% Opus)

| model | cost | share |
| --- | ---: | ---: |
| claude-opus-4-8 | $209.54 | 99.9% |
| claude-haiku-4-5 | $0.00 | 0.0% |

Haiku is **5× cheaper than Opus in every lane** ($1/$5 vs $5/$25 per MTok,
and $0.10 vs $0.50 for cache reads).

### Actions

- Opus is the right default for real engineering work — don't downgrade
  reflexively; a cheaper model that needs three attempts costs more.
- But for **mechanical, well-specified tasks** — bulk renames, formatting,
  simple extraction, log grepping — Haiku does the job at a fifth of the price.
- On a subscription this doesn't change your bill, but it *does* consume less
  of your rate-limit window (§5).

---

## 5. Staying inside the rate-limit window

Claude's session limit resets on a **5-hour** cycle. The *Rolling 5h token
usage* panel tracks the same window. It is **not** a quota percentage — that
number exists only in `/usage` — but the shape is real.

Every lever above reduces window pressure too: less context re-read, fewer
cache rebuilds, and cheaper models all consume less of the window. **Context
size is again the biggest factor**, since it multiplies against every turn.

If you're being throttled, §1 is the first thing to attack.

---

## 6. What to check, and how often

| when | look at | act if |
| --- | --- | --- |
| During a long session | context growth | past ~150k with lots of work left → `/compact` |
| End of day | *Cache amortization* | below 9× → too many short sessions |
| End of day | *Cache write % of cost* | above 50% → same |
| Weekly | *Per-session efficiency* table | find the worst session, ask what was different |
| Weekly | *Cost by project* | is the expensive project the valuable one? |
| Monthly | recalibrate thresholds | re-run the percentile query; your baseline moves |

### Don't optimize these

- **`input`** — 0.1% of cost. Ignore it entirely.
- **`cacheCreation` down to zero** — writes buy the cheap reads. The goal is a
  good read/write ratio, not fewer writes.
- **Cache hit rate (SigNoz)** — pinned at ~100% here; it can't move.

---

## 7. Honest limits

- All costs are **API-equivalent**. On a subscription your marginal cost is
  zero; these numbers are for *comparison and rate-limit pressure*, not budget.
- The playbook is built on ~19 hours of data from one machine. Re-check the
  percentages in §0 before trusting the ranking — if your `output` share is
  much higher, §3 outranks §1 for you.
- Effort level and context size aren't currently on the dashboard. §1's
  guidance comes from transcript analysis, not a panel — a "context size per
  turn" panel would need per-message data Prometheus doesn't hold.
