# Source this before launching Claude Code so its metrics are tagged with a
# project name. Run it once per terminal, from anywhere:
#
#     source ~/Documents/studies_tests/claude-telemetry/claude-env.sh my-project
#     claude
#
# The project name is optional — omit it and the current directory name is
# used instead:
#
#     source ~/.../claude-env.sh          # project = name of the current dir
#
# It must be `source`d, not executed: a script run normally sets variables in
# its own process, which exits immediately, leaving your shell unchanged.
#
# Unset again with:  source ~/.../claude-env.sh --unset

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
# Name resolution, first match wins:
#   1. the argument you passed    source claude-env.sh my-project
#   2. $CLAUDE_TELEMETRY_PROJECT  CLAUDE_TELEMETRY_PROJECT=my-project source ...
#   3. the enclosing git repository (a worktree resolves to its main repo)
#   4. the current directory name
#
# Step 3 matters: plain basename is wrong as soon as you launch Claude from a
# subdirectory, turning <project>/docs into a project called "docs". For a
# non-git project, pass the name explicitly as the argument.
#
# Values may not contain spaces or non-ASCII, so squash anything else to "_".
_claude_project="${1:-$CLAUDE_TELEMETRY_PROJECT}"
if [ -z "$_claude_project" ]; then
  _claude_git="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -n "$_claude_git" ]; then
    _claude_project="$(basename "$(dirname "$_claude_git")")"
  else
    _claude_project="$(basename "$PWD")"
  fi
  unset _claude_git
fi
_claude_project="$(printf '%s' "$_claude_project" | tr -c 'A-Za-z0-9._-' '_')"
export OTEL_RESOURCE_ATTRIBUTES="project=${_claude_project}"
unset _claude_project

echo "Claude Code telemetry -> http://localhost:4317 (export every ${OTEL_METRIC_EXPORT_INTERVAL}ms, project=${OTEL_RESOURCE_ATTRIBUTES#project=})"
