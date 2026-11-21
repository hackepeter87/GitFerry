"""Bounded real source-export capture and offline replay evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from connection_profile_probe import certificate
from gitferry.application_package import inspect_package
from gitferry.package_archive import receive_package_archive
from support import (
    ADMIN,
    PACKAGE,
    SOURCE_URL,
    WORK,
    ProbeFailure,
    api,
    docker_copy,
    docker_service_exec,
)

REPOSITORY = "access-lab/selected-private"
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
    }
)

TARGET_REPLAY_PROGRAM = """from pathlib import Path
import hashlib
import json
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
        "gitferry-a", ["python", "/probe/source_export_worker.py", mode], timeout=180
    )
    if result.returncode:
        raise ProbeFailure(f"source export worker {mode} failed with exit {result.returncode}")
    return json.loads(result.stdout)


def run_source_export_probe() -> dict[str, Any]:
    worker_path = Path(__file__).with_name("source_export_worker.py")
    docker_copy(worker_path, "gitferry-a:/probe/source_export_worker.py")
    docker_copy(worker_path.with_name("plan_worker.py"), "gitferry-a:/probe/plan_worker.py")
    tls_directory = WORK / "source-export-tls"
    tls_directory.mkdir()
    cert, key = certificate(tls_directory, "trusted")
    docker_copy(cert, "gitferry-a:/tmp/source-export-trusted.pem")
    docker_copy(key, "gitferry-a:/tmp/source-export-trusted.key")
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
    captured = _worker("capture")
    archive_host = PACKAGE / "source-export-real.zip"
    docker_copy("gitferry-a:/state/source-export/source-export.zip", archive_host)
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
        "offline_replay_exact": replay
        == {"status": "captured", "package_digest": captured["package_digest"], "unchanged": True},
        "target_only_archive_verified": received.document["format"]
        == "gitferry.application-package"
        and target_probe.returncode == 0,
        "source_and_target_databases_independent": (
            captured["source_exports_rows"] == 1
            and target_details["source_exports_rows"] == 0
            and target_details["source_state_absent"]
        ),
    }
    if set(assertions) != ASSERTIONS or not all(assertions.values()):
        raise ProbeFailure(f"Source export assertions failed: {assertions}")
    return {
        "status": "passed",
        "assertions": assertions,
        "repository": REPOSITORY,
        "archive": {"sha256": archive_digest, "bytes": archive_host.stat().st_size},
        "limits": [
            "One real private repository on pinned Gitea 1.27.2",
            "Capture is read-only after setup; Git upload-pack POSTs are transport reads",
            "Target verification stops at archive receipt and does not claim full apply",
        ],
    }
