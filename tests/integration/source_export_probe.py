"""Bounded real source-export capture and offline replay evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from browser_tunnel import browser_tunnel
from connection_profile_probe import certificate
from gitea_compat import PRIMARY_GITEA_VERSION
from gitferry.application_package import inspect_package
from gitferry.package_archive import receive_package_archive
from support import (
    ADMIN,
    COMPOSE,
    PACKAGE,
    SOURCE_URL,
    WORK,
    ProbeFailure,
    api,
    docker_copy,
    docker_service_exec,
    flush_fixture_queues,
    run,
)

REPOSITORY = "source-export-lab/selected"


ASSERTIONS = frozenset(
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
        "structure_phase_confirmed_without_bindings_or_memberships",
        "late_full_plan_reused_structure_operations",
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
        "directory_ldap_reader_clone_allowed",
        "directory_ldap_reader_push_denied",
        "directory_ldap_writer_clone_allowed",
        "directory_ldap_writer_push_allowed",
        "directory_ldap_outsider_denied",
        "directory_functional_users_non_admin",
        "directory_owners_isolated",
        "directory_intended_team_members",
        "directory_selected_repository_relations",
        "directory_reader_unit_permissions",
        "directory_writer_unit_permissions",
        "structure_account_counts_unchanged",
        "git_return_file_only",
        "git_return_ref_object_exact",
        "git_return_selected_drift_blocks_before_push",
        "git_return_protected_ref_blocks_before_push",
        "git_return_lost_response_requires_exact_recovery",
        "git_return_replay_restart_no_duplicate",
        "git_return_control_and_network_isolation",
    }
)

TARGET_REPLAY_PROGRAM = """from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from gitferry.package_archive import receive_package_archive
from gitferry.state_store import StateStore
try:
    urllib.request.urlopen('http://source:3000/api/v1/version', timeout=2)
except urllib.error.URLError:
    pass
else:
    raise RuntimeError('source reachable from target context')
receive_package_archive(
    Path('/package/source-export-real.zip'), Path('/state/source-export-target')
)
db = Path('/state/source-export-target.sqlite3')
state = StateStore(db)
with state.read_connection() as connection:
    source_exports_rows = connection.execute('SELECT count(*) FROM source_exports').fetchone()[0]
