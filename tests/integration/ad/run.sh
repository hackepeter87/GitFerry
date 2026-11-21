#!/bin/sh
set -eu
PROJECT="gitferry-it-ad-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
diag="${DIAG:-/tmp/${PROJECT}.log}"
result_dir="${RESULT_DIR:-$here/../../../integration-results/ad-${PROJECT#gitferry-it-ad-}}"
mkdir -p "$result_dir"
report="$result_dir/report.jsonl"
status=0
cleanup() {
  observed_status=$?
  if [ "$status" -eq 0 ] && [ "$observed_status" -ne 0 ]; then status=$observed_status; fi
  set +e
  trap - EXIT HUP INT TERM
  docker compose -p "$PROJECT" -f "$here/compose.yaml" logs --no-color >"$diag" 2>&1
  cleanup_status=0
  docker compose -p "$PROJECT" -f "$here/compose.yaml" down --volumes --remove-orphans >/dev/null 2>&1 || cleanup_status=$?
  printf '{"project":"%s","cleanup_status":%s,"diagnostics":"%s"}\n' "$PROJECT" "$cleanup_status" "$diag" >"$result_dir/run-metadata.json"
  if [ "$status" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then status="$cleanup_status"; fi
  exit "$status"
}
trap 'status=143; cleanup' HUP INT TERM
trap cleanup EXIT
docker compose -p "$PROJECT" -f "$here/compose.yaml" up -d --build --wait --wait-timeout 45 || status=$?
if [ "$status" -eq 0 ]; then
  image_id=$(docker compose -p "$PROJECT" -f "$here/compose.yaml" images -q ad)
  platform=$(docker image inspect "$image_id" --format '{{.Os}}/{{.Architecture}}')
  samba_version=$(docker compose -p "$PROJECT" -f "$here/compose.yaml" exec -T ad samba --version)
  printf '{"project":"%s","image_id":"%s","platform":"%s","samba_version":"%s"}\n' "$PROJECT" "$image_id" "$platform" "$samba_version" >"$result_dir/image-metadata.json"
  docker compose -p "$PROJECT" -f "$here/compose.yaml" exec -T ad python3 /usr/local/bin/ad-worker >"$report" 2>&1 || status=$?
fi
echo "results: $result_dir" >&2
