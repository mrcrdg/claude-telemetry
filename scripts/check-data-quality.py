#!/usr/bin/env python3
"""Data-quality assertions for the telemetry pipeline.

Two ingestion paths write the same metrics — live OTLP export and the
transcript backfill — and a recording rule derives cost from tokens. Each of
those has silently produced wrong data at least once:

  * cache writes were priced at 1.25x for days before transcripts showed
    Claude Code uses the 1-hour TTL (2x). Data imported before the fix is
    still stored at the old rate.
  * backfilled rows carry no `query_source`, so Grafana's "All" expansion
    (`query_source=~"(main|auxiliary)"`) matched none of them and silently hid
    most of the history.

Both were found by eye. These checks find them automatically.

    python3 scripts/check-data-quality.py            # exits non-zero on failure

Why not Great Expectations / dbt tests: those validate tabular batches in a
warehouse. The data here lives in a TSDB and is queried with PromQL, which
neither speaks. The checks that matter are reconciliations between series, so
they're expressed as PromQL and plain assertions.
"""
import json, sys, urllib.parse, urllib.request

PROM = "http://127.0.0.1:9090"
# Keep in step with prometheus/rules/claude-cost.yml and
# scripts/backfill-from-transcripts.py.
RATES = {"input": 5e-6, "output": 25e-6, "cacheCreation": 10e-6, "cacheRead": 0.5e-6}
TOLERANCE = 0.05          # 5% — covers rounding and model-variant pricing


def q(expr):
    url = f"{PROM}/api/v1/query?query={urllib.parse.quote(expr)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["data"]["result"]


def by(expr, label):
    return {x["metric"].get(label, ""): float(x["value"][1]) for x in q(expr)}


results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


print("Data quality checks\n")

# 1. Cost reconciliation against Claude Code's own metric.
#    claude_code_cost_usage is emitted by Claude Code itself, so it is
#    independent ground truth for our derived claude_code_token_cost_usd.
#    Only live sessions have it; backfilled ones are skipped.
theirs = by('sum by (session_id) (max_over_time(claude_code_cost_usage[30d]))', "session_id")
ours = by('sum by (session_id) (max_over_time(claude_code_token_cost_usd[30d]))', "session_id")
bad = []
for sid, ref in theirs.items():
    got = ours.get(sid)
    if got is None or ref == 0:
        continue
    diff = abs(got - ref) / ref
    if diff > TOLERANCE:
        bad.append(f"{sid[:8]}: Claude Code ${ref:.4f} vs ours ${got:.4f} ({(got-ref)/ref*+100:+.1f}%)")
check("derived cost matches Claude Code's own cost metric", not bad,
      "\n".join(bad) + ("\n-> rates in claude-cost.yml may be wrong, or this data was\n"
                        "   imported before a rate change and needs re-importing" if bad else ""))

# 2. Every token series should have a matching cost series. A model absent
#    from the rate table produces tokens with no cost and vanishes from every
#    cost panel without erroring.
tok_models = set(by('count by (model) (max_over_time(claude_code_token_usage[30d]))', "model"))
cost_models = set(by('count by (model) (max_over_time(claude_code_token_cost_usd[30d]))', "model"))
missing = tok_models - cost_models
check("every model with tokens also has cost", not missing,
      f"unpriced models: {sorted(missing)}\n-> add them to prometheus/rules/claude-cost.yml" if missing else "")

# 3. Stored cost must equal tokens x current rates. Catches data written
#    before a rate change — the recording rule never revisits old samples.
stale = []
for t, rate in RATES.items():
    tk = q(f'sum(max_over_time(claude_code_token_usage{{type="{t}"}}[30d]))')
    ct = q(f'sum(max_over_time(claude_code_token_cost_usd{{type="{t}"}}[30d]))')
    if not tk or not ct:
        continue
    expected = float(tk[0]["value"][1]) * rate
    actual = float(ct[0]["value"][1])
    if expected and abs(actual - expected) / expected > TOLERANCE:
        stale.append(f"{t}: stored ${actual:.4f}, expected ${expected:.4f} "
                     f"({(actual-expected)/expected*100:+.1f}%)")
check("stored cost matches tokens x current rates", not stale,
      "\n".join(stale) + "\n-> re-import affected sessions; rates changed after they were written"
      if stale else "")

# 4. Label completeness across ingestion paths. Backfilled rows lack labels
#    that live rows have; that is expected, but Grafana's "All" expansion
#    silently drops them unless allValue is ".*", so surface the count.
for label in ("query_source", "project"):
    total = q('count(count by (session_id) (max_over_time(claude_code_token_usage[30d])))')
    withl = q(f'count(count by (session_id) '
              f'(max_over_time(claude_code_token_usage{{{label}!=""}}[30d])))')
    n = int(float(total[0]["value"][1])) if total else 0
    m = int(float(withl[0]["value"][1])) if withl else 0
    check(f"sessions carrying a `{label}` label", True,
          f"{m}/{n} sessions — the rest are backfilled and lack it by design.\n"
          f"Template variables must use allValue='.*' or these vanish from 'All'."
          if m < n else f"{m}/{n}")

# 5. Freshness — is anything arriving at all?
fresh = q('count(claude_code_token_usage)')
check("metrics arriving in the last 5 minutes", bool(fresh),
      "no live series; expected if no telemetry-enabled session is running" if not fresh else "")

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("failed: " + ", ".join(failed))
sys.exit(1 if failed else 0)
