from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"


def workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_ci_keeps_stable_required_check_contexts() -> None:
    jobs = workflow()["jobs"]

    assert set(jobs) == {"quality", "gitea-integration"}
    assert all("name" not in jobs[job] for job in jobs)

    quality_commands = [
        step["run"] for step in jobs["quality"]["steps"] if isinstance(step, dict) and "run" in step
    ]
    assert quality_commands == [
        "python scripts/check_python.py\n"
        "python -m venv /tmp/gitferry-minimum-python\n"
        "/tmp/gitferry-minimum-python/bin/pip install --disable-pip-version-check '.[dev]'\n"
        "/tmp/gitferry-minimum-python/bin/pip check\n"
        "PYTHONPATH=backend/src /tmp/gitferry-minimum-python/bin/pytest backend/tests -q\n",
        "make setup",
        "cd frontend && npx playwright install --with-deps chromium",
        "make doctor",
        "make test",
        "make test-browser",
        "make container-build",
    ]

    steps = jobs["quality"]["steps"]
    by_name = {step["name"]: step for step in steps}
    browser = by_name["Run browser authentication tests"]
    diagnostics = by_name["Upload browser diagnostics"]
    assert browser["timeout-minutes"] == 5
    assert diagnostics["if"] == "failure()"
    assert diagnostics["with"]["path"] == "frontend/test-results/"
    assert diagnostics["with"]["retention-days"] == 14


def test_ci_uses_immutable_node24_action_releases() -> None:
    expected = {
        "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python": "6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
        "actions/setup-node": "949feb2413d6458794dcd2491c4babbbce0c15c1",
        "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",
    }

    used: dict[str, set[str]] = {}
    for job in workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" not in step:
                continue
            action, sha = step["uses"].split("@", 1)
            used.setdefault(action, set()).add(sha)

    assert used == {action: {sha} for action, sha in expected.items()}


def test_injected_failure_still_cleans_and_uploads_diagnostics() -> None:
    document = workflow()
    triggers = document.get("on", document.get(True))
    dispatch = triggers["workflow_dispatch"]["inputs"]["inject_integration_failure"]
    timeout_dispatch = triggers["workflow_dispatch"]["inputs"]["inject_integration_timeout"]
    assert dispatch["type"] == "boolean"
    assert dispatch["default"] is False
    assert timeout_dispatch["type"] == "boolean"
    assert timeout_dispatch["default"] is False

    job = document["jobs"]["gitea-integration"]
    assert job["timeout-minutes"] == 55
    assert "pull_request.head.sha" in job["env"]["GITFERRY_CI_SOURCE_SHA"]
    steps = job["steps"]
    by_name = {step["name"]: step for step in steps}
    integration = by_name["Run Gitea integration"]
    failure_contract = by_name["Run integration failure contract"]
    diagnostics = by_name["Capture final workflow diagnostics"]
    cleanup = by_name["Clean integration resources"]
    record_cleanup = by_name["Record workflow cleanup result"]
    upload = by_name["Upload integration results"]

    assert "inject_integration_failure" in integration["env"]["GITFERRY_IT_INJECT_FAILURE"]
    assert "inject_integration_timeout" in integration["env"]["GITFERRY_IT_INJECT_FAILURE"]
    assert "GITFERRY_IT_INJECT_TIMEOUT_SECONDS" in integration["env"]
    assert "inject_integration_timeout" in integration["timeout-minutes"]
    assert "inject_integration_failure" in failure_contract["if"]
    assert "inject_integration_timeout" in failure_contract["if"]
    assert failure_contract["timeout-minutes"] == 10
    assert diagnostics["if"] == "always()"
    assert diagnostics["timeout-minutes"] == 3
    assert diagnostics["run"] == "make test-integration-diagnostics"
    assert cleanup["if"] == "always()"
    assert cleanup["timeout-minutes"] == 5
    assert record_cleanup["if"] == "always()"
    assert record_cleanup["timeout-minutes"] == 3
    assert "record-workflow-cleanup" in record_cleanup["run"]
    assert upload["if"] == "always()"
    assert upload["timeout-minutes"] == 5
    ordered_steps = (diagnostics, cleanup, record_cleanup, upload)
    assert [steps.index(step) for step in ordered_steps] == sorted(
        steps.index(step) for step in ordered_steps
    )
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == 14
