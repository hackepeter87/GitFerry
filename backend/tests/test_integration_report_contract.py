from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def report_contract() -> ModuleType:
    path = Path(__file__).parents[2] / "tests" / "integration" / "report_contract.py"
    spec = importlib.util.spec_from_file_location("integration_report_contract", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def complete_probe_report() -> dict[str, object]:
    comment_assertions = {
        "case_a_updated_same_comment": True,
        "case_b_b_origin_on_a_parent": True,
        "case_c_comment_before_issue_planned_parent_first": True,
        "comment_drift_blocked_before_any_return_write": True,
        "forward_restart_no_duplicate": True,
        "hard_crash_restart_no_post": True,
        "hard_crash_has_verified_remote_acceptance_sync": True,
        "lost_response_restart_no_post": True,
        "lost_response_has_verified_remote_acceptance_sync": True,
        "return_restart_no_duplicate": True,
        "new_envelope_no_duplicate": True,
        "control_repository_change_preserved": True,
    }
    report: dict[str, object] = {
        "gitea": {"source": "1.27.2", "target": "1.27.2"},
        "comment_transport_roundtrip": {"assertions": comment_assertions},
        "portable_transport_roundtrip": {
            "assertions": {"separate_networks_block_cross_gitea": True}
        },
        "durable_provisioning": {
            "status": "passed",
            "assertions": {
                "all_operations_confirmed": True,
                "replay_all_operations_confirmed": True,
                "expected_private_structure_created": True,
                "replay_created_no_structure": True,
                "bundle_refs_match_source": True,
                "replay_changed_no_refs": True,
                "functional_access_matches_plan": True,
                "control_repository_untouched": True,
                "no_accounts_created": True,
                "crash_remote_post_succeeded": True,
                "crash_restart_is_outcome_unknown": True,
                "crash_replay_sent_no_second_post": True,
                "lost_response_remote_post_succeeded": True,
                "lost_response_is_outcome_unknown": True,
                "new_packaging_cannot_bypass_unknown": True,
                "credentials_absent_from_plan_and_state": True,
            },
        },
    }
    for section in (
        "git_bundle",
        "metadata",
        "identity_and_permissions",
        "metadata_roundtrip",
        "persistent_state_restart",
        "openapi_and_migration",
    ):
        report[section] = {"status": "passed"}
    report["comment_transport_roundtrip"]["status"] = "passed"
    report["portable_transport_roundtrip"]["status"] = "passed"
    return report


def prepare_results(tmp_path: Path, report: dict[str, object] | None) -> Path:
    results = tmp_path / "results"
    work = results / "work"
    work.mkdir(parents=True)
    (work / "run-metadata.json").write_text(
        json.dumps({"run_id": "test", "compose_project": "gitferry-it-test"}),
        encoding="utf-8",
    )
    (work / "contract-gate.passed").touch()
    if report is not None:
        (results / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return results


def test_finalize_rejects_missing_report_even_when_process_and_cleanup_succeeded(
    tmp_path: Path,
) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, None)

    assert not module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert execution["status"] == "failed"
    assert execution["probe_report_available"] is False


def test_finalize_rejects_missing_required_assertion_and_records_cleanup(tmp_path: Path) -> None:
    module = report_contract()
    report = complete_probe_report()
    report["comment_transport_roundtrip"]["assertions"]["case_a_updated_same_comment"] = False
    results = prepare_results(tmp_path, report)

    assert not module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=True,
    )
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert "changed_a_comment_updates_same_a_comment" in execution["failed_or_not_executed_checks"]
    assert execution["cleanup"]["status"] == "passed"


def test_finalize_accepts_complete_required_evidence(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())

    assert module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert execution["status"] == "passed"
    assert execution["failed_or_not_executed_checks"] == []


def test_probe_contract_requires_every_top_level_probe(tmp_path: Path) -> None:
    module = report_contract()
    report = complete_probe_report()
    del report["persistent_state_restart"]
    results = prepare_results(tmp_path, report)

    assert not module.probe_contract_is_complete(results)
