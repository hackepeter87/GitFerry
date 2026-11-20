#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
compose_file="$root/tests/integration/compose.yaml"
project=${GITFERRY_IT_PROJECT:-}
export GITFERRY_IT_USER_PASSWORD=${GITFERRY_IT_USER_PASSWORD:-}
export GITFERRY_IT_INSTANCE_NONCE=${GITFERRY_IT_INSTANCE_NONCE:-}

case "$project" in
  gitferry-it-*) ;;
  *) echo "refusing diagnostics outside gitferry-it-* scope: $project" >&2; exit 1 ;;
esac

results="$root/integration-results"
if [ -d "$root/integration-failure-results" ] && [ ! -d "$results" ]; then
  results="$root/integration-failure-results"
fi
mkdir -p "$results/workflow-diagnostics"
printf '%s\n' "$project" >"$results/workflow-diagnostics/project-name"

status=0
docker compose -p "$project" -f "$compose_file" ps --all --format json \
  >"$results/workflow-diagnostics/compose-ps.json" 2>&1 || status=$?
docker compose -p "$project" -f "$compose_file" images --format json \
  >"$results/workflow-diagnostics/compose-images.json" 2>&1 || status=$?
docker compose -p "$project" -f "$compose_file" logs --no-color \
  >"$results/workflow-diagnostics/compose.log" 2>&1 || status=$?

exit "$status"
