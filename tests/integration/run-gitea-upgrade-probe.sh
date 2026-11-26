#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
report=${GITFERRY_UPGRADE_REPORT:-$root/integration-results/gitea-upgrade-report.json}
mkdir -p "$(dirname "$report")"
export GITFERRY_UPGRADE_REPORT=$report
PYTHONPATH="$root/backend/src:$root/tests/integration" \
  "$root/.venv/bin/python" "$root/tests/integration/gitea_upgrade_probe.py"
