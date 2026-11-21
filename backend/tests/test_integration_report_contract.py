from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

PINNED_GITEA_IMAGE = (
    "docker.gitea.com/gitea:1.27.2@sha256:"
    "d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd"
)


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
        "source_exports": {
            "status": "passed",
            "image_id": "sha256:" + "a" * 64,
            "assertions": dict.fromkeys(
                (
                    "source_capture_confirmed",
                    "actual_git_bundle_captured",
                    "organization_and_team_captured",
                    "issue_comment_distinct_author_claims",
                    "manifest_v2_unchanged",
                    "byte_archive_exact",
                    "source_context_cannot_target_or_external",
                    "capture_has_no_api_writes",
                    "offline_replay_exact",
                    "target_only_archive_verified",
                    "source_and_target_databases_independent",
                    "package_admission_apply_confirmed",
                    "native_authors_parent_and_git_refs",
                    "target_only_archive_and_external_unreachable",
                    "package_admission_database_independent",
                    "no_user_preparation_blocks_metadata",
                    "package_replay_process_restart_no_extra_posts_or_objects",
                    "api_queue_background_download",
                    "api_restart_replay_no_source_access",
                    "target_upload_files_only_before_local_setup",
                    "target_upload_replay_persistent_identity",
                    "guided_browser_first_import",
                    "guided_browser_source_export",
                    "guided_browser_target_collision",
                    "guided_browser_preview_no_remote_write",
                    "guided_browser_production_runtime",
                    "guided_selected_set_and_control",
                    "guided_browser_restart_read_only",
                ),
                True,
            ),
        },
        "application_plans": {
            "status": "passed",
            "assertions": dict.fromkeys(
                (
                    "confirmed_restart_no_duplicates",
                    "control_repository_unchanged",
                    "crash_no_second_post",
                    "crash_remote_post_accepted",
                    "crash_restart_outcome_unknown",
                    "credentials_absent_from_state_and_report",
                    "distinct_native_authors",
                    "lost_no_second_post",
                    "lost_remote_post_accepted",
                    "lost_restart_outcome_unknown",
                    "new_packaging_cannot_bypass_unknown",
                    "explicit_resolution_rejects_wrong_parent_author_and_content",
                    "explicit_resolution_then_restart_confirms_without_post",
                    "source_catalog_lists_real_organization_and_repository_without_writes",
                    "application_package_bundle_received_and_used",
                    "parent_mapping_confirmed",
                    "structure_bundle_and_metadata_confirmed",
                    "target_only_context",
                ),
                True,
            ),
        },
        "connection_profiles": {
            "status": "passed",
            "assertions": dict.fromkeys(
                (
                    "real_gitea_version_and_account",
                    "check_did_not_approve",
                    "explicit_local_approval",
                    "read_only_connection_check",
                    "untrusted_ca_rejected",
                    "wrong_ca_rejected",
                    "hostname_mismatch_rejected",
                    "expired_certificate_rejected",
                    "credentials_absent_from_state_and_exports",
                    "api_probe_and_local_approval",
                    "cli_restart_preserves_approved_evidence",
                    "credentials_absent_from_boundaries_and_logs",
                    "system_trust_store_without_ca_reference",
                ),
                True,
            ),
        },
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
        "oci_runtime": {
            "status": "passed",
            "image_id": "sha256:" + "a" * 64,
            "assertions": {
                "non_root_user": True,
                "version_label": True,
                "revision_label": True,
                "license_label": True,
                "healthcheck_present": True,
                "graceful_stop_signal": True,
                "fresh_volume_initialized": True,
                "packaged_frontend_served": True,
                "installed_admin_security": True,
                "runtime_hardening": True,
                "container_replacement_preserved_state": True,
                "identity_binding_preserved": True,
                "object_mapping_preserved": True,
                "baseline_preserved": True,
                "outcome_unknown_preserved": True,
                "operation_events_preserved": True,
                "conflict_preserved": True,
                "package_and_work_files_preserved": True,
                "graceful_shutdown_observed": True,
                "forced_restart_recovered_sqlite": True,
                "newer_schema_failed_closed": True,
                "corrupt_database_failed_closed": True,
                "read_only_directory_failed_closed": True,
                "symlink_path_failed_closed": True,
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
        json.dumps(
            {
                "run_id": "test",
                "compose_project": "gitferry-it-test",
                "code_state": {
                    "git_head": "a" * 40,
                    "dirty": False,
                    "workspace_sha256": "b" * 64,
                },
                "platform": {
                    "host_system": "Linux",
                    "host_release": "test",
                    "host_architecture": "x86_64",
                    "python": "3.13.9",
                    "github_actions": False,
                    "runner_os": None,
                    "runner_arch": None,
                    "docker_server_os": "linux",
                    "docker_server_architecture": "amd64",
                    "docker_server_version": "test",
                    "gitea_image_architectures": ["amd64"],
                    "emulation_observed": False,
                },
                "github": None,
                "gitea_images": {
                    "expected_pin": PINNED_GITEA_IMAGE,
                    "services": {
                        name: {
                            "configured_image": PINNED_GITEA_IMAGE,
                            "image_id": f"sha256:{'a' if name == 'source' else 'b'}" + "0" * 63,
                            "os": "linux",
                            "architecture": "amd64",
                            "repo_digests": [
                                "docker.gitea.com/gitea@" + PINNED_GITEA_IMAGE.split("@", 1)[1]
                            ],
                        }
                        for name in ("source", "target")
                    },
                },
            }
        ),
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


@pytest.mark.parametrize("mutation", ["missing", "false"])
def test_finalize_rejects_incomplete_oci_assertions(tmp_path: Path, mutation: str) -> None:
    module = report_contract()
    report = complete_probe_report()
    assertions = report["oci_runtime"]["assertions"]
    if mutation == "missing":
        del assertions["symlink_path_failed_closed"]
    else:
        assertions["symlink_path_failed_closed"] = False
    results = prepare_results(tmp_path, report)

    assert not module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert "persistent_non_root_oci_runtime" in execution["failed_or_not_executed_checks"]


def test_probe_contract_requires_every_top_level_probe(tmp_path: Path) -> None:
    module = report_contract()
    report = complete_probe_report()
    del report["persistent_state_restart"]
    results = prepare_results(tmp_path, report)

    assert not module.probe_contract_is_complete(results)


@pytest.mark.parametrize("mutation", ["missing", "false"])
def test_connection_assertion_cannot_be_omitted_or_failed(tmp_path: Path, mutation: str) -> None:
    module = report_contract()
    report = complete_probe_report()
    assertions = report["connection_profiles"]["assertions"]
    if mutation == "missing":
        del assertions["real_gitea_version_and_account"]
    else:
        assertions["real_gitea_version_and_account"] = False
    results = prepare_results(tmp_path, report)
    assert not module.probe_contract_is_complete(results)
    assert not module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )


def test_finalize_rejects_incomplete_run_metadata(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    del metadata["code_state"]["workspace_sha256"]
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert not module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert "run_metadata_contract" in execution["failed_or_not_executed_checks"]


def test_initialize_records_github_run_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = report_contract()
    github_values = {
        "GITHUB_RUN_ID": "12345",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": "c" * 40,
        "GITFERRY_CI_SOURCE_SHA": "d" * 40,
        "GITHUB_REF": "refs/pull/2/merge",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_WORKFLOW_REF": "hackepeter87/GitFerry/.github/workflows/ci.yml@refs/pull/2/merge",
        "GITHUB_JOB": "gitea-integration",
    }
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for key, value in github_values.items():
        monkeypatch.setenv(key, value)
    module.initialize(tmp_path, "gha-12345-2", "gitferry-it-gha-12345-2")

    metadata = json.loads((tmp_path / "work" / "run-metadata.json").read_text())
    assert metadata["github"] == {
        "run_id": "12345",
        "run_attempt": "2",
        "source_sha": "d" * 40,
        "checkout_sha": "c" * 40,
        "ref": "refs/pull/2/merge",
        "event_name": "pull_request",
        "workflow_ref": github_values["GITHUB_WORKFLOW_REF"],
        "job": "gitea-integration",
    }


def test_finalize_rejects_github_sha_that_does_not_match_checkout(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["platform"]["github_actions"] = True
    metadata["github"] = {
        "run_id": "12345",
        "run_attempt": "1",
        "source_sha": "a" * 40,
        "checkout_sha": "c" * 40,
        "ref": "refs/heads/main",
        "event_name": "push",
        "workflow_ref": "hackepeter87/GitFerry/.github/workflows/ci.yml@refs/heads/main",
        "job": "gitea-integration",
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    assert not module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert "run_metadata_contract" in execution["failed_or_not_executed_checks"]


def test_pull_request_source_sha_may_differ_from_tested_merge_sha(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["platform"]["github_actions"] = True
    metadata["github"] = {
        "run_id": "12345",
        "run_attempt": "1",
        "source_sha": "c" * 40,
        "checkout_sha": "a" * 40,
        "ref": "refs/pull/39/merge",
        "event_name": "pull_request",
        "workflow_ref": "hackepeter87/GitFerry/.github/workflows/ci.yml@refs/pull/39/merge",
        "job": "gitea-integration",
    }

    assert module.run_metadata_is_complete(metadata)


def test_run_metadata_rejects_substituted_gitea_digest(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["gitea_images"]["services"]["target"]["configured_image"] = (
        "untrusted.invalid/gitea:1.27.2@sha256:" + "c" * 64
    )
    metadata["gitea_images"]["services"]["target"]["repo_digests"] = [
        "untrusted.invalid/gitea@sha256:" + "c" * 64
    ]

    assert not module.run_metadata_is_complete(metadata)


def test_workflow_cleanup_result_is_added_before_artifact_upload(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())
    assert module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )

    assert module.record_workflow_cleanup(results, 0)
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert execution["workflow_cleanup"]["status"] == "passed"
    assert execution["required_checks"][-1]["id"] == "workflow_scoped_cleanup"
    assert json.loads((results / "workflow-cleanup.json").read_text())["exit_code"] == 0


def test_workflow_cleanup_failure_keeps_report_failed(tmp_path: Path) -> None:
    module = report_contract()
    results = prepare_results(tmp_path, complete_probe_report())
    assert module.finalize(
        results,
        run_status=0,
        cleanup_status=0,
        phase="probe-complete",
        diagnostics_captured=False,
    )

    assert not module.record_workflow_cleanup(results, 7)
    execution = json.loads((results / "report.json").read_text())["execution"]
    assert execution["status"] == "failed"
    assert execution["workflow_cleanup"]["exit_code"] == 7
    assert "workflow_scoped_cleanup" in execution["failed_or_not_executed_checks"]


@pytest.mark.parametrize(
    "assertion",
    [
        "crash_no_second_post",
        "lost_restart_outcome_unknown",
        "explicit_resolution_rejects_wrong_parent_author_and_content",
        "explicit_resolution_then_restart_confirms_without_post",
        "source_catalog_lists_real_organization_and_repository_without_writes",
        "application_package_bundle_received_and_used",
        "structure_bundle_and_metadata_confirmed",
    ],
)
def test_application_plan_assertion_is_required(tmp_path: Path, assertion: str) -> None:
    module = report_contract()
    report = complete_probe_report()
    del report["application_plans"]["assertions"][assertion]
    results = prepare_results(tmp_path, report)
    assert not module.probe_contract_is_complete(results)


@pytest.mark.parametrize(
    "assertion",
    [
        "source_capture_confirmed",
        "actual_git_bundle_captured",
        "organization_and_team_captured",
        "issue_comment_distinct_author_claims",
        "manifest_v2_unchanged",
        "byte_archive_exact",
        "source_context_cannot_target_or_external",
        "capture_has_no_api_writes",
        "offline_replay_exact",
        "target_only_archive_verified",
        "source_and_target_databases_independent",
        "package_admission_apply_confirmed",
        "native_authors_parent_and_git_refs",
        "target_only_archive_and_external_unreachable",
        "package_admission_database_independent",
        "no_user_preparation_blocks_metadata",
        "package_replay_process_restart_no_extra_posts_or_objects",
        "api_queue_background_download",
        "api_restart_replay_no_source_access",
        "target_upload_files_only_before_local_setup",
        "target_upload_replay_persistent_identity",
        "guided_browser_first_import",
        "guided_browser_source_export",
        "guided_browser_target_collision",
        "guided_browser_preview_no_remote_write",
        "guided_browser_production_runtime",
        "guided_selected_set_and_control",
        "guided_browser_restart_read_only",
    ],
)
def test_source_capture_assertion_is_required(tmp_path: Path, assertion: str) -> None:
    module = report_contract()
    report = complete_probe_report()
    del report["source_exports"]["assertions"][assertion]
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize("image_id", [None, "sha256:" + "b" * 64])
def test_browser_and_runtime_must_use_the_same_image(tmp_path: Path, image_id) -> None:
    module = report_contract()
    report = complete_probe_report()
    report["source_exports"]["image_id"] = image_id
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize("assertion", ["installed_admin_security", "runtime_hardening"])
def test_installed_application_security_is_required(tmp_path: Path, assertion: str) -> None:
    module = report_contract()
    report = complete_probe_report()
    del report["oci_runtime"]["assertions"][assertion]
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))
