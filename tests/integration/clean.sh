#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
compose_file="$root/tests/integration/compose.yaml"
export GITFERRY_IT_USER_PASSWORD=${GITFERRY_IT_USER_PASSWORD:-}
export GITFERRY_IT_INSTANCE_NONCE=${GITFERRY_IT_INSTANCE_NONCE:-}

clean_project() {
  project=$1
  case "$project" in
    gitferry-it-*) ;;
    *) echo "refusing cleanup outside gitferry-it-* scope: $project" >&2; exit 1 ;;
  esac
  docker compose -p "$project" -f "$compose_file" down --volumes --remove-orphans
}

clean_image_file() {
  image_file=$1
  if [ ! -f "$image_file" ]; then
    return
  fi
  image=$(sed -n '1p' "$image_file")
  case "$image" in
    gitferry-it-runtime:*) ;;
    *) echo "refusing cleanup of non-integration image: $image" >&2; exit 1 ;;
  esac
  if docker image inspect "$image" >/dev/null 2>&1; then
    docker image rm "$image"
  fi
}

if [ -n "${GITFERRY_IT_PROJECT:-}" ]; then
  clean_project "$GITFERRY_IT_PROJECT"
  exit 0
fi

found=false
for directory in "$root/integration-results" "$root/integration-failure-results"; do
  if [ -f "$directory/project-name" ]; then
    clean_project "$(sed -n '1p' "$directory/project-name")"
    clean_image_file "$directory/runtime-image"
    found=true
  fi
done
if [ "$found" = false ]; then
  clean_project gitferry-it-verification
fi