print(json.dumps({
    'target_db_sha256': hashlib.sha256(db.read_bytes()).hexdigest(),
    'source_exports_rows': source_exports_rows,
    'source_state_absent': not Path('/state/source-export').exists(),
}))
"""
compile(TARGET_REPLAY_PROGRAM, "<target-replay>", "exec")


def _worker(mode: str) -> dict[str, Any]:
    result = docker_service_exec(
        "gitferry-a", ["python", "/probe/source_export_worker.py", mode], timeout=240, check=False
    )
    if result.returncode:
        diagnostic = docker_service_exec(
            "gitferry-a", ["cat", "/state/source-export/browser-server.log"], check=False
        )
        if diagnostic.returncode == 0:
            (WORK / "source-browser-server.log").write_text(diagnostic.stdout)
        evidence = {"mode": mode, "exit_code": result.returncode}
        for line in result.stderr.splitlines():
            if line.startswith("GITFERRY_PROBE_ERROR "):
                evidence.update(json.loads(line.removeprefix("GITFERRY_PROBE_ERROR ")))
        (WORK / "source-export-worker-failure.json").write_text(json.dumps(evidence, indent=2))
        raise ProbeFailure(
            f"source export worker {mode} failed; see source-export-worker-failure.json"
        )
    return json.loads(result.stdout)


def _target_worker(mode: str, context: str = "normal") -> dict[str, Any]:
    result = docker_service_exec(
        "gitferry-b",
        ["python", "/probe/package_admission_worker.py", mode, "--context", context],
        timeout=240,
        check=False,
    )
    if result.returncode:
        evidence = {"mode": mode, "context": context, "exit_code": result.returncode}
        for line in result.stderr.splitlines():
            if line.startswith("GITFERRY_PROBE_ERROR "):
                evidence.update(json.loads(line.removeprefix("GITFERRY_PROBE_ERROR ")))
        (WORK / "target-browser-worker-failure.json").write_text(json.dumps(evidence, indent=2))
        raise ProbeFailure(f"package admission worker {mode} failed with exit {result.returncode}")
    return json.loads(result.stdout)


def _git_return_worker(service: str, mode: str, *, allow_failure: bool = False) -> dict[str, Any]:
    result = docker_service_exec(
        service, ["python", "/probe/git_return_worker.py", mode], timeout=240, check=False
    )
    if result.returncode and allow_failure:
        return {
            "status": "expected_crash",
            "returncode": result.returncode,
            "stderr": result.stderr[-2000:],
        }
    if result.returncode:
        evidence = {"service": service, "mode": mode, "exit_code": result.returncode}
        for line in result.stderr.splitlines():
            if line.startswith("GITFERRY_PROBE_ERROR "):
                evidence.update(json.loads(line.removeprefix("GITFERRY_PROBE_ERROR ")))
        (WORK / "git-return-worker-failure.json").write_text(json.dumps(evidence, indent=2))
        raise ProbeFailure(f"Git return worker {service}/{mode} failed")
    return json.loads(result.stdout)


def _run_browser(
    archive: Path, phase: str, base_url: str, *, evidence_name: str | None = None
) -> dict[str, Any]:
    evidence_name = evidence_name or phase
    evidence = WORK / f"browser-{evidence_name}.json"
    environment = {
        **os.environ,
        "GITFERRY_INTEGRATION_ARCHIVE": str(archive),
        "GITFERRY_INTEGRATION_EVIDENCE": str(evidence),
        "GITFERRY_INTEGRATION_APP_URL": base_url,
        "GITFERRY_INTEGRATION_BROWSER_OUTPUT": str(WORK / ("browser-" + evidence_name)),
        "GITFERRY_INTEGRATION_SOURCE_DOWNLOAD": str(archive),
    }
    with (WORK / f"browser-{evidence_name}.log").open("wb") as log:
        result = subprocess.run(  # noqa: S603
            [
                shutil.which("npx") or "/usr/bin/npx",
                "playwright",
                "test",
                "--config=playwright.integration.config.ts",
                "--grep",
                phase,
            ],
            cwd=Path(__file__).parents[2] / "frontend",
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if result.returncode or not evidence.is_file():
        raise ProbeFailure("real first-import browser phase failed: " + phase)
    value = json.loads(evidence.read_text())
    if value.get("status") != "passed":
        raise ProbeFailure("missing first-import browser evidence: " + phase)
    return value


def run_source_export_probe() -> dict[str, Any]:
    worker_path = Path(__file__).with_name("source_export_worker.py")
    docker_copy(worker_path, "gitferry-a:/probe/source_export_worker.py")
    for service in ("gitferry-a", "gitferry-b"):
        docker_copy(
            worker_path.with_name("git_return_worker.py"),
            f"{service}:/probe/git_return_worker.py",
        )
        docker_copy(
            worker_path.with_name("native_browser_fixture.py"),
            f"{service}:/probe/native_browser_fixture.py",
        )
        docker_copy(
            worker_path.with_name("browser_runtime.py"), f"{service}:/probe/browser_runtime.py"
        )
    docker_copy(worker_path.with_name("plan_worker.py"), "gitferry-a:/probe/plan_worker.py")
    docker_copy(
        worker_path.with_name("package_admission_worker.py"),
        "gitferry-b:/probe/package_admission_worker.py",
    )
    tls_directory = WORK / "source-export-tls"
    tls_directory.mkdir()
    cert, key = certificate(tls_directory, "trusted")
    docker_copy(cert, "gitferry-a:/tmp/source-export-trusted.pem")
    docker_copy(key, "gitferry-a:/tmp/source-export-trusted.key")
    docker_copy(cert, "gitferry-b:/tmp/package-admission-trusted.pem")
    docker_copy(key, "gitferry-b:/tmp/package-admission-trusted.key")
    token = api(
        SOURCE_URL,
        "POST",
        "/users/gitferry-admin/tokens",
        auth=ADMIN,
        json_body={"name": "source-export-probe", "scopes": ["all"]},
    )["sha1"]
    token_path = WORK / ".source-export-token"
    token_path.write_text(token, encoding="ascii")
    token_path.chmod(0o600)
    try:
        docker_copy(token_path, "gitferry-a:/tmp/source-export-admin-token")
    finally:
        token_path.unlink()
    _worker("prepare-browser")
    archive_host = PACKAGE / "source-export-real.zip"
    _worker("start-browser")
    try:
        with browser_tunnel(COMPOSE, service="gitferry-a") as base_url:
            source_browser = _run_browser(archive_host, "phase0", base_url)
    finally:
        _worker("stop-browser")
    if not archive_host.is_file() or archive_host.stat().st_size == 0:
        raise ProbeFailure("source browser did not download an archive")
    docker_copy(archive_host, "gitferry-a:/state/source-export/source-export-browser.zip")
    docker_copy(
        WORK / "browser-phase0.json", "gitferry-a:/state/source-export/browser-evidence.json"
    )
    captured = _worker("capture")
    replay = _worker("replay")
    archive_digest = hashlib.sha256(archive_host.read_bytes()).hexdigest()
    extracted = WORK / "source-export-target-receive"
    if extracted.exists():
        raise ProbeFailure("target receive destination unexpectedly exists")
    received = receive_package_archive(archive_host, extracted)
    package = inspect_package(received.directory)
    manifest = received.directory / "metadata" / "manifest.json"
    target_probe = docker_service_exec(
        "gitferry-b",
        [
            "python",
            "-c",
            TARGET_REPLAY_PROGRAM,
        ],
        check=False,
    )
    api_writes = captured["source_api_writes"]
    if target_probe.returncode:
        raise ProbeFailure("target-only archive verification failed")
    target_details = json.loads(target_probe.stdout)
    setup = _target_worker("prepare-browser")
    if (
        any(setup["counts"].values())
        or not setup["target_only"]
        or not setup["external_unreachable"]
    ):
        raise ProbeFailure(
            "browser setup contains unauthorized application facts or network access"
        )
    _target_worker("start-browser")
    try:
        with browser_tunnel(COMPOSE) as base_url:
            uploaded = _run_browser(archive_host, "phase1", base_url)
            _target_worker("browser-upload")
            _run_browser(archive_host, "structure", base_url)
            structure = _target_worker("browser-structure-result")
            _target_worker("browser-preview-snapshot")
            review_browser = _run_browser(archive_host, "phase2", base_url)
            preview = _target_worker("browser-preview")
            browser = _run_browser(archive_host, "phase5", base_url)
        docker_copy(
            WORK / "browser-phase2.json",
            "gitferry-b:/state/package-admission/browser-evidence.json",
        )
        target_package = _target_worker("browser-result")
    finally:
        try:
            docker_copy(
                "gitferry-b:/state/package-admission/browser-server.log",
                WORK / "browser-server.log",
            )
        finally:
            _target_worker("stop-browser")
    _target_worker("start-browser")
    try:
        with browser_tunnel(COMPOSE) as base_url:
            restart = _run_browser(archive_host, "phase3", base_url)
    finally:
        try:
            docker_copy(
                "gitferry-b:/state/package-admission/browser-server.log",
                WORK / "browser-server.log",
            )
        finally:
            _target_worker("stop-browser")
    target_replay = _target_worker("replay")
    return_export = _git_return_worker("gitferry-b", "export-b")
    return_archive = PACKAGE / "git-return-real.zip"
    docker_copy("gitferry-b:/state/package-admission/git-return-real.zip", return_archive)
    docker_copy(return_archive, "gitferry-a:/state/source-export/git-return-real.zip")
    return_drift = _git_return_worker("gitferry-a", "drift-a")
    return_protected = _git_return_worker("gitferry-a", "protected-a")
    return_crash = _git_return_worker("gitferry-a", "crash-a", allow_failure=True)
    return_recovery = _git_return_worker("gitferry-a", "recover-a")
    return_replay = _git_return_worker("gitferry-a", "replay-a")
    (WORK / "git-return-evidence.json").write_text(
        json.dumps(
            {
                "export": return_export,
                "drift": return_drift,
                "protected": return_protected,
                "crash": return_crash,
                "recovery": return_recovery,
                "replay": return_replay,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    runtimes = {}
    image_ids = {}
    for service, context in (("gitferry-a", "source-export"), ("gitferry-b", "package-admission")):
        runtimes[service] = json.loads(
            docker_service_exec(service, ["cat", f"/state/{context}/browser-runtime.json"]).stdout
        )
        container = run([*COMPOSE, "ps", "-q", service]).stdout.strip()
        image_ids[service] = run(
            ["docker", "inspect", "--format", "{{.Image}}", container]
        ).stdout.strip()
    built_image = run(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            os.environ["GITFERRY_IT_RUNTIME_IMAGE"],
        ]
    ).stdout.strip()
    flush_fixture_queues("target", "collision")
    collision_before = _target_worker("collision-snapshot")
    _target_worker("prepare-browser", "collision")
    _target_worker("start-browser", "collision")
    try:
        with browser_tunnel(COMPOSE) as base_url:
            _run_browser(archive_host, "phase1", base_url, evidence_name="collision-upload")
            collision_browser = _run_browser(archive_host, "phase4", base_url)
        collision = _target_worker("collision-result", "collision")
        (WORK / "collision-evidence.json").write_text(
            json.dumps({"before": collision_before, "after": collision}, indent=2)
        )
    finally:
        _target_worker("stop-browser", "collision")
    # Keep bounded, credential-free comparison facts before the runner cleans up
    # the isolated instances, including when a later assertion fails.
    evidence_keys = (
        "status",
        "plan_id",
        "issue_author",
        "issue_author_id",
        "comment_author",
        "comment_author_id",
        "target_author_ids",
        "comment_issue_url",
        "issue_url",
        "issue_html_url",
        "bundle_refs",
        "target_refs",
        "git_visibility",
        "objects",
        "post_count",
        "structure_plan_id",
        "structure_operation_ids_reused",
    )
    (WORK / "package-admission-evidence.json").write_text(
        json.dumps(
            {
                "initial": {key: target_package[key] for key in evidence_keys},
                "replay": target_replay,
                "effective_rights": target_replay["effective_rights"],
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    claims = package.document.get("metadata", {}).get("issue_authors", [])
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assertions = {
        "source_capture_confirmed": captured["job"]["status"] == "captured",
        "actual_git_bundle_captured": any(
            member.startswith("bundles/") for member in package.files
        ),
        "organization_and_team_captured": bool(package.document["repositories"][0]["organization"])
        and bool(package.document["repositories"][0].get("teams")),
        "issue_comment_distinct_author_claims": len(claims) >= 1
        and claims[0]["author"]["login"] == "issue-author"
        and any(
            obj["origin"]["object_type"] == "issue_comment"
            and obj["author"]["login"] == "comment-author"
            for obj in json.loads(manifest.read_text())["objects"]
        ),
        "manifest_v2_unchanged": captured["manifest_sha256"] == manifest_digest,
        "byte_archive_exact": archive_digest == captured["archive_sha256"]
        and package.digest_sha256 == captured["package_digest"],
        "source_context_cannot_target_or_external": captured["source_target_unreachable"]
        and captured["source_external_unreachable"],
        "capture_has_no_api_writes": not api_writes and bool(captured["git_upload_pack_posts"]),
        "offline_replay_exact": replay["status"] == "captured"
        and replay["package_digest"] == captured["package_digest"]
        and replay["unchanged"],
        "api_queue_background_download": (
            captured["api_queue_status"] in {"queued", "running", "ready"}
            and captured["api_queue_export_id"] == captured["job"]["export_id"]
            and captured["api_delivery"]["status"] == "ready"
            and captured["api_delivery"]["capture_status"] == "captured"
            and captured["api_download"]["archive_sha256"] == captured["archive_sha256"]
            and captured["api_download"]["header_sha256"] == captured["archive_sha256"]
            and captured["api_download"]["archive_bytes"] == captured["archive_bytes"]
            and captured["api_download"]["header_bytes"] == captured["archive_bytes"]
        ),
        "api_restart_replay_no_source_access": (
            replay["api_status"] == "ready"
            and replay["archive_sha256"] == captured["archive_sha256"]
            and replay["no_source_access"]
        ),
        "target_only_archive_verified": received.document["format"]
        == "gitferry.application-package"
        and target_probe.returncode == 0,
        "source_and_target_databases_independent": (
            captured["source_exports_rows"] == 1
            and target_details["source_exports_rows"] == 0
            and target_details["source_state_absent"]
        ),
        "package_admission_apply_confirmed": target_package["status"] == "confirmed",
        "structure_phase_confirmed_without_bindings_or_memberships": (
            structure["status"] == "confirmed"
            and structure["no_metadata_or_memberships"]
            and structure["no_account_operations"]
            and structure["no_identity_bindings"]
            and structure["no_target_memberships"]
            and structure["account_counts_unchanged"]
        ),
        "late_full_plan_reused_structure_operations": (
            target_package["structure_plan_id"] == structure["plan_id"]
            and target_package["structure_operation_ids_reused"] > 0
        ),
        "native_authors_parent_and_git_refs": (
            target_package["issue_author"] == "ldap-reader"
            and target_package["issue_author_id"]
            == target_package["target_author_ids"]["issue-author"]
            and target_package["comment_author"] == "ldap-writer"
            and target_package["comment_author_id"]
            == target_package["target_author_ids"]["comment-author"]
            # Gitea's comment.issue_url is the parent's web URL; issue.url is
            # its distinct API URL. Compare the returned web relationship.
            and target_package["comment_issue_url"] == target_package["issue_html_url"]
            and target_package["target_refs"] == target_package["bundle_refs"]
        ),
        "target_only_archive_and_external_unreachable": (
            target_package["target_only"] and target_package["external_unreachable"]
        ),
        "package_admission_database_independent": target_package["source_exports_rows"] == 0,
        "no_user_preparation_blocks_metadata": (
            target_package["review_missing_bindings"] and target_package["blocked_without_bindings"]
        ),
        "package_replay_process_restart_no_extra_posts_or_objects": (
            target_replay["replay"]
            and target_replay["plan_id"] == target_package["plan_id"]
            and target_replay["plan_reviewed"]
            and target_replay["plan_prepared"]
            and target_replay["same_remote_objects"]
            and target_replay["no_extra_posts"]
            and target_replay["no_extra_objects"]
        ),
        "target_upload_files_only_before_local_setup": (
            target_package["upload"]["status"] == "ready"
            and bool(
                re.fullmatch(
                    r"upload-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    target_package["upload"]["package_ref"],
                )
            )
            and target_package["upload"]["package_ref"] == uploaded["upload_id"]
            and target_package["upload"]["resumed_status"] == "ready"
            and target_package["upload"]["counts_unchanged"]
        ),
        "target_upload_replay_persistent_identity": (
            target_replay["upload_package_ref"] == target_package["upload"]["package_ref"]
            and target_replay["upload_digest"] == target_package["upload"]["digest"]
            and target_replay["upload_archive_sha256"] == captured["archive_sha256"]
            and target_replay["upload_resumed_status"] == "ready"
            and target_replay["upload_counts_unchanged"]
        ),
    }
    assertions["guided_browser_first_import"] = (
        browser["status"] == "passed"
        and browser["confirmed"]
        and browser["keyboard_interactions"] > 0
        and source_browser["keyboard_interactions"] > 0
        and review_browser["keyboard_interactions"] > 0
        and review_browser["storage_estimate_reviewed"]
    )
    assertions["guided_browser_source_export"] = (
        source_browser["status"] == "passed"
        and source_browser["source_capture"]
        and source_browser["downloaded"]
        and source_browser["selected_repository"] == REPOSITORY
        and archive_digest == captured["archive_sha256"]
    )
    assertions["guided_browser_inventory_bound_export"] = (
        source_browser.get("inventory_reviewed") is True
        and source_browser.get("inventory_request_version") == 3
        and captured["job"]["request"].get("version") == 3
    )
    assertions["guided_browser_target_collision"] = (
        collision_browser["collision_blocked"]
        and collision["blocked"]
        and collision["no_remote_writes"]
        and collision["snapshot"] == collision_before
    )
    assertions["guided_browser_preview_no_remote_write"] = (
        preview["passed"] and preview["no_remote_writes"]
    )
    assertions["guided_selected_set_and_control"] = (
        set(target_package["selected_repositories"]) == {REPOSITORY, REPOSITORY + "-extra"}
        and target_package["unselected_absent"]
        and {item["repository"] for item in package.document["repositories"]}
        == set(target_package["selected_repositories"])
    )
    assertions["guided_browser_production_runtime"] = (
        source_browser["secure_session"]
        and browser["secure_session"]
        and restart["secure_session"]
        and set(image_ids.values()) == {built_image}
        and all(
            runtime["uid"] == 10001
            and runtime["gid"] == 10001
            and runtime["installed_application"]
            and runtime["secure_cookie_policy"]
            and runtime["production_entrypoint"] == "gitferry.server"
            for runtime in runtimes.values()
        )
    )
    assertions["guided_browser_restart_read_only"] = (
        restart["status"] == "passed" and restart["read_only"]
    )
    rights = target_replay["effective_rights"]
    assertions["directory_ldap_reader_clone_allowed"] = rights["reader_clone_allowed"]
    assertions["directory_ldap_reader_push_denied"] = rights["reader_push_denied"]
    assertions["directory_ldap_writer_clone_allowed"] = rights["writer_clone_allowed"]
    assertions["directory_ldap_writer_push_allowed"] = rights["writer_push_allowed"]
    assertions["directory_ldap_outsider_denied"] = rights["outsider_clone_denied"]
    authority = target_replay["authority_snapshot"]
    assertions["directory_functional_users_non_admin"] = authority["all_functional_users_non_admin"]
    assertions["directory_owners_isolated"] = authority["owners_members_only_admin"]
    assertions["directory_intended_team_members"] = authority["intended_team_members"]
    assertions["directory_selected_repository_relations"] = authority[
        "selected_repository_relations"
    ]
    assertions["directory_reader_unit_permissions"] = authority["reader_unit_permissions"]
    assertions["directory_writer_unit_permissions"] = authority["writer_unit_permissions"]
    assertions["structure_account_counts_unchanged"] = (
        structure["account_count_before"] == structure["account_count_after"]
    )
    assertions["git_return_file_only"] = (
        return_export["status"] == "ready"
        and return_archive.is_file()
        and return_recovery["status"] == "confirmed"
        and return_recovery["return_digest"] == return_export["package_digest"]
    )
    assertions["git_return_ref_object_exact"] = (
        return_recovery["remote_oid"] == return_export["offline_oid"]
    )
    assertions["git_return_selected_drift_blocks_before_push"] = (
        return_drift["status"] == "blocked"
        and return_drift["no_push"]
        and return_drift["cleanup_restored"]
    )
    assertions["git_return_protected_ref_blocks_before_push"] = (
        return_protected["status"] == "blocked" and return_protected["no_push"]
    )
    assertions["git_return_lost_response_requires_exact_recovery"] = (
        return_crash["status"] == "expected_crash"
        and return_crash["returncode"] == 42
        and return_recovery["restart_status"] == "outcome_unknown"
        and return_recovery["status"] == "confirmed"
        and return_recovery["wrong_target_rejected"]
    )
    assertions["git_return_replay_restart_no_duplicate"] = (
        return_replay["status"] == "confirmed"
        and return_replay["same_refs"]
        and return_replay["push_attempts"] > 0
        and return_recovery["push_attempts"] == return_replay["push_attempts"]
    )
    assertions["git_return_control_and_network_isolation"] = (
        return_export["source_unreachable"]
        and return_drift["target_unreachable"]
        and return_drift["control_unchanged"]
        and return_protected["control_unchanged"]
    )
    from directory_identity_recovery_probe import run as run_identity_recovery

    identity_recovery = run_identity_recovery()
    assertions["directory_claim_drift_restart_blocked"] = (
        identity_recovery["status"] == "passed"
        and identity_recovery["binding_unchanged"]
        and identity_recovery["restored_facts_do_not_reopen"]
    )
    first_identity = identity_recovery["initial"]["directory"]
    second_identity = identity_recovery["writer_initial"]["directory"]
    assertions["directory_equal_login_realms_explicit_bindings"] = (
        first_identity["claim"]["login"] == second_identity["claim"]["login"] == "ldap-reader"
        and all(
            first_identity["claim"][key] != second_identity["claim"][key]
            for key in ("realm_key", "external_id", "dn", "ou_path")
        )
        and first_identity["selection"]["native_source_id"]
        != second_identity["selection"]["native_source_id"]
        and identity_recovery["initial"]["binding_before"]["target_account_id"]
        != identity_recovery["writer_initial"]["binding_before"]["target_account_id"]
    )
    assertions["directory_outage_blocks_without_rebinding"] = (
        identity_recovery["outage"]["status"] == "blocked"
        and identity_recovery["outage"]["after_status"] == "approved"
        and identity_recovery["outage"]["binding_unchanged"]
        and identity_recovery["outage_recovered"]["status"] == "passed"
    )
    assertions["directory_group_drift_requires_review"] = (
        identity_recovery["group_drift"]["changed_groups"]
        and identity_recovery["group_restored"]["after_status"] == "conflict"
        and identity_recovery["group_restored"]["binding_unchanged"]
    )
    flush_fixture_queues("target", "identity-recovery")
    recovery_snapshot = _target_worker("collision-snapshot")
    recovery_events = docker_service_exec(
        "gitferry-b", ["wc", "-l", "/state/package-admission/remote-events.jsonl"]
    ).stdout
    _target_worker("start-browser")
    try:
        with browser_tunnel(COMPOSE) as base_url:
            reviewed = _run_browser(archive_host, "phase6", base_url)
    finally:
        try:
            docker_copy(
                "gitferry-b:/state/package-admission/browser-server.log",
                WORK / "browser-recovery-server.log",
            )
        finally:
            _target_worker("stop-browser")
    from directory_identity_recovery_probe import _worker as identity_worker

    reviewed_bindings = [
        identity_worker("verify", login) for login in ("ldap-reader", "ldap-writer")
    ]
    assertions["directory_recovery_no_transfer_writes"] = (
        recovery_snapshot == _target_worker("collision-snapshot")
        and recovery_events
        == docker_service_exec(
            "gitferry-b", ["wc", "-l", "/state/package-admission/remote-events.jsonl"]
        ).stdout
    )
    identity_recovery["desktop_review"] = reviewed
    identity_recovery["reviewed_bindings"] = reviewed_bindings
    assertions["directory_conflict_desktop_recovery"] = (
        reviewed["explicit_review"] == 2
        and reviewed["persisted_approved"] == 2
        and all(
            value["status"] == "passed" and value["binding_unchanged"]
            for value in reviewed_bindings
        )
    )
    from ad_identity_probe import run as run_ad_identity

    ad_identity = run_ad_identity()
    assertions["directory_ad_identity_drift_restart_blocked"] = ad_identity[
        "status"
    ] == "passed" and all(ad_identity["assertions"].values())
    if set(assertions) != ASSERTIONS or not all(assertions.values()):
        raise ProbeFailure(f"Source export assertions failed: {assertions}")
    return {
        "status": "passed",
        "native_payloads": target_package["native_payloads"],
        "source_browser": source_browser,
        "identity_recovery": identity_recovery,
        "ad_identity_recovery": ad_identity,
        "assertions": assertions,
        "browser_runtime": runtimes,
        "image_id": built_image,
        "browser_image_ids": image_ids,
        "repository": REPOSITORY,
        "archive": {"sha256": archive_digest, "bytes": archive_host.stat().st_size},
        "limits": [
            "Two selected private repositories and an excluded source control; "
            f"Gitea {PRIMARY_GITEA_VERSION}",
            "Capture is read-only after setup; Git upload-pack POSTs are transport reads",
            "Target import uses one fresh package and synthetic local identity bindings",
        ],
    }
