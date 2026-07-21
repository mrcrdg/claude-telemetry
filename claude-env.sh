# Source this before launching Claude Code so it exports OpenTelemetry
# metrics to the local collector:
#
#     source ./claude-env.sh
#     claude
#
# Unset again with:  source ./claude-env.sh --unset

if [ "$1" = "--unset" ]; then
  unset CLAUDE_CODE_ENABLE_TELEMETRY
  unset OTEL_METRICS_EXPORTER
  unset OTEL_EXPORTER_OTLP_PROTOCOL
  unset OTEL_EXPORTER_OTLP_ENDPOINT
  unset OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE
  unset OTEL_METRIC_EXPORT_INTERVAL
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

echo "Claude Code telemetry -> http://localhost:4317 (export every ${OTEL_METRIC_EXPORT_INTERVAL}ms)"
