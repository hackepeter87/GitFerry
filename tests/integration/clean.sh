#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
compose_file="$root/tests/integration/compose.yaml"
results_root=${GITFERRY_IT_RESULTS_ROOT:-$root}
export GITFERRY_IT_USER_PASSWORD=${GITFERRY_IT_USER_PASSWORD:-}
export GITFERRY_IT_ADMIN_PASSWORD=${GITFERRY_IT_ADMIN_PASSWORD:-}
export GITFERRY_IT_INSTANCE_NONCE=${GITFERRY_IT_INSTANCE_NONCE:-}
seen_projects=
seen_images=

clean_scoped_resources() {
  project=$1
  label="gitferry.integration.project=$project"
  for container in $(docker ps --all --quiet --filter "label=$label"); do
    docker rm --force "$container"
  done
  for volume in $(docker volume ls --quiet --filter "label=$label"); do
    docker volume rm --force "$volume"
  done
}

clean_project() {
  project=$1
  case "$project" in
    gitferry-it-*) ;;
    *) echo "refusing cleanup outside gitferry-it-* scope: $project" >&2; exit 1 ;;
  esac
  case "$project" in
    *[!a-zA-Z0-9_-]*) echo "invalid integration project name: $project" >&2; exit 1 ;;
  esac
  case "|$seen_projects|" in
    *"|$project|"*) return ;;
  esac
  seen_projects="${seen_projects:+$seen_projects|}$project"
  clean_scoped_resources "$project"
  docker compose -p "$project" -f "$compose_file" down --volumes --remove-orphans
}

clean_image() {
  image=$1
  case "$image" in
    gitferry-it-runtime:*) ;;
    *) echo "refusing cleanup of non-integration image: $image" >&2; exit 1 ;;
  esac
  case "$image" in
    *[!a-zA-Z0-9_.:-]*) echo "invalid integration image name: $image" >&2; exit 1 ;;
  esac
  case "|$seen_images|" in
    *"|$image|"*) return ;;
  esac
  seen_images="${seen_images:+$seen_images|}$image"
  if docker image inspect "$image" >/dev/null 2>&1; then
    docker image rm "$image"
  fi
}

clean_image_file() {
  image_file=$1
  if [ -f "$image_file" ]; then
    clean_image "$(sed -n '1p' "$image_file")"
  fi
}

found=false
if [ -n "${GITFERRY_IT_PROJECT:-}" ]; then
  clean_project "$GITFERRY_IT_PROJECT"
  found=true
  if [ -n "${GITFERRY_IT_RUNTIME_IMAGE:-}" ]; then
    clean_image "$GITFERRY_IT_RUNTIME_IMAGE"
  fi
fi

for directory in "$results_root/integration-results" "$results_root/integration-failure-results"; do
  if [ -f "$directory/project-name" ]; then
    clean_project "$(sed -n '1p' "$directory/project-name")"
    clean_image_file "$directory/runtime-image"
    found=true
  fi
done
if [ "$found" = false ]; then
  clean_project gitferry-it-verification
fi
