#!/usr/bin/env python3
"""Recover token history from Claude Code transcripts into Prometheus.

Telemetry is only exported by sessions that had the OTel env vars set at
launch. Everything else is lost to Prometheus — but not actually lost: every
session's transcript under ~/.claude/projects/ records per-message token
counts, so the history can be reconstructed after the fact.

    {"input_tokens": 2, "cache_creation_input_tokens": 5973,
     "cache_read_input_tokens": 21760, "output_tokens": 37, ...}

This reads those transcripts and emits an OpenMetrics file that
`promtool tsdb create-blocks-from openmetrics` turns into Prometheus blocks.

Usage:
    python3 scripts/backfill-from-transcripts.py --dry-run     # report only
    python3 scripts/backfill-from-transcripts.py -o /tmp/backfill.om
    # then, to load it (see --help-load for the exact commands):
    #   docker cp /tmp/backfill.om claude-prometheus:/tmp/
    #   docker exec claude-prometheus promtool tsdb create-blocks-from \\
    #       openmetrics /tmp/backfill.om /prometheus/backfill
    #   docker compose stop prometheus
    #   docker run --rm -v claude-telemetry_prometheus-data:/prometheus alpine \\
    #       sh -c 'mv /prometheus/backfill/* /prometheus/ && rmdir /prometheus/backfill'
    #   docker compose start prometheus

Safety: samples newer than --cutoff-hours (default 3) are skipped. Blocks that
overlap Prometheus's in-memory head cause the head to be truncated on restart,
which destroys recent samples that hadn't been flushed to disk yet.

Lowering the cutoff to pick up recent work therefore costs you whatever is
still only in the WAL — in practice the newest one or two sessions. Two things
make that acceptable:

  1. Back up the volume first, so the trade is reversible:
       docker run --rm -v claude-telemetry_prometheus-data:/data \
         -v "$PWD":/backup alpine tar czf /backup/prom.tgz -C /data .
  2. Re-run this script afterwards. Transcripts are the source of truth, so a
     second pass re-imports whatever the first pass dropped. Each round the
     dropped set shrinks, so it converges.

Emitted values are cumulative rather than deltas, which is what makes re-runs
safe: rewriting a sample Prometheus already has restates the same total instead
of adding to it.
"""
import argparse, glob, json, os, sys, time, urllib.parse, urllib.request
from collections import defaultdict

# USD per token. Must match prometheus/rules/claude-cost.yml — recording rules
# are forward-only and never apply to backfilled samples, so cost is computed
# here instead. cacheCreation is 2x input: Claude Code uses the 1-hour cache
# TTL exclusively (verified via .message.usage.cache_creation in transcripts).
RATES = {
    "claude-opus":   {"input": 5e-6, "output": 25e-6, "cacheCreation": 10e-6, "cacheRead": 0.5e-6},
    "claude-sonnet": {"input": 3e-6, "output": 15e-6, "cacheCreation":  6e-6, "cacheRead": 0.3e-6},
    "claude-haiku":  {"input": 1e-6, "output":  5e-6, "cacheCreation":  2e-6, "cacheRead": 0.1e-6},
}
USAGE_FIELDS = {                    # transcript field -> metric `type` label
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_creation_input_tokens": "cacheCreation",
    "cache_read_input_tokens": "cacheRead",
}


def rates_for(model):
    for prefix, r in RATES.items():
        if model.startswith(prefix):
            return r
    return None


def sanitize(name):
    """Match claude-env.sh: anything outside [A-Za-z0-9._-] becomes '_'."""
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in name)


def existing_watermarks(prom_url):
    """Latest sample timestamp Prometheus already holds, per session.

    Per-SESSION dedupe would be wrong: a session partially imported by an
    earlier run could never be topped up, because its id already exists. We
    instead record how far each session got, and later emit only samples past
    that point. Cumulative totals are still computed from the session's first
    message, so the counter continues from the stored value rather than
    restarting at zero.
    """
    # An instant query only sees the last few minutes, so finished sessions
    # would report no watermark and be re-imported wholesale. Walk a range
    # instead and take each session's last populated step.
    #
    # Step granularity makes this approximate, which is safe in one direction
    # only -- and it is the safe one. Emitted values are CUMULATIVE, so
    # re-sending a sample Prometheus already has just rewrites the same total;
    # it cannot double-count the way re-sending a delta would. Missing data is
    # the real failure, so round the watermark down.
    step = 300
    end = int(time.time())
    start = end - 30 * 86400
    q = "max by (session_id) (claude_code_token_usage)"
    url = (f"{prom_url}/api/v1/query_range?query={urllib.parse.quote(q)}"
           f"&start={start}&end={end}&step={step}")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            res = json.load(r)["data"]["result"]
        return {x["metric"]["session_id"]: max(v[0] for v in x["values"]) - step
                for x in res if x.get("values")}
    except Exception as e:
        print(f"  ! could not reach Prometheus at {prom_url} ({e});"
              f" proceeding without dedupe", file=sys.stderr)
        return {}


