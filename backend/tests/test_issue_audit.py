from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("issue_audit", ROOT / "scripts" / "issue_audit.py")
assert SPEC and SPEC.loader
issue_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_audit)


BODY = """# Problem and expected result
Users need a resumable issue contract.
# Scope
The bounded implementation and its users.
# Exclusions
No productive migration.
# Acceptance criteria
Observable criteria are listed here.
# Required tests and evidence
Tests: pytest contract. Evidence: verification result.
Evidence required: none
# Documentation and compatibility impact
Update docs and compatibility notes.
# Dependencies and blockers
Depends on: #1\nBlocks: None
# Target release
0.4.0
# Work kind
Feature
# Failure and recovery
Failure is reported and recovery is documented.
# Coherent execution boundary
One bounded delivery slice with a checkpoint.
"""


def snapshot(**changes: object) -> dict[str, object]:
    issue = {
        "number": 48,
        "state": "open",
        "body": BODY,
        "labels": ["type:feature", "area:planning", "priority:p1", "risk:low"],
        "milestone": "0.4.0",
    }
    issue.update(changes)
    return {
        "issue": issue,
        "project": {
            "Status": "In Progress",
            "Priority": "P1",
            "Type": "Feature",
            "Area": "Planning",
            "Target release": "0.4.0",
            "Risk": "Low",
            "Needs Decision": "No",
        },
    }


def test_complete_snapshot_passes() -> None:
    result = issue_audit.audit_snapshot(snapshot())
    assert result["status"] == "passed"
    assert result["findings"] == []


def test_missing_criteria_and_metadata_are_reported() -> None:
    result = issue_audit.audit_snapshot(
        snapshot(body=BODY.replace("# Acceptance criteria", "# Acceptance"))
    )
    assert result["status"] == "failed"
    assert "Acceptance criteria" in result["missing_sections"]
    assert "Target release" not in result["missing_body_metadata"]


def test_missing_milestone_is_reported() -> None:
    result = issue_audit.audit_snapshot(snapshot(milestone=None))
    assert result["missing_milestone"] is True
    assert "missing issue milestone" in result["findings"]


def test_missing_dependency_and_label_metadata_are_reported() -> None:
    result = issue_audit.audit_snapshot(
        snapshot(body=BODY.replace("Depends on: #1", ""), labels=["type:feature"])
    )
    assert "Depends on" in result["missing_body_metadata"]
    assert set(result["missing_label_kinds"]) == {"area", "priority", "risk"}


def test_missing_project_fields_and_evidence_flags_are_reported() -> None:
    data = snapshot(
        body=BODY.replace("Tests: pytest contract. Evidence: verification result.", "Test pending.")
    )
    data["project"] = {"Status": "Backlog", "Needs Decision": "No"}
    result = issue_audit.audit_snapshot(data)
    assert set(result["missing_project_fields"]) == {
        "Priority",
        "Type",
        "Area",
        "Target release",
        "Risk",
    }
    assert result["evidence_flags"] == {"has_test_marker": True, "has_evidence_marker": True}


def test_mismatched_evidence_and_project_metadata_are_reported() -> None:
    data = snapshot(
        body=BODY.replace("Evidence required: none", "Evidence required: browser"),
        labels=["type:bug", "area:planning", "priority:p1", "risk:low", "evidence:directory"],
    )
    data["project"] = {"Status": "Unknown", "Type": "Bug fix", "Needs Decision": "Maybe"}
    result = issue_audit.audit_snapshot(data)
    assert "evidence labels do not match Evidence required" in result["findings"]
    assert "invalid project status" in result["findings"]
    assert "Needs Decision must be Yes or No" in result["findings"]


def test_label_and_project_values_must_match() -> None:
    data = snapshot(labels=["type:feature", "area:other", "priority:p2", "risk:high"])
    result = issue_audit.audit_snapshot(data)
    assert "area label does not match project field" in result["findings"]
    assert "priority label does not match project field" in result["findings"]
    assert "risk label does not match project field" in result["findings"]


def test_form_keeps_contract_fields_required() -> None:
    form = (ROOT / ".github/ISSUE_TEMPLATE/implementation.yml").read_text(encoding="utf-8")
    for field in (
        "work_kind",
        "failure_recovery",
        "execution_boundary",
        "target_release",
        "evidence_level",
    ):
        assert f"id: {field}" in form
    assert form.count("required: true") >= 12


def test_real_evidence_labels_and_empty_dependencies() -> None:
    data = snapshot(
        body=BODY.replace("Evidence required: none", "Evidence required: browser, recovery")
    )
    assert issue_audit.audit_snapshot(data)["status"] == "failed"
    data["issue"]["labels"] += ["evidence:browser", "evidence:recovery"]
    assert issue_audit.audit_snapshot(data)["status"] == "passed"
    data["issue"]["body"] = data["issue"]["body"].replace("Depends on: #1", "Depends on:")
    assert "Depends on must have a value or None" in issue_audit.audit_snapshot(data)["findings"]


def test_form_yaml_and_required_contract_fields() -> None:
    import yaml

    form = yaml.safe_load((ROOT / ".github/ISSUE_TEMPLATE/implementation.yml").read_text())
    fields = {field["attributes"]["label"]: field for field in form["body"]}
    for name in issue_audit.REQUIRED_SECTIONS:
        assert fields[name]["validations"]["required"] is True
    assert set(fields["Work kind"]["attributes"]["options"]) == {
        "Feature",
        "Bug",
        "Maintenance",
        "Release",
        "Evidence",
    }


def test_empty_cli_input_fails(tmp_path, capsys) -> None:
    path = tmp_path / "empty.json"
    path.write_text("[]")
    assert issue_audit.main([str(path)]) == 2
    assert "failed" in capsys.readouterr().out


def test_github_export_requires_complete_project_and_normalizes_fields() -> None:
    import pytest

    issue = snapshot()["issue"]
    item = {
        "content": {"number": 48, "repository": "hackepeter87/GitFerry"},
        "status": "In Progress",
        "priority": "P1",
        "work Type": "Feature",
        "area": "Planning",
        "target Release": "0.4.0",
        "risk": "Low",
        "needs Decision": "No",
    }
    project = {"items": [item], "totalCount": 1}
    issues = [dict(issue, number=number) for number in [*range(8, 37), 47, 48]]
    data = issue_audit.github_snapshots([issues], project)
    assert issue_audit.audit_snapshot(data[-1])["status"] == "passed"
    project["totalCount"] = 2
    with pytest.raises(ValueError, match="incomplete"):
        issue_audit.github_snapshots(issues, project)


def test_release_prefix_and_project_completion_cannot_hide_mismatch() -> None:
    data = snapshot()
    data["project"]["Target release"] = "0.4"
    assert (
        "milestone does not match project target release"
        in issue_audit.audit_snapshot(data)["findings"]
    )
    data = snapshot(state="closed")
    assert "issue state contradicts project status" in issue_audit.audit_snapshot(data)["findings"]
    data["project"]["Status"] = "Done"
    assert issue_audit.audit_snapshot(data)["status"] == "passed"


def test_incomplete_issue_export_cannot_pass() -> None:
    import pytest

    with pytest.raises(ValueError, match="incomplete required issue scope"):
        issue_audit.github_snapshots([snapshot()["issue"]], {"totalCount": 0, "items": []})
