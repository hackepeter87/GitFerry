from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

PRIMARY_GITEA_VERSION = "28.4.0"
PINNED_GITEA_IMAGE = (
    "docker.gitea.com/gitea:28.4.0@sha256:"
    "aa90e8f00b755b0d665214ab9c14447ea44d3190dbe6b6d0cb46cd9c4f55d309"
)


def report_contract() -> ModuleType:
    path = Path(__file__).parents[2] / "tests" / "integration" / "report_contract.py"
    sys.path.insert(0, str(path.parent))
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
        "content_inventory": {
            "status": "passed",
            "gitea_version": PRIMARY_GITEA_VERSION,
            "assertions": {key: True for key in report_contract().INVENTORY_ASSERTIONS},
            "counts": {
                **{key: 1 for key in report_contract().INVENTORY_OBSERVED},
                "lfs": None,
                "other_content": None,
            },
            "direct_counts": {
                **{key: 1 for key in report_contract().INVENTORY_OBSERVED},
                "lfs": None,
                "other_content": None,
            },
            "source_writes_during_collection": 0,
            "selection_summary": {
                "transfer_ready": False,
                "full_transfer": False,
                "blockers": [
                    {"content_class": name, "reason": "Transfer adapter is not supported"}
                    for name in (
                        "milestones",
                        "pull_comments",
                        "pull_requests",
                        "reviews",
                        "review_comments",
                        "timelines",
                        "reactions",
                        "other_content",
                    )
                ],
            },
            "pull_content_version": {
                "accepted_status": 201,
                "stale_status": 409,
                "before": 1,
                "after": 2,
                "stale_title_and_body_unchanged": True,
            },
            "pull_merge_evidence": {
                "state": "closed",
                "merged": True,
                "closed_at": "2026-11-27T04:26:34Z",
                "merged_at": "2026-11-27T04:26:34Z",
                "merge_commit_sha": "7804ae35a5297bff75a4815dff272ba2046286cc",
                "merged_by_response": None,
                "timeline_merge_actor": "gitferry-admin",
            },
        },
        "native_payloads": {
            "status": "passed",
            "gitea_version": PRIMARY_GITEA_VERSION,
            "assertions": {key: True for key in report_contract().NATIVE_PAYLOAD_ASSERTIONS},
            "cases": {
                "normal": {
                    "source": {"inventory_status": "ready"},
                    "applied": {
                        "wiki_refs_and_content_match": True,
                        "lfs_remote_bytes_verified": True,
                    },
                    "replay": {
                        "source_state_absent": True,
                        "isolation_checked": True,
                        "plan_status": "confirmed",
                        "operations": {"upload_lfs_object": "confirmed"},
                        "remote_writes": 0,
                        "lfs_uploads": 1,
                    },
                    "repackaged": {
                        "remote_writes": 0,
                        "lfs_uploads": 1,
                        "operations": {"upload_lfs_object": "confirmed"},
                    },
                },
                "crash": {
                    "source": {"inventory_status": "ready"},
                    "applied": {"exit_code": 88},
                    "replay": {
                        "source_state_absent": True,
                        "isolation_checked": True,
                        "after_remote_crash_marker": True,
                        "lfs_remote_bytes_verified": True,
                        "operations": {"upload_lfs_object": "outcome_unknown"},
                        "remote_writes": 0,
                        "lfs_uploads": 1,
                    },
                    "repackaged": {
                        "remote_writes": 0,
                        "lfs_uploads": 1,
                        "operations": {"upload_lfs_object": "outcome_unknown"},
                    },
                    "resolved": {"plan_status": "confirmed", "lfs_uploads": 1},
                },
                "lost": {
                    "source": {"inventory_status": "ready"},
                    "applied": {"response_lost_marker": True},
                    "replay": {
                        "source_state_absent": True,
                        "isolation_checked": True,
                        "operations": {"upload_lfs_object": "outcome_unknown"},
                        "remote_writes": 0,
                        "lfs_uploads": 1,
                    },
                    "repackaged": {
                        "remote_writes": 0,
                        "lfs_uploads": 1,
                        "operations": {"upload_lfs_object": "outcome_unknown"},
                    },
                    "resolved": {"plan_status": "confirmed", "lfs_uploads": 1},
                },
            },
            "negatives": {
                "status": "passed",
                "assertions": {key: True for key in report_contract().NATIVE_NEGATIVE_ASSERTIONS},
            },
            "return_export": {
                "status": "passed",
                "application_return_blocked": True,
                "files": {
                    "lfs": {"oid": "c" * 64, "size": 128},
                    "refs": {"refs/heads/main": "b" * 40},
                },
            },
            "return_import": {
                "status": "passed",
                "scope": "native adapter feasibility only",
                "control_refs_unchanged": True,
                "application_return_implemented": False,
                "isolation_checked": True,
                "lfs_sha256": "c" * 64,
                "a_baseline": {"refs/heads/main": "a" * 40},
                "returned_refs": {"refs/heads/main": "b" * 40},
                "drift_preserved": {"refs/heads/main": "e" * 40},
                "confirmation": report_contract()._native_confirmation(
                    {"refs/heads/main": "b" * 40}
                ),
            },
        },
        "directory": {
            "status": "passed",
            "application_claim_reader": {
                "status": "passed",
                "client": "ldap3 2.9.1",
                "duplicate_login_conflict": True,
                "filter_escaping": True,
                "real_bind_and_wrong_password": True,
                "first": {
                    "login": "ldap-reader",
                    "external_id": "SYNTHETIC-EXT-10001",
                    "dn": "uid=ldap-reader,ou=People,dc=example,dc=invalid",
                    "directory_realm": "directory-a",
                    "ou_path": "ou=People",
                    "groups": ["cn=gitea-users,ou=Groups,dc=example,dc=invalid"],
                },
                "second": {
                    "login": "ldap-reader",
                    "external_id": "SYNTHETIC-EXT-20001",
                    "dn": "uid=ldap-reader,ou=People-B,dc=example,dc=invalid",
                    "directory_realm": "directory-b",
                    "ou_path": "ou=People-B",
                },
                "missing_mail": {
                    "email": None,
                    "dn": "uid=missing-mail,ou=People,dc=example,dc=invalid",
                },
            },
            "local_application_bootstrap": {
                "first": {"status": "passed", "create_call_count": 1},
                "replay": {"status": "passed", "create_call_count": 1},
                "crash": {"exit_code": 73},
                "changed": {"status": "passed"},
                "recovered_crash": {
                    "status": "passed",
                    "create_call_count": 1,
                    "record": {"status": "outcome_unknown", "native_source_id": None},
                },
                "recovered_lost_response": {
                    "status": "passed",
                    "create_call_count": 1,
                    "record": {"status": "outcome_unknown", "native_source_id": None},
                },
            },
            "gitea_version": PRIMARY_GITEA_VERSION,
            "valid_password_login": True,
            "wrong_password_rejected": True,
            "wrong_ca_rejected_and_restored": True,
            "disabled_filter_excluded_and_restored": True,
            "external_employee_number_changed_and_restored": True,
            "group_membership_changed_and_restored": True,
            "ldap_outage_failed_closed_and_restored": True,
            "ldap_user_not_administrator": True,
            "same_login_collision_ldap_rejected": True,
            "authenticated_account_readback": True,
            "directory_facts": {"duplicate_uid_scopes": True},
            "missing_mail_native_email": "synthetic@localhost.local",
        },
        "directory_ad": {
            "status": "passed",
            "gitea": {
                "status": "passed",
                "assertions": dict.fromkeys(
                    (
                        "valid_login",
                        "missing_directory_mail_login",
                        "disabled_denied",
                        "wrong_password_denied",
                        "non_admin",
                        "explicit_source",
                        "native_alias_distinct",
                    ),
                    True,
                ),
            },
            "client": "ldap3 2.9.1",
            "image_id": "sha256:synthetic",
            "platform": "linux/arm64",
            "server_version": "Version 4.22.11-synthetic",
            "claims": {
                "external_id_kind": "sid",
                "dn": "CN=ldap-same,OU=People,DC=synth,DC=ad",
                "ou_path": "OU=People,DC=synth,DC=ad",
                "external_id": "S-1-5-21-1-2-3-1103",
                "email": None,
                "groups": ["CN=Direct,OU=Groups,DC=synth,DC=ad"],
            },
            "negative_assertions": {
                "wrong_password": "passed",
                "wrong_ca": "passed",
                "disabled_filter": "passed",
            },
            "credential_forms": {"upn": True, "domain_login": True},
            "nested_matching_rule_results": [
                "CN=Direct,OU=Groups,DC=synth,DC=ad",
                "CN=Nested,OU=Groups,DC=synth,DC=ad",
            ],
        },
        "source_exports": {
            "status": "passed",
            "source_browser": {"inventory_request_version": 3, "inventory_reviewed": True},
            "native_payloads": {
                "status": "passed",
                "lfs": {"a" * 64: 128, "b" * 64: 256},
                "wiki": {
                    "source-export-lab/selected": {"refs/heads/master": "c" * 40},
                    "source-export-lab/selected-extra": {"refs/heads/master": "d" * 40},
                },
            },
            "ad_identity_recovery": {
                "status": "passed",
                "assertions": dict.fromkeys(
                    (
                        "missing_mail_binding_approved",
                        "native_account_login_without_implicit_rights",
                        "outage_blocks_without_rebinding",
                        "ad_group_change_persists_conflict",
                        "restored_groups_need_explicit_review",
                        "disabled_subject_blocks",
                        "enabled_subject_needs_explicit_review",
                        "same_login_new_sid_blocks",
                        "sid_conflict_survives_restart_and_review",
                        "binding_unchanged",
                        "no_mapped_writes",
                    ),
                    True,
                ),
                "recreated": {
                    "before": {"sid": "S-1-5-21-1", "dn": "CN=synthetic", "login": "synthetic"},
                    "after": {"sid": "S-1-5-21-2", "dn": "CN=synthetic", "login": "synthetic"},
                },
            },
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
                    "guided_browser_inventory_bound_export",
                    "guided_browser_target_collision",
                    "guided_browser_preview_no_remote_write",
                    "guided_browser_production_runtime",
                    "guided_selected_set_and_control",
                    "guided_browser_restart_read_only",
                    "directory_equal_login_realms_explicit_bindings",
                    "directory_claim_drift_restart_blocked",
                    "directory_ad_identity_drift_restart_blocked",
                    "directory_outage_blocks_without_rebinding",
                    "directory_group_drift_requires_review",
                    "directory_conflict_desktop_recovery",
                    "directory_recovery_no_transfer_writes",
                    "directory_functional_users_non_admin",
                    "directory_intended_team_members",
                    "directory_ldap_outsider_denied",
                    "directory_ldap_reader_clone_allowed",
                    "directory_ldap_reader_push_denied",
                    "directory_ldap_writer_clone_allowed",
                    "directory_ldap_writer_push_allowed",
                    "directory_owners_isolated",
                    "directory_reader_unit_permissions",
                    "directory_selected_repository_relations",
                    "directory_writer_unit_permissions",
                    "structure_account_counts_unchanged",
                    "structure_phase_confirmed_without_bindings_or_memberships",
                    "late_full_plan_reused_structure_operations",
                ),
                True,
            ),
        },
        "application_plans": {
            "status": "passed",
            "assertions": dict.fromkeys(
                (
                    "confirmed_restart_no_duplicates",
                    "confirmed_repackage_no_duplicates",
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
                    "portable_metadata_confirmed",
                    "label_assignment_uses_mapped_label",
                    "attachment_parent_and_bytes_confirmed",
                    "release_author_and_asset_confirmed",
                    "metadata_update_same_ids_and_timestamps",
                    "metadata_update_replay_no_writes",
                    "label_and_release_collisions_blocked",
                    "release_tag_target_collision_blocked",
                    "metadata_omission_no_deletion",
                    "metadata_drift_blocks_overwrite",
                    "asset_lost_remote_post_accepted",
                    "asset_lost_restart_outcome_unknown",
                    "asset_lost_no_second_post",
                    "asset_lost_repackage_still_blocked",
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
        "gitea": {"source": PRIMARY_GITEA_VERSION, "target": PRIMARY_GITEA_VERSION},
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
                "documented_compose_installation": True,
            },
            "reference_compose": {
                "image_id": "sha256:" + "a" * 64,
                "exact_image": True,
                "healthy": True,
                "non_root": True,
                "aliases_readable": True,
                "installed_admin_security": True,
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
    report["openapi_and_migration"]["runtime_openapi"] = {
        "assertions": {name: True for name in report_contract().ADVANCED_METADATA_ASSERTIONS},
        "advanced_metadata": {
            "classification": json.loads(
                json.dumps(report_contract().ADVANCED_METADATA_CLASSIFICATION)
            )
        },
    }
    report["comment_transport_roundtrip"]["status"] = "passed"
    report["portable_transport_roundtrip"]["status"] = "passed"
    from scope_acceptance import build_scope_acceptance

    report["scope_acceptance"] = build_scope_acceptance(report)
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
                            "resolved_platform_digest": (
                                "sha256:1565a01ff935d56156285149acfce48ecc19c5d4e8ed278bebcf06f44e148295"
                            ),
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
        "directory_equal_login_realms_explicit_bindings",
        "directory_claim_drift_restart_blocked",
        "directory_ad_identity_drift_restart_blocked",
        "directory_outage_blocks_without_rebinding",
        "directory_group_drift_requires_review",
        "directory_conflict_desktop_recovery",
        "directory_recovery_no_transfer_writes",
        "directory_functional_users_non_admin",
        "directory_intended_team_members",
        "directory_ldap_outsider_denied",
        "directory_ldap_reader_clone_allowed",
        "directory_ldap_reader_push_denied",
        "directory_ldap_writer_clone_allowed",
        "directory_ldap_writer_push_allowed",
        "directory_owners_isolated",
        "directory_reader_unit_permissions",
        "directory_selected_repository_relations",
        "directory_writer_unit_permissions",
        "structure_account_counts_unchanged",
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


@pytest.mark.parametrize(
    "key", ["exact_image", "healthy", "non_root", "aliases_readable", "installed_admin_security"]
)
def test_documented_compose_installation_requires_each_fact(tmp_path: Path, key: str) -> None:
    module = report_contract()
    report = complete_probe_report()
    del report["oci_runtime"]["reference_compose"][key]
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize("image_id", [None, "sha256:" + "b" * 64])
def test_documented_compose_installation_must_use_runtime_image(
    tmp_path: Path, image_id: str | None
) -> None:
    module = report_contract()
    report = complete_probe_report()
    report["oci_runtime"]["reference_compose"]["image_id"] = image_id
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize("mode", ["first", "replay", "recovered_crash", "recovered_lost_response"])
def test_directory_bootstrap_requires_single_create(tmp_path, mode):
    module = report_contract()
    report = complete_probe_report()
    report["directory"]["local_application_bootstrap"][mode]["create_call_count"] = 2
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize(
    "key",
    [
        "disabled_filter_excluded_and_restored",
        "external_employee_number_changed_and_restored",
        "group_membership_changed_and_restored",
        "ldap_outage_failed_closed_and_restored",
        "wrong_ca_rejected_and_restored",
    ],
)
def test_directory_failure_qualification_is_mandatory(tmp_path, key):
    module = report_contract()
    report = complete_probe_report()
    del report["directory"][key]
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


def test_directory_unknown_result_requires_explicit_missing_confirmation(tmp_path):
    module = report_contract()
    report = complete_probe_report()
    del report["directory"]["local_application_bootstrap"]["recovered_crash"]["record"][
        "native_source_id"
    ]
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize(
    "key",
    [
        "first",
        "second",
        "missing_mail",
        "duplicate_login_conflict",
        "filter_escaping",
        "real_bind_and_wrong_password",
    ],
)
def test_application_directory_claim_evidence_is_required(tmp_path, key):
    module = report_contract()
    report = complete_probe_report()
    del report["directory"]["application_claim_reader"][key]
    assert not module.probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize(
    "section,key",
    [
        ("negative_assertions", "wrong_password"),
        ("negative_assertions", "wrong_ca"),
        ("negative_assertions", "disabled_filter"),
        ("credential_forms", "upn"),
        ("credential_forms", "domain_login"),
        ("claims", "email"),
        ("claims", "groups"),
    ],
)
def test_samba_ad_evidence_must_be_present(tmp_path: Path, section: str, key: str) -> None:
    report = complete_probe_report()
    del report["directory_ad"][section][key]
    assert not report_contract().probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize(
    "value", [None, [], ["CN=Direct,OU=Groups,DC=synth,DC=ad"], ["unexpected"]]
)
def test_nested_ad_server_evidence_is_required(tmp_path: Path, value: object) -> None:
    report = complete_probe_report()
    report["directory_ad"]["nested_matching_rule_results"] = value
    assert not report_contract().probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize("side", ["first", "second", "missing_mail"])
@pytest.mark.parametrize("value", [None, "", 12])
def test_ldap_dn_evidence_is_required(tmp_path: Path, side: str, value: object) -> None:
    report = complete_probe_report()
    report["directory"]["application_claim_reader"][side]["dn"] = value
    assert not report_contract().probe_contract_is_complete(prepare_results(tmp_path, report))


@pytest.mark.parametrize(
    "assertion",
    (
        "valid_login",
        "missing_directory_mail_login",
        "disabled_denied",
        "wrong_password_denied",
        "non_admin",
        "explicit_source",
        "native_alias_distinct",
    ),
)
def test_ad_gitea_login_evidence_cannot_be_missing(assertion: str) -> None:
    report = complete_probe_report()
    del report["directory_ad"]["gitea"]["assertions"][assertion]
    entries = report_contract().evidence_catalog(report, True)
    entry = next(item for item in entries if item["id"] == "separate_samba_ad_claim_qualification")
    assert entry["status"] != "passed"


@pytest.mark.parametrize("status", [None, "failed", "blocked", "not-implemented"])
def test_ad_gitea_failed_or_missing_status_cannot_pass(status: str | None) -> None:
    report = complete_probe_report()
    if status is None:
        del report["directory_ad"]["gitea"]["status"]
    else:
        report["directory_ad"]["gitea"]["status"] = status
    entries = report_contract().evidence_catalog(report, True)
    entry = next(item for item in entries if item["id"] == "separate_samba_ad_claim_qualification")
    assert entry["status"] == "failed"


@pytest.mark.parametrize("assertion", sorted(report_contract().ADVANCED_METADATA_ASSERTIONS))
def test_advanced_metadata_contract_assertions_are_mandatory(assertion: str) -> None:
    report = complete_probe_report()
    del report["openapi_and_migration"]["runtime_openapi"]["assertions"][assertion]
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "advanced_pull_review_timeline_reaction_contract"
    )
    assert entry["status"] == "failed"


def test_advanced_metadata_classification_cannot_silently_change() -> None:
    report = complete_probe_report()
    report["openapi_and_migration"]["runtime_openapi"]["advanced_metadata"]["classification"][
        "pull_requests"
    ]["first_import"] = "supported"
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "advanced_pull_review_timeline_reaction_contract"
    )
    assert entry["status"] == "failed"


def test_pull_content_version_live_evidence_is_mandatory() -> None:
    report = complete_probe_report()
    report["content_inventory"]["pull_content_version"]["stale_status"] = 200
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "selected_content_inventory_and_portable_plan"
    )
    assert entry["status"] == "failed"


