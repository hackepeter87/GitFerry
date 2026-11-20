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
    assert dispatch["type"] == "boolean"
    assert dispatch["default"] is False

    steps = document["jobs"]["gitea-integration"]["steps"]
    by_name = {step["name"]: step for step in steps}
    integration = by_name["Run Gitea integration"]
    failure_contract = by_name["Run integration failure contract"]
    cleanup = by_name["Clean integration resources"]
    upload = by_name["Upload integration results"]

    assert "inject_integration_failure" in integration["env"]["GITFERRY_IT_INJECT_FAILURE"]
    assert "inject_integration_failure" in failure_contract["if"]
    assert cleanup["if"] == "always()"
    assert upload["if"] == "always()"
    assert steps.index(cleanup) < steps.index(upload)
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["retention-days"] == 14
