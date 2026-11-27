"""Build and validate the single machine-readable integration report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gitea_compat import (
    PRIMARY_GITEA_IMAGE,
    PRIMARY_GITEA_PLATFORM_DIGESTS,
    PRIMARY_GITEA_VERSION,
)
from scope_acceptance import build_scope_acceptance

ROOT = Path(__file__).parents[2]
PINNED_GITEA_IMAGE = PRIMARY_GITEA_IMAGE

OCI_RUNTIME_ASSERTION_NAMES = frozenset(
    {
        "non_root_user",
        "version_label",
        "revision_label",
        "license_label",
        "healthcheck_present",
        "graceful_stop_signal",
        "fresh_volume_initialized",
        "packaged_frontend_served",
        "installed_admin_security",
        "runtime_hardening",
        "container_replacement_preserved_state",
        "identity_binding_preserved",
        "object_mapping_preserved",
        "baseline_preserved",
        "outcome_unknown_preserved",
        "operation_events_preserved",
        "conflict_preserved",
        "package_and_work_files_preserved",
        "graceful_shutdown_observed",
        "forced_restart_recovered_sqlite",
        "newer_schema_failed_closed",
        "corrupt_database_failed_closed",
        "read_only_directory_failed_closed",
        "symlink_path_failed_closed",
        "documented_compose_installation",
    }
)

REFERENCE_COMPOSE_ASSERTION_NAMES = frozenset(
    {"exact_image", "healthy", "non_root", "aliases_readable", "installed_admin_security"}
)


INVENTORY_ASSERTIONS = frozenset(
    {
        "explicit_empty_repository",
        "all_classes_explicit",
        "direct_counts_match",
        "native_identity_and_parents",
        "payload_sizes_positive",
        "read_only_collection",
        "control_unread_unchanged",
        "qualified_empty_reactions",
        "distinct_review_authors_and_equal_text_origins",
        "pull_refs_state_review_positions_and_timeline_order",
        "review_reply_and_resolution_routes",
        "pull_merged_state_and_approval_decision",
        "inline_reply_authors_and_resolution_transitions",
        "pull_content_version_rejects_stale_without_mutation",
        "unsupported_counts_unknown",
    }
)
INVENTORY_OBSERVED = frozenset(
    {
        "attachments",
        "comments",
        "git_refs",
        "identities",
        "issues",
        "labels",
        "milestones",
        "organizations",
        "permissions",
        "pull_comments",
        "pull_requests",
        "reactions",
        "release_assets",
        "releases",
        "review_comments",
        "reviews",
        "teams",
        "timelines",
        "wiki",
    }
)

NATIVE_PAYLOAD_ASSERTIONS = frozenset(
    {
        "source_inventory_and_v3_capture",
        "independent_state_file_only_transfer",
        "native_lfs_digest_and_wiki_history",
        "confirmed_restart_zero_writes",
        "remote_success_before_crash",
        "lost_success_response_persistent_unknown",
        "unknown_restart_no_second_upload",
        "explicit_digest_reconciliation",
        "new_package_preserves_operation_identity",
        "negative_payload_prerequisites",
        "native_return_and_selected_ref_conflict",
    }
)

NATIVE_NEGATIVE_ASSERTIONS = frozenset(
    {
        "missing_lfs_is_blocked_unknown",
        "missing_lfs_export_refuses_blocked_inventory",
        "uninitialized_wiki_blocks_without_false_empty_claim",
        "disabled_wiki_is_blocked_unknown",
        "missing_lfs_payload_rejected",
        "corrupt_lfs_payload_rejected",
        "source_observation_is_write_free",
    }
)

ADVANCED_METADATA_ASSERTIONS = frozenset(
    {
        "pull_create_requires_refs_without_author_or_time",
        "pull_response_exposes_refs_identity_author_and_times",
        "pull_patch_exposes_content_version_guard",
        "review_create_accepts_nested_positions_without_author_or_time",
        "review_response_exposes_identity_author_state_and_times",
        "inline_response_exposes_review_parent_positions_author_and_times",
        "review_comments_have_no_update_or_delete_route",
        "timeline_is_read_only",
        "reaction_contract_is_natural_key_without_native_id",
    }
)

ADVANCED_METADATA_CLASSIFICATION = {
    "pull_requests": {
        "inventory": "supported",
        "first_import": "blocked-required-refs-and-author-fidelity",
        "return_update": "not-supported-no-adapter-despite-content-version",
        "deletion": "not-supported",
    },
    "reviews": {
        "inventory": "supported",
        "first_import": "blocked-commit-and-author-fidelity",
        "return_update": "not-supported",
        "deletion": "draft-review-only-not-portable",
    },
    "inline_review_comments": {
        "inventory": "supported-without-reply-parent",
        "first_import": "blocked-review-parent-and-diff-position",
        "return_update": "not-supported-resolve-transitions-only",
        "deletion": "not-supported",
    },
    "timelines": {
        "inventory": "supported",
        "first_import": "not-supported-read-only-api",
        "return_update": "not-supported-read-only-api",
        "deletion": "not-supported-read-only-api",
    },
    "reactions": {
        "inventory": "supported-with-natural-key",
        "first_import": "blocked-no-native-id-or-historical-time",
        "return_update": "not-applicable-create-delete-set",
        "deletion": "available-but-not-enabled",
    },
}

SCOPE_ACCEPTANCE_CLASSES = frozenset(
    {
        "attachments",
        "comments",
        "git_refs",
        "identities",
        "issues",
        "labels",
        "lfs",
        "milestones",
        "organizations",
        "other_content",
        "permissions",
        "pull_comments",
        "pull_requests",
        "reactions",
        "release_assets",
        "releases",
        "review_comments",
        "reviews",
        "teams",
        "timelines",
        "wiki",
    }
)
SCOPE_ACCEPTANCE_ASSERTIONS = frozenset(
    {
        "exact_gitea_baseline",
        "all_classes_catalogued",
        "inventory_counts_reconciled",
        "supported_first_imports_reconciled",
        "unsupported_classes_blocked_before_write",
        "omission_never_deletes",
        "control_repository_unchanged",
        "desktop_inventory_reviewed",
    }
)


def _inventory_evidence(report: dict[str, Any]) -> bool:
    counts = _value(report, "content_inventory.counts")
    pull_guard = _value(report, "content_inventory.pull_content_version")
    pull_merge = _value(report, "content_inventory.pull_merge_evidence")
    return (
        _value(report, "content_inventory.status") == "passed"
        and _value(report, "content_inventory.gitea_version") == PRIMARY_GITEA_VERSION
        and _all_required_true(report, "content_inventory.assertions", INVENTORY_ASSERTIONS)
        and isinstance(counts, dict)
        and set(counts) == INVENTORY_OBSERVED | {"lfs", "other_content"}
        and counts == _value(report, "content_inventory.direct_counts")
        and all(type(counts.get(key)) is int and counts[key] > 0 for key in INVENTORY_OBSERVED)
        and counts.get("lfs") is None
        and counts.get("other_content") is None
        and _value(report, "content_inventory.source_writes_during_collection") == 0
        and isinstance(pull_guard, dict)
        and pull_guard.get("accepted_status") in (200, 201)
        and pull_guard.get("stale_status") == 409
        and type(pull_guard.get("before")) is int
        and type(pull_guard.get("after")) is int
        and pull_guard["after"] > pull_guard["before"]
        and pull_guard.get("stale_title_and_body_unchanged") is True
        and isinstance(pull_merge, dict)
        and pull_merge.get("state") == "closed"
        and pull_merge.get("merged") is True
        and isinstance(pull_merge.get("closed_at"), str)
        and isinstance(pull_merge.get("merged_at"), str)
        and isinstance(pull_merge.get("merge_commit_sha"), str)
        and bool(pull_merge["merge_commit_sha"])
        and "merged_by_response" in pull_merge
        and pull_merge["merged_by_response"] is None
        and pull_merge.get("timeline_merge_actor") == "gitferry-admin"
    )


def _advanced_metadata_evidence(report: dict[str, Any]) -> bool:
    contract = _value(report, "openapi_and_migration.runtime_openapi.advanced_metadata")
    classifications = contract.get("classification") if isinstance(contract, dict) else None
    return (
        _all_required_true(
            report,
            "openapi_and_migration.runtime_openapi.assertions",
            ADVANCED_METADATA_ASSERTIONS,
        )
        and classifications == ADVANCED_METADATA_CLASSIFICATION
    )


def _scope_acceptance_evidence(report: dict[str, Any]) -> bool:
    actual = report.get("scope_acceptance")
    expected = build_scope_acceptance(report)
    return (
        actual == expected
        and expected["status"] == "passed"
        and len(expected["catalog"]) == len(SCOPE_ACCEPTANCE_CLASSES)
        and set(expected["assertions"]) == SCOPE_ACCEPTANCE_ASSERTIONS
    )


def _native_refs(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and 0 < len(value) <= 10000
        and all(
            isinstance(ref, str)
            and ref.startswith("refs/")
            and not ref.endswith(("/", "."))
            and not any(mark in ref for mark in ("..", "@{", "//"))
            and not any(ord(c) <= 32 or ord(c) == 127 or c in "~^:?*[\\" for c in ref)
            and all(
                part and not part.startswith(".") and not part.endswith(".lock")
                for part in ref.split("/")
            )
            and isinstance(sha, str)
            and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha)
            for ref, sha in value.items()
        )
    )


def _native_confirmation(refs: dict[str, str]) -> dict[str, Any]:
    raw = json.dumps(refs, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"ref_count": len(refs), "refs_sha256": hashlib.sha256(raw).hexdigest()}


def _native_payload_evidence(report: dict[str, Any]) -> bool:
    """Require the live native payload probe's complete, bounded result shape."""

    evidence = _value(report, "native_payloads")
    browser = _value(report, "source_exports.native_payloads")
    browser_request_version = _value(
        report, "source_exports.source_browser.inventory_request_version"
    )
    if not (
        isinstance(evidence, dict)
        and evidence.get("status") == "passed"
        and evidence.get("gitea_version") == PRIMARY_GITEA_VERSION
        and _all_required_true(report, "native_payloads.assertions", NATIVE_PAYLOAD_ASSERTIONS)
        and set(evidence.get("assertions", {})) == NATIVE_PAYLOAD_ASSERTIONS
        and isinstance(browser, dict)
        and browser.get("status") == "passed"
        and isinstance(browser.get("lfs"), dict)
        and len(browser["lfs"]) == 2
        and all(
            isinstance(oid, str)
            and re.fullmatch(r"[0-9a-f]{64}", oid)
            and type(size) is int
            and size > 0
            for oid, size in browser["lfs"].items()
        )
        and isinstance(browser.get("wiki"), dict)
        and len(browser["wiki"]) == 2
        and all(
            isinstance(scope, str) and isinstance(refs, dict)
            for scope, refs in browser["wiki"].items()
        )
        and all(
            refs
            and all(
                isinstance(ref, str)
                and ref.startswith("refs/")
                and isinstance(sha, str)
                and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha)
                for ref, sha in refs.items()
            )
            for refs in browser["wiki"].values()
        )
        and isinstance(browser_request_version, int)
        and browser_request_version == 3
    ):
        return False
    negatives = evidence.get("negatives")
    return_export = evidence.get("return_export")
    return_import = evidence.get("return_import")
    if not (
        isinstance(negatives, dict)
        and negatives.get("status") == "passed"
        and isinstance(negatives.get("assertions"), dict)
        and set(negatives["assertions"]) == NATIVE_NEGATIVE_ASSERTIONS
        and all(value is True for value in negatives["assertions"].values())
        and isinstance(return_export, dict)
        and return_export.get("status") == "passed"
        and return_export.get("application_return_blocked") is True
        and isinstance(return_export.get("files"), dict)
        and isinstance(return_export["files"].get("lfs"), dict)
        and isinstance(return_export["files"]["lfs"].get("oid"), str)
        and re.fullmatch(r"[0-9a-f]{64}", return_export["files"]["lfs"]["oid"])
        and type(return_export["files"]["lfs"].get("size")) is int
        and return_export["files"]["lfs"]["size"] > 0
        and _native_refs(return_export["files"].get("refs"))
        and isinstance(return_import, dict)
        and return_import.get("status") == "passed"
        and return_import.get("scope") == "native adapter feasibility only"
        and return_import.get("application_return_implemented") is False
        and return_import.get("isolation_checked") is True
        and return_import.get("control_refs_unchanged") is True
        and isinstance(return_import.get("lfs_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", return_import["lfs_sha256"])
        and _native_refs(return_import.get("a_baseline"))
        and _native_refs(return_import.get("returned_refs"))
        and _native_refs(return_import.get("drift_preserved"))
        and return_import["a_baseline"] != return_import["returned_refs"]
        and return_export["files"]["refs"] == return_import["returned_refs"]
        and return_export["files"]["lfs"]["oid"] == return_import["lfs_sha256"]
        and return_import["returned_refs"] != return_import["drift_preserved"]
        and isinstance(return_import.get("confirmation"), dict)
        and type(return_import["confirmation"].get("ref_count")) is int
        and return_import["confirmation"] == _native_confirmation(return_import["returned_refs"])
    ):
        return False
    cases = evidence.get("cases")
    if not isinstance(cases, dict) or set(cases) != {"normal", "crash", "lost"}:
        return False
    for case_name, case in cases.items():
        if not isinstance(case, dict):
            return False
        source = case.get("source")
        applied = case.get("applied")
        replay = case.get("replay")
        repackaged = case.get("repackaged")
        if not (
            isinstance(source, dict)
            and source.get("inventory_status") == "ready"
            and isinstance(applied, dict)
            and isinstance(replay, dict)
            and isinstance(repackaged, dict)
            and replay.get("source_state_absent") is True
            and replay.get("isolation_checked") is True
            and replay.get("remote_writes") == 0
            and replay.get("lfs_uploads") == 1
        ):
            return False
        if not (
            repackaged.get("remote_writes") == 0
            and repackaged.get("lfs_uploads") == 1
            and repackaged.get("operations") == replay.get("operations")
        ):
            return False
        if case_name == "normal":
            if not (
                applied.get("wiki_refs_and_content_match") is True
                and applied.get("lfs_remote_bytes_verified") is True
                and replay.get("plan_status") == "confirmed"
            ):
                return False
        elif case_name == "crash":
            if not (
                applied.get("exit_code") == 88
                and replay.get("after_remote_crash_marker") is True
                and replay.get("lfs_remote_bytes_verified") is True
            ):
                return False
        else:
            if applied.get("response_lost_marker") is not True:
                return False
        if case_name != "normal":
            operations = replay.get("operations")
            resolved = case.get("resolved")
            if not (
                isinstance(operations, dict)
                and operations.get("upload_lfs_object") == "outcome_unknown"
                and isinstance(resolved, dict)
                and resolved.get("plan_status") == "confirmed"
                and resolved.get("lfs_uploads") == 1
            ):
                return False
    return True


def _run(args: list[str]) -> str:
    return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _workspace_digest() -> str:
    included = (
        ".github",
        "backend",
        "containers",
        "deploy",
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
        "github": (
            {
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "source_sha": os.environ.get("GITFERRY_CI_SOURCE_SHA"),
                "checkout_sha": os.environ.get("GITHUB_SHA"),
                "ref": os.environ.get("GITHUB_REF"),
                "event_name": os.environ.get("GITHUB_EVENT_NAME"),
                "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF"),
                "job": os.environ.get("GITHUB_JOB"),
            }
            if os.environ.get("GITHUB_ACTIONS") == "true"
            else None
        ),
    }
    (work / "run-metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8"
    )


