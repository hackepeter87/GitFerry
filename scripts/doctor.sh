#!/bin/sh
set -eu

python_command=${PYTHON_BOOTSTRAP:-python3}
if [ -x .venv/bin/python ]; then python_command=.venv/bin/python; fi
required="git $python_command node npm docker"
failed=0
for command_name in $required; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "missing: $command_name"
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  exit 1
fi

echo "host_arch=$(uname -m)"
"$python_command" scripts/check_python.py
echo "python=$($python_command --version 2>&1)"
echo "node=$(node --version)"
echo "git=$(git --version)"
echo "docker_context=$(docker context show)"
docker version --format 'docker_client={{.Client.Version}} docker_server={{.Server.Version}} server_arch={{.Server.Arch}}'
docker compose version

endpoint=$(docker context inspect --format '{{(index .Endpoints "docker").Host}}' "$(docker context show)")
case "$endpoint" in
  unix://*|npipe://*|tcp://127.0.0.1:*|tcp://localhost:*) ;;
  *)
    echo "refusing non-local Docker endpoint: $endpoint" >&2
    exit 1
    ;;
esac
echo "docker_endpoint=$endpoint"

available_kib=$(df -Pk . | awk 'NR == 2 {print $4}')
if [ "$available_kib" -lt 5242880 ]; then
  echo "at least 5 GiB free space is required for integration tests" >&2
  exit 1
fi
echo "available_kib=$available_kib"
