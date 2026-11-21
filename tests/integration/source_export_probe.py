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
            _run_browser(archive_host, "phase2", base_url)
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
    )
    (WORK / "package-admission-evidence.json").write_text(
        json.dumps(
            {
                "initial": {key: target_package[key] for key in evidence_keys},
                "replay": target_replay,
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
        "native_authors_parent_and_git_refs": (
            target_package["issue_author"] == "issue-author"
            and target_package["issue_author_id"]
            == target_package["target_author_ids"]["issue-author"]
            and target_package["comment_author"] == "comment-author"
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
        browser["status"] == "passed" and browser["confirmed"]
    )
    assertions["guided_browser_source_export"] = (
        source_browser["status"] == "passed"
        and source_browser["source_capture"]
        and source_browser["downloaded"]
        and source_browser["selected_repository"] == REPOSITORY
        and archive_digest == captured["archive_sha256"]
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
    if set(assertions) != ASSERTIONS or not all(assertions.values()):
        raise ProbeFailure(f"Source export assertions failed: {assertions}")
    return {
        "status": "passed",
        "assertions": assertions,
        "browser_runtime": runtimes,
        "image_id": built_image,
        "browser_image_ids": image_ids,
        "repository": REPOSITORY,
        "archive": {"sha256": archive_digest, "bytes": archive_host.stat().st_size},
        "limits": [
            "Two selected private repositories and an excluded source control; Gitea 1.27.2",
            "Capture is read-only after setup; Git upload-pack POSTs are transport reads",
            "Target import uses one fresh package and synthetic local identity bindings",
        ],
    }