def test_pull_merge_live_evidence_is_mandatory() -> None:
    report = complete_probe_report()
    report["content_inventory"]["pull_merge_evidence"]["timeline_merge_actor"] = "developer"
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "selected_content_inventory_and_portable_plan"
    )
    assert entry["status"] == "failed"


def test_pull_merge_null_field_must_be_explicit() -> None:
    report = complete_probe_report()
    del report["content_inventory"]["pull_merge_evidence"]["merged_by_response"]
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "selected_content_inventory_and_portable_plan"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    "assertion",
    (
        "missing_mail_binding_approved",
        "native_account_login_without_implicit_rights",
        "outage_blocks_without_rebinding",
        "ad_group_change_persists_conflict",
        "restored_groups_need_explicit_review",
        "disabled_subject_blocks",
        "enabled_subject_needs_explicit_review",
        "same_login_new_sid_blocks",
        "sid_conflict_survives_restart_and_review",
        "binding_unchanged",
        "no_mapped_writes",
    ),
)
def test_ad_identity_recovery_requires_every_assertion(assertion: str) -> None:
    report = complete_probe_report()
    del report["source_exports"]["ad_identity_recovery"]["assertions"][assertion]
    entries = report_contract().evidence_catalog(report, True)
    entry = next(item for item in entries if item["id"] == "separate_samba_ad_claim_qualification")
    assert entry["status"] == "failed"


