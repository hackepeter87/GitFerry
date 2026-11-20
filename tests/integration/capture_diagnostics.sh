#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
compose_file="$root/tests/integration/compose.yaml"
results_root=${GITFERRY_IT_RESULTS_ROOT:-$root}
export GITFERRY_IT_USER_PASSWORD=${GITFERRY_IT_USER_PASSWORD:-}
export GITFERRY_IT_INSTANCE_NONCE=${GITFERRY_IT_INSTANCE_NONCE:-}
seen_projects=
status=0
found=false

capture_project() {
  project=$1
  results=$2
  case "$project" in
    gitferry-it-*) ;;
    *) echo "refusing diagnostics outside gitferry-it-* scope: $project" >&2; exit 1 ;;
  esac
  case "$project" in
    *[!a-zA-Z0-9_-]*) echo "invalid integration project name: $project" >&2; exit 1 ;;
  esac
  case "|$seen_projects|" in
    *"|$project|"*) return ;;
  esac
  seen_projects="${seen_projects:+$seen_projects|}$project"
  found=true

  mkdir -p "$results/workflow-diagnostics"
  printf '%s\n' "$project" >"$results/workflow-diagnostics/project-name"
  docker compose -p "$project" -f "$compose_file" ps --all --format json \
    >"$results/workflow-diagnostics/compose-ps.json" 2>&1 || status=$?
  docker compose -p "$project" -f "$compose_file" images --format json \
    >"$results/workflow-diagnostics/compose-images.json" 2>&1 || status=$?
  docker compose -p "$project" -f "$compose_file" logs --no-color \
    >"$results/workflow-diagnostics/compose.log" 2>&1 || status=$?
}

for results in "$results_root/integration-results" "$results_root/integration-failure-results"; do
  if [ -f "$results/project-name" ]; then
    capture_project "$(sed -n '1p' "$results/project-name")" "$results"
  fi
done
if [ -n "${GITFERRY_IT_PROJECT:-}" ]; then
  capture_project "$GITFERRY_IT_PROJECT" "$results_root/integration-results"
fi
if [ "$found" = false ]; then
  echo "no run-scoped integration project available for diagnostics" >&2
  exit 1
fi

exit "$status"
