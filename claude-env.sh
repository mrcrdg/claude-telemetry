# Source this before launching Claude Code so it exports OpenTelemetry
# metrics to the local collector:
#
#     source ./claude-env.sh
#     claude
#
# Metrics are tagged with a `project` label taken from the current directory
# name. Override it by setting CLAUDE_TELEMETRY_PROJECT first:
#
#     CLAUDE_TELEMETRY_PROJECT=my-app source ./claude-env.sh
#
# Unset again with:  source ./claude-env.sh --unset

if [ "$1" = "--unset" ]; then
  unset CLAUDE_CODE_ENABLE_TELEMETRY
  unset OTEL_METRICS_EXPORTER
  unset OTEL_EXPORTER_OTLP_PROTOCOL
  unset OTEL_EXPORTER_OTLP_ENDPOINT
  unset OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE
  unset OTEL_METRIC_EXPORT_INTERVAL
  unset OTEL_RESOURCE_ATTRIBUTES
  echo "Claude Code telemetry env vars unset."
  return 0 2>/dev/null || exit 0
fi

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Claude Code defaults to DELTA temporality; Prometheus (and the collector's
# prometheus exporter) expect cumulative counters. Force cumulative so
# increase()/rate() work correctly. This is the important one — without it,
# counters won't accumulate the way the dashboard expects.
export OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative

# Export every 10s instead of the 60s default, so panels update quickly
# while you're testing. Raise it (or drop this line) for normal use.
export OTEL_METRIC_EXPORT_INTERVAL=10000

# Claude Code does NOT emit cwd, project name or git branch on any metric (by
# design — unbounded cardinality). So tag it ourselves: keys listed in
# OTEL_RESOURCE_ATTRIBUTES are attached to every metric as data-point
# attributes, which the collector turns into Prometheus labels. This works
# regardless of `resource_to_telemetry_conversion` in the collector config —
# that setting only governs *resource*-level attributes.
#
# Values may not contain spaces or non-ASCII, so squash anything else to "_".
_claude_project="${CLAUDE_TELEMETRY_PROJECT:-$(basename "$PWD")}"
_claude_project="$(printf '%s' "$_claude_project" | tr -c 'A-Za-z0-9._-' '_')"
export OTEL_RESOURCE_ATTRIBUTES="project=${_claude_project}"
unset _claude_project

echo "Claude Code telemetry -> http://localhost:4317 (export every ${OTEL_METRIC_EXPORT_INTERVAL}ms, project=${OTEL_RESOURCE_ATTRIBUTES#project=})"
