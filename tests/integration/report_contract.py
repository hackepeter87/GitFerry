"""Build and validate the single machine-readable integration report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
PINNED_GITEA_IMAGE = (
    "docker.gitea.com/gitea:1.27.2@sha256:"
    "d20286ca2b2e170fdf628e7231b8a31a3220ade39ff462b55041d43d1fc757dd"
)


def _run(args: list[str]) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_digest() -> str:
    included = (
        ".github",
        "backend",
        "containers",
        "docs",
        "examples",
        "frontend/package-lock.json",
        "frontend/package.json",
        "frontend/src",
        "Makefile",
        "pyproject.toml",
        "schemas",
        "scripts",
        "tests",
    )
    digest = hashlib.sha256()
    for entry in included:
        path = ROOT / entry
        paths = (
            [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
        )
        for item in paths:
            relative = item.relative_to(ROOT).as_posix()
            if any(part in {"__pycache__", "node_modules", "dist"} for part in item.parts):
                continue
            digest.update(relative.encode("utf-8") + b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def initialize(results: Path, run_id: str, project: str) -> None:
    work = results / "work"
    work.mkdir(parents=True, exist_ok=True)
    git_head = _run(["git", "rev-parse", "HEAD"])
    dirty = bool(_run(["git", "status", "--porcelain", "--untracked-files=all"]))
    metadata = {
        "run_id": run_id,
        "compose_project": project,
        "started_at": _utc_now(),
        "code_state": {
            "git_head": git_head,
            "dirty": dirty,
            "workspace_sha256": _workspace_digest(),
        },
        "platform": {
            "host_system": platform.system(),
            "host_release": platform.release(),
            "host_architecture": platform.machine(),
            "python": platform.python_version(),
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
            "runner_os": os.environ.get("RUNNER_OS"),
            "runner_arch": os.environ.get("RUNNER_ARCH"),
        },
    }
    (work / "run-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )


def collect_runtime(results: Path, project: str, compose_file: Path) -> None:
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    server = json.loads(_run(["docker", "version", "--format", "{{json .Server}}"]))
    services: dict[str, Any] = {}
    for service in ("source", "target"):
        container_id = _run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(compose_file),
                "ps",
                "-q",
                service,
            ]
        )
        image_id = _run(["docker", "inspect", "--format", "{{.Image}}", container_id])
        configured = _run(["docker", "inspect", "--format", "{{.Config.Image}}", container_id])
        image = json.loads(_run(["docker", "image", "inspect", image_id]))[0]
        services[service] = {
            "configured_image": configured,
            "image_id": image_id,
            "os": image.get("Os"),
            "architecture": image.get("Architecture"),
            "repo_digests": image.get("RepoDigests", []),
        }
    image_arches = sorted({value["architecture"] for value in services.values()})
    server_arch = server.get("Arch")
    metadata["platform"].update(
        {
            "docker_server_os": server.get("Os"),
            "docker_server_architecture": server_arch,
            "docker_server_version": server.get("Version"),
            "gitea_image_architectures": image_arches,
            "emulation_observed": bool(
                server_arch and any(arch != server_arch for arch in image_arches)
            ),
        }
    )
    metadata["gitea_images"] = {
        "expected_pin": PINNED_GITEA_IMAGE,
        "services": services,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")


def _value(report: dict[str, Any], path: str) -> Any:
    current: Any = report
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _all_true(report: dict[str, Any], prefix: str) -> bool:
    value = _value(report, prefix)
    return isinstance(value, dict) and bool(value) and all(item is True for item in value.values())


def evidence_catalog(report: dict[str, Any], contract_gate: bool) -> list[dict[str, Any]]:
    """Map every requested regression to an executed assertion or focused unit test."""

    mandatory_sections = (
        "git_bundle",
        "metadata",
        "identity_and_permissions",
        "durable_provisioning",
        "metadata_roundtrip",
        "persistent_state_restart",
        "portable_transport_roundtrip",
        "comment_transport_roundtrip",
        "openapi_and_migration",
    )
    mandatory_sections_passed = (
        _value(report, "gitea.source") == "1.27.2"
        and _value(report, "gitea.target") == "1.27.2"
        and all(_value(report, f"{section}.status") == "passed" for section in mandatory_sections)
    )
    entries = [
        (
            "all_required_probe_sections_completed",
            mandatory_sections_passed,
            "tests/integration/probe.py",
            "gitea and all mandatory top-level probe status fields",
        ),
        (
            "manifest_v1_fixture_bytes_and_digests",
            contract_gate,
            "backend/tests/test_transport_manifest_v2.py::"
            "test_existing_v1_examples_validate_without_byte_or_digest_change",
            "focused contract gate",
        ),
        (
            "durable_structure_and_bundle_application_service",
            _all_true(report, "durable_provisioning.assertions"),
            "tests/integration/provisioning_probe.py",
            "durable_provisioning.assertions",
        ),
        (
            "provisioning_crash_and_lost_response_halt_replay",
            all(
                _value(report, f"durable_provisioning.assertions.{name}") is True
                for name in (
                    "crash_remote_post_succeeded",
                    "crash_restart_is_outcome_unknown",
                    "crash_replay_sent_no_second_post",
                    "lost_response_remote_post_succeeded",
                    "lost_response_is_outcome_unknown",
                    "new_packaging_cannot_bypass_unknown",
                )
            ),
            "tests/integration/provisioning_probe.py",
            "durable_provisioning.crash_probe and lost_response_probe",
        ),
        (
            "changed_a_comment_updates_same_a_comment",
            _value(
                report,
                "comment_transport_roundtrip.assertions.case_a_updated_same_comment",
            )
            is True,
            "tests/integration/comment_transport_roundtrip_probe.py",
            "comment_transport_roundtrip.assertions.case_a_updated_same_comment",
        ),
        (
            "new_b_comment_on_original_a_issue",
            _value(report, "comment_transport_roundtrip.assertions.case_b_b_origin_on_a_parent")
            is True,
            "tests/integration/comment_transport_roundtrip_probe.py",
            "comment_transport_roundtrip.assertions.case_b_b_origin_on_a_parent",
        ),
        (
            "child_first_b_issue_and_comment_executes_parent_first",
            _value(
                report,
                "comment_transport_roundtrip.assertions."
                "case_c_comment_before_issue_planned_parent_first",
            )
            is True,
            "tests/integration/comment_transport_roundtrip_probe.py",
            "comment_transport_roundtrip.assertions."
            "case_c_comment_before_issue_planned_parent_first",
        ),
        (
            "same_text_distinct_comment_origins",
            contract_gate,
            "backend/tests/test_comment_operations.py::"
            "test_identical_text_comments_keep_distinct_origins_and_targets",
            "focused contract gate",
        ),
        (
            "wrong_comment_parent_is_rejected",
            contract_gate,
            "backend/tests/test_comment_operations.py::"
            "test_missing_or_differently_parented_target_is_not_recreated",
            "focused contract gate",
        ),
        (
            "missing_author_binding_blocks_write",
            contract_gate,
            "backend/tests/test_comment_operations.py::"
            "test_missing_author_mapping_never_falls_back_to_importer",
            "focused contract gate",
        ),
        (
            "drift_and_stale_baseline_block_overwrite",
            _value(
                report,
                "comment_transport_roundtrip.assertions."
                "comment_drift_blocked_before_any_return_write",
            )
            is True
            and contract_gate,
            "tests/integration/comment_transport_roundtrip_probe.py and "
            "backend/tests/test_comment_operations.py::test_stale_or_wrong_parent_update_"
            "conflicts_without_patch",
            "comment_transport_roundtrip.assertions.comment_drift_blocked_before_any_return_write",
        ),
        (
            "replay_restart_repackage_and_unknown_do_not_duplicate",
            all(
                _value(report, f"comment_transport_roundtrip.assertions.{name}") is True
                for name in (
                    "forward_restart_no_duplicate",
                    "hard_crash_restart_no_post",
                    "hard_crash_has_verified_remote_acceptance_sync",
                    "lost_response_restart_no_post",
                    "lost_response_has_verified_remote_acceptance_sync",
                    "return_restart_no_duplicate",
                    "new_envelope_no_duplicate",
                )
            ),
            "tests/integration/comment_transport_roundtrip_probe.py",
            "comment_transport_roundtrip.assertions",
        ),
        (
            "unselected_control_objects_are_preserved",
            _value(
                report,
                "comment_transport_roundtrip.assertions.control_repository_change_preserved",
            )
            is True,
            "tests/integration/comment_transport_roundtrip_probe.py",
            "comment_transport_roundtrip.assertions.control_repository_change_preserved",
        ),
        (
            "blocked_parent_is_not_sent",
            contract_gate,
            "backend/tests/test_comment_operations.py::"
            "test_parent_block_survives_until_confirmed_mapping_and_no_early_post",
            "focused contract gate",
        ),
        (
            "unknown_comment_resolution_checks_parent_author_and_content",
            contract_gate,
            "backend/tests/test_comment_operations.py::"
            "test_unknown_comment_create_is_not_reposted_and_resolution_checks_parent",
            "focused contract gate",
        ),
        (
            "actual_importers_cannot_reach_opposite_gitea",
            _value(
                report,
                "portable_transport_roundtrip.assertions.separate_networks_block_cross_gitea",
            )
            is True,
            "tests/integration/transport_roundtrip_probe.py",
            "portable_transport_roundtrip.assertions.separate_networks_block_cross_gitea",
        ),
        (
            "all_issue_comment_roundtrip_assertions",
            _all_true(report, "comment_transport_roundtrip.assertions"),
            "tests/integration/comment_transport_roundtrip_probe.py",
            "comment_transport_roundtrip.assertions",
        ),
    ]
    return [
        {
            "id": identifier,
            "status": "passed" if passed else "failed",
            "test": test,
            "report_evidence": evidence,
        }
        for identifier, passed, test, evidence in entries
    ]


def probe_contract_is_complete(results: Path) -> bool:
    report_path = results / "report.json"
    if not report_path.is_file():
        return False
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(report, dict):
        return False
    contract_gate = (results / "work" / "contract-gate.passed").is_file()
    return all(entry["status"] == "passed" for entry in evidence_catalog(report, contract_gate))


def finalize(
    results: Path,
    *,
    run_status: int,
    cleanup_status: int,
    phase: str,
    diagnostics_captured: bool,
) -> bool:
    report_path = results / "report.json"
    report_available = report_path.is_file()
    try:
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_available else {}
        if not isinstance(report, dict):
            raise ValueError("report root is not an object")
    except (OSError, ValueError, json.JSONDecodeError):
        report = {}
        report_available = False
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    contract_gate = (results / "work" / "contract-gate.passed").is_file()
    catalog = evidence_catalog(report, contract_gate)
    failed = [entry["id"] for entry in catalog if entry["status"] != "passed"]
    complete = report_available and run_status == 0 and cleanup_status == 0 and not failed
    execution = {
        **metadata,
        "finished_at": _utc_now(),
        "phase_reached": phase,
        "status": "passed" if complete else "failed",
        "probe_exit_code": run_status,
        "probe_report_available": report_available,
        "focused_contract_gate": "passed" if contract_gate else "not-passed",
        "required_checks": catalog,
        "failed_or_not_executed_checks": failed,
        "diagnostics_captured": diagnostics_captured,
        "cleanup": {
            "status": "passed" if cleanup_status == 0 else "failed",
            "exit_code": cleanup_status,
            "scope": metadata["compose_project"],
            "only_run_scoped_resources": True,
        },
    }
    report["execution"] = execution
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return complete


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "collect-runtime", "check", "finalize"))
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--project")
    parser.add_argument("--compose-file", type=Path)
    parser.add_argument("--run-status", type=int)
    parser.add_argument("--cleanup-status", type=int)
    parser.add_argument("--phase", default="unknown")
    parser.add_argument("--diagnostics-captured", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        if not args.run_id or not args.project:
            parser.error("init requires --run-id and --project")
        initialize(args.results, args.run_id, args.project)
        return 0
    if args.command == "collect-runtime":
        if not args.project or args.compose_file is None:
            parser.error("collect-runtime requires --project and --compose-file")
        collect_runtime(args.results, args.project, args.compose_file)
        return 0
    if args.command == "check":
        return 0 if probe_contract_is_complete(args.results) else 1
    if args.run_status is None or args.cleanup_status is None:
        parser.error("finalize requires --run-status and --cleanup-status")
    return (
        0
        if finalize(
            args.results,
            run_status=args.run_status,
            cleanup_status=args.cleanup_status,
            phase=args.phase,
            diagnostics_captured=args.diagnostics_captured,
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