def collect_runtime(results: Path, project: str, compose_file: Path) -> None:
    metadata_path = results / "work" / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    server = json.loads(_run(["docker", "version", "--format", "{{json .Server}}"]))
    index = json.loads(
        _run(["docker", "buildx", "imagetools", "inspect", "--raw", PINNED_GITEA_IMAGE])
    )
    manifests = index.get("manifests") if isinstance(index, dict) else None
    if not isinstance(manifests, list):
        raise RuntimeError("pinned Gitea image did not resolve to a multi-platform OCI index")
    resolved: dict[str, str] = {}
    for manifest in manifests:
        platform_value = manifest.get("platform") if isinstance(manifest, dict) else None
        digest = manifest.get("digest") if isinstance(manifest, dict) else None
        if not isinstance(platform_value, dict) or not isinstance(digest, str):
            continue
        os_name = platform_value.get("os")
        architecture = platform_value.get("architecture")
        if isinstance(os_name, str) and isinstance(architecture, str):
            resolved[f"{os_name}/{architecture}"] = digest
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
        platform_key = f"{image.get('Os')}/{image.get('Architecture')}"
        services[service] = {
            "configured_image": configured,
            "image_id": image_id,
            "os": image.get("Os"),
            "architecture": image.get("Architecture"),
            "repo_digests": image.get("RepoDigests", []),
            "resolved_platform_digest": resolved.get(platform_key),
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


def run_metadata_is_complete(metadata: dict[str, Any]) -> bool:
    code_state = metadata.get("code_state")
    platform_data = metadata.get("platform")
    images = metadata.get("gitea_images")
    if not (
        isinstance(metadata.get("run_id"), str)
        and metadata["run_id"]
        and isinstance(metadata.get("compose_project"), str)
        and metadata["compose_project"].startswith("gitferry-it-")
        and isinstance(code_state, dict)
        and isinstance(code_state.get("git_head"), str)
        and len(code_state["git_head"]) == 40
        and isinstance(code_state.get("dirty"), bool)
        and isinstance(code_state.get("workspace_sha256"), str)
        and len(code_state["workspace_sha256"]) == 64
        and isinstance(platform_data, dict)
    ):
        return False
    platform_fields = (
        "host_system",
        "host_release",
        "host_architecture",
        "python",
        "docker_server_os",
        "docker_server_architecture",
        "docker_server_version",
    )
    if not all(
        isinstance(platform_data.get(field), str) and platform_data[field]
        for field in platform_fields
    ):
        return False
    if not (
        isinstance(platform_data.get("github_actions"), bool)
        and isinstance(platform_data.get("gitea_image_architectures"), list)
        and platform_data["gitea_image_architectures"]
        and isinstance(platform_data.get("emulation_observed"), bool)
        and isinstance(images, dict)
        and images.get("expected_pin") == PINNED_GITEA_IMAGE
        and isinstance(images.get("services"), dict)
    ):
        return False
    for service_name in ("source", "target"):
        service = images["services"].get(service_name)
        if not isinstance(service, dict):
            return False
        for field in ("configured_image", "image_id", "os", "architecture"):
            if not isinstance(service.get(field), str) or not service[field]:
                return False
        expected_digest = PINNED_GITEA_IMAGE.split("@", 1)[1]
        platform_key = f"{service['os']}/{service['architecture']}"
        expected_platform_digest = PRIMARY_GITEA_PLATFORM_DIGESTS.get(platform_key)
        repo_digests = service.get("repo_digests")
        if not (
            service["configured_image"] == PINNED_GITEA_IMAGE
            and re.fullmatch(r"sha256:[0-9a-f]{64}", service["image_id"])
            and isinstance(repo_digests, list)
            and any(
                isinstance(digest, str) and digest.endswith(f"@{expected_digest}")
                for digest in repo_digests
            )
            and expected_platform_digest is not None
            and service.get("resolved_platform_digest") == expected_platform_digest
        ):
            return False
    if platform_data["github_actions"]:
        github = metadata.get("github")
        if not isinstance(github, dict):
            return False
        for field in (
            "run_id",
            "run_attempt",
            "source_sha",
            "checkout_sha",
            "ref",
            "event_name",
            "workflow_ref",
            "job",
        ):
            if not isinstance(github.get(field), str) or not github[field]:
                return False
        if (
            not re.fullmatch(r"[0-9a-f]{40}", github["source_sha"])
            or not re.fullmatch(r"[0-9a-f]{40}", github["checkout_sha"])
            or github["checkout_sha"] != code_state["git_head"]
            or (
                github["event_name"] != "pull_request"
                and github["source_sha"] != github["checkout_sha"]
            )
            or code_state["dirty"]
        ):
            return False
    return True


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


def _all_required_true(report: dict[str, Any], prefix: str, required_names: frozenset[str]) -> bool:
    value = _value(report, prefix)
    return (
        isinstance(value, dict)
        and required_names.issubset(value)
        and all(value[name] is True for name in required_names)
    )


def evidence_catalog(report: dict[str, Any], contract_gate: bool) -> list[dict[str, Any]]:
    """Map every requested regression to an executed assertion or focused unit test."""

    mandatory_sections = (
        "directory",
        "directory_ad",
        "connection_profiles",
        "application_plans",
        "source_exports",
        "git_bundle",
        "metadata",
        "identity_and_permissions",
        "durable_provisioning",
        "metadata_roundtrip",
        "persistent_state_restart",
        "portable_transport_roundtrip",
        "comment_transport_roundtrip",
        "openapi_and_migration",
        "oci_runtime",
        "scope_acceptance",
    )
    mandatory_sections_passed = (
        _value(report, "gitea.source") == PRIMARY_GITEA_VERSION
        and _value(report, "gitea.target") == PRIMARY_GITEA_VERSION
        and all(_value(report, f"{section}.status") == "passed" for section in mandatory_sections)
    )
    entries = [
        (
            "complete_agreed_scope_acceptance",
            _scope_acceptance_evidence(report),
            "tests/integration/scope_acceptance.py",
            "scope_acceptance",
        ),
        (
            "selected_content_inventory_and_portable_plan",
            _inventory_evidence(report)
            and _value(report, "source_exports.assertions.guided_browser_inventory_bound_export")
            is True,
            "tests/integration/content_inventory_probe.py",
            "content_inventory",
        ),
        (
            "advanced_pull_review_timeline_reaction_contract",
            _advanced_metadata_evidence(report),
            "tests/integration/openapi_migration_probe.py",
            "openapi_and_migration.runtime_openapi.advanced_metadata",
        ),
        (
            "native_payload_capture_and_restart_safety",
            _native_payload_evidence(report),
            "tests/integration/native_payload_probe.py",
            "native_payloads",
        ),
        (
            "separate_samba_ad_claim_qualification",
            _value(report, "directory_ad.status") == "passed"
            and _value(report, "directory_ad.client") == "ldap3 2.9.1"
            and _value(report, "directory_ad.gitea.status") == "passed"
            and _value(report, "source_exports.ad_identity_recovery.status") == "passed"
            and _all_required_true(
                report,
                "source_exports.ad_identity_recovery.assertions",
                frozenset(
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
                    )
                ),
            )
            and all(
                isinstance(value, str) and bool(value)
                for side in ("before", "after")
                for field in ("sid", "dn", "login")
                for value in (
                    _value(report, f"source_exports.ad_identity_recovery.recreated.{side}.{field}"),
                )
            )
            and _value(report, "source_exports.ad_identity_recovery.recreated.before.login")
            == _value(report, "source_exports.ad_identity_recovery.recreated.after.login")
            and _value(report, "source_exports.ad_identity_recovery.recreated.before.sid")
            != _value(report, "source_exports.ad_identity_recovery.recreated.after.sid")
            and _value(report, "source_exports.ad_identity_recovery.recreated.before.dn")
            == _value(report, "source_exports.ad_identity_recovery.recreated.after.dn")
            and _all_required_true(
                report,
                "directory_ad.gitea.assertions",
                frozenset(
                    (
                        "valid_login",
                        "missing_directory_mail_login",
                        "disabled_denied",
                        "wrong_password_denied",
                        "non_admin",
                        "explicit_source",
                        "native_alias_distinct",
                    )
                ),
            )
            and bool(_value(report, "directory_ad.image_id"))
            and bool(_value(report, "directory_ad.platform"))
            and str(_value(report, "directory_ad.server_version")).startswith("Version 4.22.11")
            and _value(report, "directory_ad.claims.dn") == "CN=ldap-same,OU=People,DC=synth,DC=ad"
            and _value(report, "directory_ad.claims.ou_path") == "OU=People,DC=synth,DC=ad"
            and _value(report, "directory_ad.nested_matching_rule_results")
            in (
                ["CN=Direct,OU=Groups,DC=synth,DC=ad", "CN=Nested,OU=Groups,DC=synth,DC=ad"],
                ["CN=Nested,OU=Groups,DC=synth,DC=ad", "CN=Direct,OU=Groups,DC=synth,DC=ad"],
            )
            and _value(report, "directory_ad.claims.external_id_kind") == "sid"
            and str(_value(report, "directory_ad.claims.external_id")).startswith("S-1-5-21-")
            and _value(report, "directory_ad.claims.groups")
            == ["CN=Direct,OU=Groups,DC=synth,DC=ad"]
            and isinstance(_value(report, "directory_ad.claims"), dict)
            and "email" in _value(report, "directory_ad.claims")
            and _value(report, "directory_ad.claims.email") is None
            and all(
                _value(report, "directory_ad.negative_assertions." + key) == "passed"
                for key in ("wrong_password", "wrong_ca", "disabled_filter")
            )
            and all(
                _value(report, "directory_ad.credential_forms." + key) is True
                for key in ("upn", "domain_login")
            ),
            "tests/integration/ad_probe.py",
            "directory_ad",
        ),
        (
            "real_application_directory_claim_reader",
            _value(report, "directory.application_claim_reader.status") == "passed"
            and _value(report, "directory.application_claim_reader.client") == "ldap3 2.9.1"
            and all(
                _value(report, "directory.application_claim_reader." + key) is True
                for key in (
                    "duplicate_login_conflict",
                    "filter_escaping",
                    "real_bind_and_wrong_password",
                )
            )
            and all(
                isinstance(
                    _value(report, "directory.application_claim_reader." + side + ".dn"), str
                )
                and bool(_value(report, "directory.application_claim_reader." + side + ".dn"))
                for side in ("first", "second", "missing_mail")
            )
            and _value(report, "directory.application_claim_reader.first.login") == "ldap-reader"
            and _value(report, "directory.application_claim_reader.second.login") == "ldap-reader"
            and _value(report, "directory.application_claim_reader.first.external_id")
            == "SYNTHETIC-EXT-10001"
            and _value(report, "directory.application_claim_reader.second.external_id")
            == "SYNTHETIC-EXT-20001"
            and _value(report, "directory.application_claim_reader.first.directory_realm")
            == "directory-a"
            and _value(report, "directory.application_claim_reader.second.directory_realm")
            == "directory-b"
            and all(
                isinstance(
                    _value(report, "directory.application_claim_reader." + side + ".ou_path"), str
                )
                and bool(_value(report, "directory.application_claim_reader." + side + ".ou_path"))
                for side in ("first", "second")
            )
            and _value(report, "directory.application_claim_reader.first.ou_path")
            != _value(report, "directory.application_claim_reader.second.ou_path")
            and _value(report, "directory.application_claim_reader.first.groups")
            == ["cn=gitea-users,ou=Groups,dc=example,dc=invalid"]
            and isinstance(_value(report, "directory.application_claim_reader.missing_mail"), dict)
            and "email" in _value(report, "directory.application_claim_reader.missing_mail")
            and _value(report, "directory.application_claim_reader.missing_mail.email") is None,
            "tests/integration/directory_claim_worker.py",
            "directory.application_claim_reader",
        ),
        (
            "local_directory_bootstrap_restart_and_uncertainty",
            all(
                _value(report, "directory.local_application_bootstrap." + mode + ".status")
                == "passed"
                and _value(
                    report, "directory.local_application_bootstrap." + mode + ".create_call_count"
                )
                == 1
                for mode in ("first", "replay", "recovered_crash", "recovered_lost_response")
            )
            and _value(report, "directory.local_application_bootstrap.crash.exit_code") == 73
            and all(
                _value(report, "directory.local_application_bootstrap." + mode + ".record.status")
                == "outcome_unknown"
                and "native_source_id"
                in _value(report, "directory.local_application_bootstrap." + mode + ".record")
                and _value(
                    report,
                    "directory.local_application_bootstrap." + mode + ".record.native_source_id",
                )
                is None
                for mode in ("recovered_crash", "recovered_lost_response")
            )
            and _value(report, "directory.local_application_bootstrap.changed.status") == "passed",
            "tests/integration/directory_bootstrap_worker.py",
            "directory.local_application_bootstrap",
        ),
        (
            "real_ldap_simple_authentication",
            _value(report, "directory.status") == "passed"
            and _value(report, "directory.gitea_version") == PRIMARY_GITEA_VERSION
            and all(
                _value(report, "directory." + key) is True
                for key in (
                    "valid_password_login",
                    "wrong_password_rejected",
                    "wrong_ca_rejected_and_restored",
                    "disabled_filter_excluded_and_restored",
                    "external_employee_number_changed_and_restored",
                    "group_membership_changed_and_restored",
                    "ldap_outage_failed_closed_and_restored",
                    "ldap_user_not_administrator",
                    "same_login_collision_ldap_rejected",
                    "authenticated_account_readback",
                )
            )
            and _value(report, "directory.directory_facts.duplicate_uid_scopes") is True
            and bool(_value(report, "directory.missing_mail_native_email")),
            "tests/integration/directory_probe.py",
            "directory",
        ),
        (
            "source_export_capture_and_offline_file_receipt",
            _all_required_true(
                report,
                "source_exports.assertions",
                frozenset(
                    {
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
                        "structure_phase_confirmed_without_bindings_or_memberships",
                        "late_full_plan_reused_structure_operations",
                        "git_return_file_only",
                        "git_return_ref_object_exact",
                        "git_return_existing_branch_fast_forward",
                        "git_return_selected_drift_blocks_before_push",
                        "git_return_protected_ref_blocks_before_push",
                        "git_return_lost_response_requires_exact_recovery",
                        "git_return_replay_restart_no_duplicate",
                        "git_return_control_and_network_isolation",
                    }
                ),
            )
            and bool(_value(report, "source_exports.image_id"))
            and _value(report, "source_exports.image_id") == _value(report, "oci_runtime.image_id"),
            "tests/integration/source_export_probe.py",
            "source_exports.assertions",
        ),
        (
            "durable_application_plan_and_remote_interruptions",
            _all_required_true(
                report,
                "application_plans.assertions",
                frozenset(
                    {
                        "structure_bundle_and_metadata_confirmed",
                        "distinct_native_authors",
                        "parent_mapping_confirmed",
                        "target_only_context",
                        "confirmed_restart_no_duplicates",
                        "crash_remote_post_accepted",
                        "crash_restart_outcome_unknown",
                        "crash_no_second_post",
                        "lost_remote_post_accepted",
                        "lost_restart_outcome_unknown",
                        "lost_no_second_post",
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
                        "metadata_omission_no_deletion",
                        "metadata_drift_blocks_overwrite",
                        "asset_lost_remote_post_accepted",
                        "asset_lost_restart_outcome_unknown",
                        "asset_lost_no_second_post",
                        "asset_lost_repackage_still_blocked",
                        "control_repository_unchanged",
                        "credentials_absent_from_state_and_report",
                    }
                ),
            ),
            "tests/integration/application_plan_probe.py",
            "application_plans.assertions",
        ),
        (
            "connection_profiles_strict_tls_and_local_authority",
            _all_required_true(
                report,
                "connection_profiles.assertions",
                frozenset(
                    {
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
                    }
                ),
            ),
            "tests/integration/connection_profile_probe.py",
            "connection_profiles.assertions",
        ),
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
            "persistent_non_root_oci_runtime",
            _all_required_true(report, "oci_runtime.assertions", OCI_RUNTIME_ASSERTION_NAMES)
            and _all_required_true(
                report, "oci_runtime.reference_compose", REFERENCE_COMPOSE_ASSERTION_NAMES
            )
            and isinstance(_value(report, "oci_runtime.reference_compose.image_id"), str)
            and (
                re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    _value(report, "oci_runtime.reference_compose.image_id"),
                )
                is not None
                and _value(report, "oci_runtime.reference_compose.image_id")
                == _value(report, "oci_runtime.image_id")
            ),
            "tests/integration/oci_runtime_probe.py",
            "oci_runtime.assertions and oci_runtime.reference_compose",
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
    metadata_complete = run_metadata_is_complete(metadata)
    catalog.append(
        {
            "id": "run_metadata_contract",
            "status": "passed" if metadata_complete else "failed",
            "test": "backend/tests/test_integration_report_contract.py",
            "report_evidence": "execution run, code, platform, image, and hosted-CI identity",
        }
    )
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


