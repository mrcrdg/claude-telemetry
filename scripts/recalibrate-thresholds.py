#!/usr/bin/env python3
"""Recompute the dashboard's green/yellow bands from your own history.

The scorecard bands are percentiles of your own sessions, not an industry
norm — no published baseline for Claude Code efficiency exists. That means
they go stale: bands fitted to 14 sessions are the wrong bands at 50, and a
number frozen into a panel description is wrong the moment you work again.

This reads the current distribution from Prometheus and writes the bands back
into the dashboard JSON, so the thresholds track how you actually work.

    python3 scripts/recalibrate-thresholds.py --dry-run
    python3 scripts/recalibrate-thresholds.py

One band is deliberately NOT recalculated: cache amortization's red boundary
stays at 2x. That one is arithmetic from published pricing — cache writes cost
2x input, reads 0.1x, so below ~2.11 reuses caching costs more than sending
fresh. It is not a matter of how you happen to work.
"""
import argparse, json, statistics as st, sys, urllib.parse, urllib.request

PROM = "http://127.0.0.1:9090"
WINDOW = "30d"
DASHBOARD = "grafana/dashboards/claude-code-usage.json"


def per_session(expr):
    url = f"{PROM}/api/v1/query?query={urllib.parse.quote(expr)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)["data"]["result"]
    vals = []
    for x in res:
        try:
            v = float(x["value"][1])
        except (ValueError, KeyError):
            continue
        if v == v and v not in (float("inf"), float("-inf")):   # drop NaN/inf
            vals.append(v)
    return sorted(vals)


def pct(vals, p):
    if len(vals) < 4:
        return None
    return st.quantiles(vals, n=100)[p - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dashboard", default=DASHBOARD)
    args = ap.parse_args()

    amort = per_session(
        f'sum by (session_id) (max_over_time(claude_code_token_usage'
        f'{{type="cacheRead"}}[{WINDOW}])) / sum by (session_id) '
        f'(max_over_time(claude_code_token_usage{{type="cacheCreation"}}[{WINDOW}]))')
    genint = per_session(
        f'sum by (session_id) (max_over_time(claude_code_token_usage'
        f'{{type="output"}}[{WINDOW}])) / sum by (session_id) '
        f'(max_over_time(claude_code_token_usage{{type="cacheRead"}}[{WINDOW}])) * 100')

    n = len(amort)
    if n < 8:
        print(f"Only {n} sessions — too few to fit percentiles. "
              f"Come back after ~20 and the bands will mean something.")
        return 0

    a25, a50 = pct(amort, 25), pct(amort, 50)
    g50, g75 = pct(genint, 50), pct(genint, 75)

    print(f"Baseline from {n} sessions ({WINDOW} window)\n")
    print(f"  amortization        p25 {a25:6.1f}x   median {a50:6.1f}x   "
          f"max {max(amort):6.1f}x")
    print(f"  generation intensity median {g50:5.2f}%   p75 {g75:5.2f}%   "
          f"max {max(genint):5.2f}%")
    print()
    print("  new bands:")
    print(f"    Cache amortization    red <2 (fixed, arithmetic) · "
          f"orange <{a25:.0f} · yellow <{a50:.0f} · green >={a50:.0f}")
    print(f"    Generation intensity  green <{g75:.1f} · yellow <5 · red >=5")

    if args.dry_run:
        print("\n  --dry-run: dashboard not modified")
        return 0

    d = json.load(open(args.dashboard))
    changed = []
    for p in d["panels"]:
        if p.get("title") == "Cache amortization":
            p["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
                {"color": "red", "value": None},
                {"color": "orange", "value": 2},
                {"color": "yellow", "value": round(a25)},
                {"color": "green", "value": round(a50)}]
            changed.append(p["title"])
        elif p.get("title") == "Generation intensity":
            p["fieldConfig"]["defaults"]["thresholds"]["steps"] = [
                {"color": "green", "value": None},
                {"color": "yellow", "value": round(g75, 1)},
                {"color": "red", "value": 5}]
            changed.append(p["title"])
        elif p.get("title") == "Per-session efficiency":
            for ov in p["fieldConfig"]["overrides"]:
                if ov["matcher"]["options"] == "Amortization":
                    for pr in ov["properties"]:
                        if pr["id"] == "thresholds":
                            pr["value"]["steps"] = [
                                {"color": "red", "value": None},
                                {"color": "orange", "value": 2},
                                {"color": "yellow", "value": round(a25)},
                                {"color": "green", "value": round(a50)}]
            changed.append(p["title"])
    d["version"] = d.get("version", 1) + 1
    json.dump(d, open(args.dashboard, "w"), indent=2)
    open(args.dashboard, "a").write("\n")
    print(f"\n  updated: {', '.join(changed)}")
    print("  Grafana reloads provisioned dashboards within ~30s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