@pytest.mark.parametrize("side", ["before", "after"])
@pytest.mark.parametrize("field", ["sid", "dn", "login"])
def test_ad_recreation_requires_observed_facts(side: str, field: str) -> None:
    report = complete_probe_report()
    del report["source_exports"]["ad_identity_recovery"]["recreated"][side][field]
    entries = report_contract().evidence_catalog(report, True)
    entry = next(item for item in entries if item["id"] == "separate_samba_ad_claim_qualification")
    assert entry["status"] == "failed"


@pytest.mark.parametrize("status", [None, "failed", "blocked", "not-implemented"])
def test_ad_recovery_failed_or_missing_status_cannot_pass(status: str | None) -> None:
    report = complete_probe_report()
    evidence = report["source_exports"]["ad_identity_recovery"]
    if status is None:
        del evidence["status"]
    else:
        evidence["status"] = status
    entries = report_contract().evidence_catalog(report, True)
    entry = next(item for item in entries if item["id"] == "separate_samba_ad_claim_qualification")
    assert entry["status"] == "failed"


@pytest.mark.parametrize("name", sorted(report_contract().INVENTORY_ASSERTIONS))
def test_inventory_report_requires_each_assertion(name: str) -> None:
    report = complete_probe_report()
    del report["content_inventory"]["assertions"][name]
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "selected_content_inventory_and_portable_plan"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    "change", ["missing", "zero", "unsupported-zero", "mismatch", "write", "browser"]
)
def test_inventory_partial_or_mismatched_evidence_cannot_pass(change: str) -> None:
    report = complete_probe_report()
    inventory = report["content_inventory"]
    if change == "missing":
        del inventory["counts"]["wiki"]
    elif change == "zero":
        inventory["counts"]["wiki"] = 0
    elif change == "unsupported-zero":
        inventory["counts"]["lfs"] = 0
    elif change == "mismatch":
        inventory["direct_counts"]["wiki"] = 2
    elif change == "write":
        inventory["source_writes_during_collection"] = 1
    else:
        del report["source_exports"]["assertions"]["guided_browser_inventory_bound_export"]
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "selected_content_inventory_and_portable_plan"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    "mutation",
    ["missing-class", "unverified", "complete-claim", "missing-limit", "missing-assertion"],
)
def test_scope_acceptance_cannot_hide_incomplete_or_unsupported_content(mutation: str) -> None:
    report = complete_probe_report()
    acceptance = report["scope_acceptance"]
    if mutation == "missing-class":
        acceptance["catalog"].pop()
    elif mutation == "unverified":
        acceptance["catalog"][0]["first_import_verified"] = False
    elif mutation == "complete-claim":
        acceptance["complete_transfer_claimed"] = True
    elif mutation == "missing-limit":
        acceptance["catalog"][0]["limitation"] = ""
    else:
        del acceptance["assertions"]["unsupported_classes_blocked_before_write"]
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "complete_agreed_scope_acceptance"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize("mutation", ["missing", "false"])
def test_native_payload_assertion_is_mandatory(tmp_path: Path, mutation: str) -> None:
    report = complete_probe_report()
    assertions = report["native_payloads"]["assertions"]
    if mutation == "missing":
        del assertions["explicit_digest_reconciliation"]
    else:
        assertions["explicit_digest_reconciliation"] = False
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "native_payload_capture_and_restart_safety"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    "mutation",
    ["wrong_crash_exit", "unknown_bypassed", "repeated_replay_upload", "failed_resolve"],
)
def test_native_payload_faults_cannot_pass(tmp_path: Path, mutation: str) -> None:
    report = complete_probe_report()
    native = report["native_payloads"]
    if mutation == "wrong_crash_exit":
        native["cases"]["crash"]["applied"]["exit_code"] = 87
    elif mutation == "unknown_bypassed":
        native["cases"]["lost"]["replay"]["operations"]["upload_lfs_object"] = "confirmed"
    elif mutation == "repeated_replay_upload":
        native["cases"]["normal"]["replay"]["lfs_uploads"] = 2
    else:
        native["cases"]["crash"]["resolved"]["plan_status"] = "failed"
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "native_payload_capture_and_restart_safety"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    "mutation",
    ["repackaged", "negative", "return_export", "return_import"],
)
def test_native_payload_subevidence_cannot_be_omitted_or_downgraded(
    tmp_path: Path, mutation: str
) -> None:
    report = complete_probe_report()
    native = report["native_payloads"]
    if mutation == "repackaged":
        del native["cases"]["normal"]["repackaged"]
    elif mutation == "negative":
        native["negatives"]["assertions"].pop("corrupt_lfs_payload_rejected")
    elif mutation == "return_export":
        native["return_export"]["files"].pop("lfs")
    else:
        native["return_import"]["application_return_implemented"] = True
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "native_payload_capture_and_restart_safety"
    )
    assert entry["status"] == "failed"


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_confirmation",
        "invalid_baseline",
        "invalid_returned",
        "invalid_drift",
        "wrong_digest",
        "wrong_count",
        "different_export_refs",
        "different_lfs",
        "unchanged_return",
    ],
)
def test_native_return_evidence_requires_valid_and_related_facts(mutation: str) -> None:
    report = complete_probe_report()
    native = report["native_payloads"]
    returned = native["return_import"]
    if mutation == "empty_confirmation":
        returned["confirmation"] = {}
    elif mutation == "invalid_baseline":
        returned["a_baseline"] = {"not-a-ref": "not-a-sha"}
    elif mutation == "invalid_returned":
        returned["returned_refs"] = {"refs/heads/main": 1}
    elif mutation == "invalid_drift":
        returned["drift_preserved"] = {"refs/heads/main": 2}
    elif mutation == "wrong_digest":
        returned["confirmation"]["refs_sha256"] = "0" * 64
    elif mutation == "wrong_count":
        returned["confirmation"]["ref_count"] = 2
    elif mutation == "different_export_refs":
        native["return_export"]["files"]["refs"] = {"refs/heads/main": "c" * 40}
    elif mutation == "different_lfs":
        native["return_export"]["files"]["lfs"]["oid"] = "d" * 64
    else:
        returned["a_baseline"] = returned["returned_refs"]
    entry = next(
        item
        for item in report_contract().evidence_catalog(report, True)
        if item["id"] == "native_payload_capture_and_restart_safety"
    )
    assert entry["status"] == "failed"