def record_workflow_cleanup(results: Path, cleanup_status: int) -> bool:
    report_path = results / "report.json"
    cleanup_record = {
        "status": "passed" if cleanup_status == 0 else "failed",
        "exit_code": cleanup_status,
        "scope": "workflow-final-sweep",
        "only_run_scoped_resources": True,
    }
    (results / "workflow-cleanup.json").write_text(
        json.dumps(cleanup_record, indent=2, sort_keys=True), encoding="utf-8"
    )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        execution = report["execution"]
        required_checks = execution["required_checks"]
        failed_checks = execution["failed_or_not_executed_checks"]
        if not (
            isinstance(report, dict)
            and isinstance(execution, dict)
            and isinstance(required_checks, list)
            and all(isinstance(entry, dict) for entry in required_checks)
            and isinstance(failed_checks, list)
            and all(isinstance(entry, str) for entry in failed_checks)
        ):
            return False
    except (KeyError, OSError, TypeError, json.JSONDecodeError):
        return False
    identifier = "workflow_scoped_cleanup"
    required_checks[:] = [entry for entry in required_checks if entry.get("id") != identifier]
    required_checks.append(
        {
            "id": identifier,
            "status": cleanup_record["status"],
            "test": "tests/integration/clean.sh",
            "report_evidence": "workflow_cleanup",
        }
    )
    execution["workflow_cleanup"] = cleanup_record
    failed_checks[:] = [entry for entry in failed_checks if entry != identifier]
    if cleanup_status != 0:
        failed_checks.append(identifier)
        execution["status"] = "failed"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return cleanup_status == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("init", "collect-runtime", "check", "finalize", "record-workflow-cleanup"),
    )
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
    if args.command == "record-workflow-cleanup":
        if args.cleanup_status is None:
            parser.error("record-workflow-cleanup requires --cleanup-status")
        return 0 if record_workflow_cleanup(args.results, args.cleanup_status) else 1
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
