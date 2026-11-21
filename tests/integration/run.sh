#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
compose_file="$root/tests/integration/compose.yaml"
python="$root/.venv/bin/python"
results_setting=${GITFERRY_IT_RESULTS_DIR:-$root/integration-results}
case "$results_setting" in
  /*) results=$results_setting ;;
  *) results="$root/$results_setting" ;;
esac
case "$results" in
  "$root/integration-results"|"$root/integration-failure-results") ;;
  *) echo "unsafe integration results directory: $results" >&2; exit 1 ;;
esac
run_id=${GITFERRY_IT_RUN_ID:-$($python -c 'import uuid; print(uuid.uuid4().hex[:16])')}
safe_run_id=$(printf '%s' "$run_id" | tr -c 'a-zA-Z0-9_-' '-' | cut -c1-40)
project=${GITFERRY_IT_PROJECT:-gitferry-it-$safe_run_id}
runtime_image=${GITFERRY_IT_RUNTIME_IMAGE:-gitferry-it-runtime:$safe_run_id}
phase=initializing
diagnostics_captured=false

case "$project" in
  gitferry-it-*) ;;
  *) echo "unsafe Compose project name: $project" >&2; exit 1 ;;
esac
case "$runtime_image" in
  gitferry-it-runtime:*) ;;
  *) echo "unsafe integration runtime image name: $runtime_image" >&2; exit 1 ;;
esac

capture_diagnostics() {
  mkdir -p "$results/diagnostics"
  docker compose -p "$project" -f "$compose_file" ps --all --format json \
    >"$results/diagnostics/compose-ps.json" 2>&1 || true
  docker compose -p "$project" -f "$compose_file" images --format json \
    >"$results/diagnostics/compose-images.json" 2>&1 || true
  docker compose -p "$project" -f "$compose_file" logs --no-color \
    >"$results/diagnostics/compose.log" 2>&1 || true
  diagnostics_captured=true
}

wait_for_service_state() {
  service=$1
  expected=$2
  timeout_seconds=$3
  started_at=$(date +%s)
  while :; do
    container_id=$(docker compose -p "$project" -f "$compose_file" ps -q "$service" 2>/dev/null || true)
    if [ -n "$container_id" ]; then
      container_state=$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)
      if [ "$expected" = healthy ]; then
        observed_state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id" 2>/dev/null || true)
      else
        observed_state=$container_state
      fi
      if [ "$observed_state" = "$expected" ]; then
        return 0
      fi
      case "$container_state" in
        dead|exited)
          echo "service $service stopped before reaching $expected" >&2
          return 1
          ;;
      esac
    fi
    now=$(date +%s)
    if [ $((now - started_at)) -ge "$timeout_seconds" ]; then
      echo "service $service did not reach $expected within ${timeout_seconds}s" >&2
      return 1
    fi
    sleep 1
  done
}

finish() {
  run_status=$?
  trap - EXIT HUP INT TERM
  set +e
  if [ "$run_status" -eq 0 ]; then
    "$python" "$root/tests/integration/report_contract.py" check --results "$results"
    contract_status=$?
    if [ "$contract_status" -ne 0 ]; then
      run_status=65
    fi
  fi
  if [ "$run_status" -ne 0 ]; then
    capture_diagnostics
  fi
  scoped_cleanup_status=0
  resource_label="gitferry.integration.project=$project"
  for container in $(docker ps --all --quiet --filter "label=$resource_label"); do
    docker rm --force "$container" >>"$results/work/cleanup.log" 2>&1 \
      || scoped_cleanup_status=$?
  done
  for volume in $(docker volume ls --quiet --filter "label=$resource_label"); do
    docker volume rm --force "$volume" >>"$results/work/cleanup.log" 2>&1 \
      || scoped_cleanup_status=$?
  done
  docker compose -p "$project" -f "$compose_file" down --volumes --remove-orphans \
    >>"$results/work/cleanup.log" 2>&1
  cleanup_status=$?
  if [ "$cleanup_status" -eq 0 ] && [ "$scoped_cleanup_status" -ne 0 ]; then
    cleanup_status=$scoped_cleanup_status
  fi
  if docker image inspect "$runtime_image" >/dev/null 2>&1; then
    docker image rm "$runtime_image" >>"$results/work/cleanup.log" 2>&1
    image_cleanup_status=$?
    if [ "$cleanup_status" -eq 0 ] && [ "$image_cleanup_status" -ne 0 ]; then
      cleanup_status=$image_cleanup_status
    fi
  fi
  finalize_args=""
  if [ "$diagnostics_captured" = true ]; then
    finalize_args="--diagnostics-captured"
  fi
  # shellcheck disable=SC2086
  "$python" "$root/tests/integration/report_contract.py" finalize \
    --results "$results" --run-status "$run_status" --cleanup-status "$cleanup_status" \
    --phase "$phase" $finalize_args
  report_status=$?
  final_status=$run_status
  if [ "$final_status" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then
    final_status=$cleanup_status
  fi
  if [ "$final_status" -eq 0 ] && [ "$report_status" -ne 0 ]; then
    final_status=$report_status
  fi
  exit "$final_status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

rm -rf "$results"
mkdir -p "$results/package" "$results/work" "$results/diagnostics"
printf '%s\n' "$project" >"$results/project-name"
printf '%s\n' "$runtime_image" >"$results/runtime-image"
"$python" "$root/tests/integration/report_contract.py" init \
  --results "$results" --run-id "$run_id" --project "$project"

phase=focused-contract-gate
PYTHONPATH="$root/backend/src" "$root/.venv/bin/pytest" -q \
  "$root/backend/tests/test_transport_manifest_v2.py" \
  "$root/backend/tests/test_transport_state.py" \
  "$root/backend/tests/test_comment_operations.py" \
  "$root/backend/tests/test_state_store.py" \
  "$root/backend/tests/test_git_bundle.py" \
  "$root/backend/tests/test_gitea_provisioning.py" \
  "$root/backend/tests/test_provisioning.py" \
  >"$results/work/contract-gate.log" 2>&1
touch "$results/work/contract-gate.passed"

admin_password=$($python -c 'import secrets; print(secrets.token_urlsafe(24))')
user_password=$($python -c 'import secrets; print(secrets.token_urlsafe(24))')
export GITFERRY_IT_ADMIN_PASSWORD="$admin_password"
export GITFERRY_IT_USER_PASSWORD="$user_password"
export GITFERRY_IT_PROJECT="$project"
export GITFERRY_IT_RUNTIME_IMAGE="$runtime_image"
GITFERRY_VERSION=$(PYTHONPATH="$root/backend/src" "$python" -c 'from gitferry import __version__; print(__version__)')
export GITFERRY_VERSION
export GITFERRY_SOURCE_REVISION=${GITFERRY_CI_SOURCE_SHA:-$(git -C "$root" rev-parse HEAD)}
export GITFERRY_IT_INSTANCE_NONCE=$($python -c 'import secrets; print(secrets.token_hex(12))')
export GITFERRY_IT_SOURCE_PORT=${GITFERRY_IT_SOURCE_PORT:-33001}
export GITFERRY_IT_TARGET_PORT=${GITFERRY_IT_TARGET_PORT:-33002}
export GITFERRY_IT_SOURCE_URL="http://127.0.0.1:$GITFERRY_IT_SOURCE_PORT"
export GITFERRY_IT_TARGET_URL="http://127.0.0.1:$GITFERRY_IT_TARGET_PORT"

phase=environment-start
docker compose -p "$project" -f "$compose_file" up -d --build
wait_for_service_state source healthy 120
wait_for_service_state target healthy 120
wait_for_service_state importer running 120
wait_for_service_state gitferry-a running 120
wait_for_service_state gitferry-b running 120
"$python" "$root/tests/integration/report_contract.py" collect-runtime \
  --results "$results" --project "$project" --compose-file "$compose_file"

if [ "${GITFERRY_IT_INJECT_FAILURE:-}" = "after-environment-start" ]; then
  echo "controlled integration failure after environment start" >&2
  exit 86
fi
if [ "${GITFERRY_IT_INJECT_FAILURE:-}" = "timeout-after-environment-start" ]; then
  phase=controlled-timeout
  timeout_seconds=${GITFERRY_IT_INJECT_TIMEOUT_SECONDS:-30}
  echo "controlled internal integration timeout after environment start" >&2
  set +e
  "$python" "$root/tests/integration/run_with_timeout.py" "$timeout_seconds" \
    "$python" -c 'import time; time.sleep(86400)'
  timeout_status=$?
  set -e
  if [ "$timeout_status" -ne 124 ]; then
    echo "controlled timeout returned unexpected status: $timeout_status" >&2
    exit 125
  fi
  exit 124
fi

phase=synthetic-administration
for service in source target; do
  docker compose -p "$project" -f "$compose_file" exec -T --user git "$service" \
    gitea admin user create \
    --username gitferry-admin \
    --password "$admin_password" \
    --email gitferry-admin@example.invalid \
    --admin \
    --must-change-password=false
done

phase=integration-probe
cd "$root"
PYTHONPATH=backend/src "$python" tests/integration/run_with_timeout.py \
  "${GITFERRY_IT_PROBE_TIMEOUT_SECONDS:-900}" \
  "$python" tests/integration/probe.py
phase=probe-complete
