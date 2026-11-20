#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
results="$root/integration-failure-results"
run_id="failure-$(date -u +%Y%m%d%H%M%S)-$$"
project="gitferry-it-$run_id"

set +e
GITFERRY_IT_RESULTS_DIR="$results" \
GITFERRY_IT_RUN_ID="$run_id" \
GITFERRY_IT_PROJECT="$project" \
GITFERRY_IT_RUNTIME_IMAGE="gitferry-it-runtime:$run_id" \
GITFERRY_IT_INJECT_FAILURE=after-environment-start \
  "$root/tests/integration/run.sh"
observed=$?
set -e

if [ "$observed" -eq 0 ]; then
  echo "controlled failure was incorrectly reported as success" >&2
  exit 1
fi
"$root/.venv/bin/python" - "$results/report.json" "$project" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
project = sys.argv[2]
report = json.loads(report_path.read_text(encoding="utf-8"))
execution = report["execution"]
assert execution["status"] == "failed"
assert execution["probe_exit_code"] == 86
assert execution["diagnostics_captured"] is True
assert execution["cleanup"]["status"] == "passed"
assert (report_path.parent / "diagnostics" / "compose.log").is_file()
for resource in ("container", "network", "volume"):
    found = subprocess.run(
        ["docker", resource, "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert not found, f"run-scoped {resource} resources remain: {found}"
runtime_image = (report_path.parent / "runtime-image").read_text(encoding="utf-8").strip()
image = subprocess.run(
    ["docker", "image", "inspect", runtime_image], text=True, capture_output=True
)
assert image.returncode != 0, f"run-scoped image remains: {runtime_image}"
PY
echo "controlled failure contract passed for $project"
