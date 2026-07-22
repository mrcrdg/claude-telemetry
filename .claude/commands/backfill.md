---
description: Recover untracked sessions from Claude Code transcripts into Prometheus
---

Run the transcript backfill. This is a multi-step, stateful procedure with a
known hazard, so follow it in order rather than improvising.

1. **Preview first.** `python3 scripts/backfill-from-transcripts.py --dry-run`
   Report what would be recovered — sessions, tokens, cost per project.

2. **Back up the TSDB** before touching it:
   ```bash
   docker run --rm -v claude-telemetry_prometheus-data:/data \
     -v "$PWD":/backup alpine tar czf /backup/prom-backup.tgz -C /data .
   ```

3. **Record the before-state** so loss is detectable: session count and total
   tokens from `claude_code_token_usage`.

4. **Generate and load:**
   ```bash
   python3 scripts/backfill-from-transcripts.py -o /tmp/backfill.om
   docker cp /tmp/backfill.om claude-prometheus:/tmp/
   docker exec claude-prometheus promtool tsdb create-blocks-from openmetrics \
     /tmp/backfill.om /prometheus/backfill
   docker compose stop prometheus
   docker run --rm -v claude-telemetry_prometheus-data:/prometheus alpine \
     sh -c 'mv /prometheus/backfill/* /prometheus/ && rmdir /prometheus/backfill'
   docker compose start prometheus
   ```

5. **Verify** against step 3, then run `/check`. If sessions were lost, say so
   explicitly and offer the backup.

**Hazards — do not skip:**

- `--cutoff-hours` defaults to 3. Lowering it recovers more but blocks that
  overlap Prometheus's in-memory head truncate it on restart, dropping samples
  still in the WAL. That has already cost real data twice.
- Re-running is safe (values are cumulative, not deltas) and is the remedy for
  anything a previous run dropped.
- The script skips sessions covered by live telemetry entirely. Do not
  "improve" this into a watermark top-up: live and backfilled rows carry
  different labels, so they form separate series that sum rather than merge.
