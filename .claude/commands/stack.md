---
description: Health-check the observability stack end to end
---

Check the pipeline is actually working, in ingestion order, and report a
one-line verdict per hop:

1. **Containers** — `docker compose ps`; all four of collector, prometheus,
   loki, grafana should be up.
2. **Collector** — `curl -s localhost:8889/metrics | grep -c '^claude_code'`
   confirms metrics are being exposed for scraping.
3. **Prometheus** — target health via `/api/v1/targets`, then whether any
   series arrived in the last 5 minutes.
4. **Loki** — `curl -s localhost:3100/ready`, then whether any events landed.
5. **Grafana** — datasources registered (`/api/v1/datasources`, expect both
   Prometheus and Loki) and the dashboard loaded.

Then report current headline numbers: total cost, sessions, cache
amortization, cache write % of cost.

**If a session is not being recorded**, the cause is almost always that it
started before telemetry was enabled — environment is read once at process
start. Check with:

```bash
for pid in $(pgrep -f '^claude'); do
  tr '\0' '\n' < /proc/$pid/environ | grep -q 'CLAUDE_CODE_ENABLE_TELEMETRY=1' \
    && echo "$pid on" || echo "$pid OFF"
done
```

The fix is to restart that session with `claude --continue`, not to change
anything in the stack.
