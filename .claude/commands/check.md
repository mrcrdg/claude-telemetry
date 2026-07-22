---
description: Run data-quality checks and report what failed and why
---

Run `python3 scripts/check-data-quality.py` and interpret the result.

The load-bearing assertion is the first: Claude Code emits its own
`claude_code_cost_usage`, so our derived `claude_code_token_cost_usd` is
checked against independent ground truth. If it fails, the usual causes are:

- **rates changed after data was written** — recording rules only ever
  evaluate live scrapes and never revisit old samples, so the fix is to delete
  and re-import the affected sessions
- **a session counted twice** — it exists on both the live and backfill paths,
  which produce different label sets and therefore separate series
- **the rate table is genuinely wrong** — check
  `prometheus/rules/claude-cost.yml` against current published pricing

Do not "fix" a failure by widening `TOLERANCE`. Find the cause.

Report pass/fail per check, and for any failure state the specific sessions or
metrics involved rather than just the assertion name.