def collect(transcript_glob, cutoff_ts, watermarks):
    """-> samples[(metric, labels_tuple)] = [(ts, cumulative_value), ...]"""
    per_series = defaultdict(list)          # (session, project, model, type) -> [(ts, delta)]
    stats = defaultdict(int)
    for path in sorted(glob.glob(transcript_glob)):
        for line in open(path, errors="replace"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            stats["messages"] += 1
            sid = rec.get("sessionId") or rec.get("session_id")
            model = msg.get("model") or ""
            if not sid or not rates_for(model):
                stats["skipped_no_model_or_session"] += 1
                continue
            ts_iso = rec.get("timestamp")
            if not ts_iso:
                continue
            try:
                ts = time.mktime(time.strptime(ts_iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            except ValueError:
                continue
            if ts > cutoff_ts:
                stats["skipped_too_recent"] += 1
                continue
            # Kept for cumulative accounting even when already stored; the
            # emit step drops anything at or before the watermark.
            already = ts <= watermarks.get(sid, -1)
            if already:
                stats["already_in_prometheus"] += 1
            project = sanitize(os.path.basename(rec.get("cwd") or "")) or ""
            for field, type_ in USAGE_FIELDS.items():
                v = usage.get(field) or 0
                if v:
                    per_series[(sid, project, model, type_)].append((ts, v, already))
                    if not already:
                        stats["samples"] += 1
    return per_series, stats


def to_openmetrics(per_series):
    """Counters are cumulative per session, so accumulate deltas in time order.

    Samples already in Prometheus still advance the running total — they are
    just not re-emitted — so an appended sample carries the correct cumulative
    value instead of resetting the counter.
    """
    rows = []
    for (sid, project, model, type_), points in per_series.items():
        rate = rates_for(model)[type_]
        running = 0.0
        for ts, delta, already in sorted(points):
            running += delta
            if already:
                continue
            labels = (f'session_id="{sid}",project="{project}",'
                      f'model="{model}",type="{type_}"')
            rows.append((ts, "claude_code_token_usage", labels, running))
            rows.append((ts, "claude_code_token_cost_usd", labels, running * rate))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out = ["# TYPE claude_code_token_usage gauge",
           "# TYPE claude_code_token_cost_usd gauge"]
    for ts, metric, labels, val in rows:
        out.append(f"{metric}{{{labels}}} {val:.6f} {ts:.3f}")
    out.append("# EOF")
    return "\n".join(out) + "\n", len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcripts",
                    default=os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    ap.add_argument("--prometheus", default="http://127.0.0.1:9090")
    ap.add_argument("--cutoff-hours", type=float, default=3.0,
                    help="skip samples newer than this many hours (default 3)")
    ap.add_argument("-o", "--output", default="/tmp/claude-backfill.om")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-dedupe", action="store_true",
                    help="ignore watermarks and re-emit everything (double-counts)")
    args = ap.parse_args()

    marks = {} if args.no_dedupe else existing_watermarks(args.prometheus)
    if marks:
        print(f"  {len(marks)} session(s) already partly stored; only samples"
              f" newer than each one's watermark will be written")
    cutoff = time.time() - args.cutoff_hours * 3600

    per_series, stats = collect(args.transcripts, cutoff, marks)
    if not per_series:
        print("Nothing to backfill.")
        return 0

    sessions = {k[0] for k, pts in per_series.items() if any(not a for *_, a in pts)}
    projects = defaultdict(float)
    tokens = defaultdict(float)
    for (sid, project, model, type_), pts in per_series.items():
        total = sum(v for _, v, already in pts if not already)
        tokens[type_] += total
        projects[project] += total * rates_for(model)[type_]

    print(f"\n  messages scanned            {stats['messages']:>10,}")
    print(f"  samples to write            {stats['samples']:>10,}")
    print(f"  sessions recovered          {len(sessions):>10,}")
    for k in ("already_in_prometheus", "skipped_too_recent",
              "skipped_no_model_or_session"):
        if stats.get(k):
            print(f"  {k:<27} {stats[k]:>10,}")
    print("\n  tokens by type:")
    for t, v in sorted(tokens.items(), key=lambda x: -x[1]):
        print(f"    {t:<15} {v:>12,.0f}")
    print("\n  recovered cost by project:")
    for p, v in sorted(projects.items(), key=lambda x: -x[1]):
        print(f"    {p or '(no project)':<35} ${v:>8.4f}")
    print(f"\n  TOTAL recovered cost        ${sum(projects.values()):>9.4f}")

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    text, n = to_openmetrics(per_series)
    with open(args.output, "w") as f:
        f.write(text)
    print(f"\n  wrote {n:,} samples -> {args.output}")
    print("  next: see the load commands in this script's docstring")
    return 0


if __name__ == "__main__":
    sys.exit(main())
