#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
python="$root/.venv/bin/python"
run_id=$($python -c 'import uuid; print(uuid.uuid4().hex[:12])')
project="gitferry-it-directory-$run_id"
port=$((33100 + $(( $(printf '%s' "$run_id" | cksum | cut -d' ' -f1) % 500 ))))
results="$root/integration-results/directory-$run_id"
compose="$root/tests/integration/directory/compose.yaml"
mkdir -p "$results/diagnostics"

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  set +e
  diagnostics_status=0
  if [ "$status" -ne 0 ]; then
    docker compose -p "$project" -f "$compose" ps --all >"$results/diagnostics/compose-ps.txt" 2>&1
    docker compose -p "$project" -f "$compose" logs --no-color >"$results/diagnostics/compose.log" 2>&1
  fi
  docker compose -p "$project" -f "$compose" down --volumes --remove-orphans >"$results/diagnostics/cleanup.log" 2>&1 || diagnostics_status=$?
  printf 'command_status=%s\ncleanup_status=%s\n' "$status" "$diagnostics_status" >"$results/diagnostics/run-status.txt"
  if [ "$status" -eq 0 ] && [ "$diagnostics_status" -ne 0 ]; then status=$diagnostics_status; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

export GITFERRY_DIRECTORY_PORT="$port"
export GITFERRY_IT_PROJECT="$project"
export GITFERRY_DIRECTORY_COMPOSE_FILE="$compose"
export GITFERRY_IT_TARGET_URL="http://127.0.0.1:$port"
export GITFERRY_IT_ADMIN_PASSWORD="synthetic-admin-pass"
export GITFERRY_IT_USER_PASSWORD="synthetic-user-pass"
export GITFERRY_IT_DIRECTORY_PASSWORD="synthetic-directory-pass"
export GITFERRY_IT_DIRECTORY_CA="/run/directory-ca/ca.pem"

docker compose -p "$project" -f "$compose" config --quiet
docker compose -p "$project" -f "$compose" up -d --build --wait --wait-timeout 120
docker inspect "${project}-target-1" --format '{{.Config.Image}}' >"$results/gitea-image.json"
docker inspect "${project}-openldap-1" --format '{{.Image}}' >"$results/openldap-image-id"
docker compose -p "$project" -f "$compose" exec -T --user git target \
  gitea admin user create --username gitferry-admin --password "$GITFERRY_IT_ADMIN_PASSWORD" \
  --email gitferry-admin@example.invalid --admin --must-change-password=false \
  >"$results/admin-create.txt" 2>&1
PYTHONPATH="$root/backend/src:$root/tests/integration" "$python" \
  "$root/tests/integration/directory_probe.py" >"$results/report.json"
printf '%s\n' "$project" >"$results/project-name"
printf '%s\n' "$port" >"$results/port"
