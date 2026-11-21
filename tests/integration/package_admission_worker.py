"""Exercise real package admission and first import in the target container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import plan_worker
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.connection_probe import LocalReferences, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.package_admission import PackageAdmissionConflict, PackageAdmissions
from gitferry.plan_runner import PlanRunner
from gitferry.runtime import RuntimePaths
from gitferry.state_store import StateStore
from gitferry.transfer_plans import PlanService

ROOT = Path("/state/package-admission")
DATABASE = ROOT / "state" / "state.sqlite3"
URL = "http://target:3000"
HTTPS = "https://localhost:34443"
PACKAGE_REF = "source-export-real"
TARGET = "target"
SOURCE = "source"


def remote(method: str, path: str, *, username: str = "gitferry-admin", **kwargs: Any) -> Any:
    password = os.environ[
        "GITFERRY_IT_ADMIN_PASSWORD"
        if username == "gitferry-admin"
        else "GITFERRY_IT_USER_PASSWORD"
    ]
    response = httpx.request(
        method,
        URL + "/api/v1" + path,
        auth=(username, password),
        timeout=kwargs.pop("timeout", 20),
        trust_env=False,
        **kwargs,
    )
    response.raise_for_status()
    return response.json() if response.content else None


def _mint_user_tokens(secrets: Path) -> None:
    for username in ("gitferry-admin", "issue-author", "comment-author"):
        token = remote(
            "POST",
            f"/users/{username}/tokens",
            username=username,
            json={"name": "package-admission-probe", "scopes": ["all"]},
        )
        path = secrets / username
        path.write_text(token["sha1"], encoding="ascii")
        path.chmod(0o600)


def _references() -> LocalReferences:
    return LocalReferences(ROOT / "secrets", ROOT)


def _unreachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return False
    except OSError:
        return True


def _api_login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "synthetic integration passphrase",
        },
    )
    response.raise_for_status()
    csrf = client.cookies.get("gitferry_csrf")
    if not csrf:
        raise RuntimeError("managed package API login did not issue a CSRF cookie")
    return {"x-gitferry-csrf": csrf}


def _managed_upload(state: StateStore, paths: RuntimePaths) -> dict[str, Any]:
    archive = Path("/package/source-export-real.zip")
    archive_size = archive.stat().st_size
    archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    auth = AuthService(state)
    if not auth.bootstrap_completed():
        auth.bootstrap_admin("synthetic.admin", "synthetic integration passphrase")
    environment = {
        "GITFERRY_RUNTIME_MANAGED": "1",
        "GITFERRY_STATE_DB": str(paths.state_database),
        "GITFERRY_PACKAGE_DIR": str(paths.package_directory),
        "GITFERRY_WORK_DIR": str(paths.work_directory),
        "GITFERRY_SECRET_DIR": str(ROOT / "secrets"),
        "GITFERRY_CA_DIR": str(ROOT),
    }
    with patch.dict(os.environ, environment, clear=False):
        with TestClient(
            create_app(auth_service=auth, session_cookie_secure=False),
            base_url="http://testserver",
        ) as client:
            headers = {
                **_api_login(client),
                "content-type": "application/zip",
                "x-gitferry-archive-size": str(archive_size),
            }
            with archive.open("rb") as stream:
                uploaded = client.put(
                    f"/api/v1/package-uploads/{PACKAGE_REF}",
                    content=stream,
                    headers=headers,
                )
            uploaded.raise_for_status()
            detail = client.get(f"/api/v1/package-uploads/{PACKAGE_REF}")
            detail.raise_for_status()
            resumed = client.post(
                f"/api/v1/package-uploads/{PACKAGE_REF}/resume",
                json={},
                headers=_api_login(client),
            )
            resumed.raise_for_status()
            listed = client.get("/api/v1/package-uploads")
            listed.raise_for_status()
    return {
        "uploaded": uploaded.json(),
        "detail": detail.json(),
        "resumed": resumed.json(),
        "listed": listed.json(),
        "archive_size": archive_size,
        "archive_sha256": archive_digest,
    }


def _initialize() -> tuple[PackageAdmissions, str, dict[str, Any]]:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (DATABASE.parent, ROOT / "packages", ROOT / "work", ROOT / "secrets"):
        directory.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile("/tmp/package-admission-trusted.pem", ROOT / "trusted.pem")
    shutil.copyfile("/tmp/package-admission-trusted.key", ROOT / "trusted.key")
    _mint_user_tokens(ROOT / "secrets")
    state = StateStore(DATABASE)
    DATABASE.chmod(0o600)
    paths = RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work")
    before_upload = _counts(PackageAdmissions(state, paths))
    upload = _managed_upload(state, paths)
    after_upload = _counts(PackageAdmissions(state, paths))
    if before_upload != after_upload:
        raise RuntimeError("package upload changed admission state before explicit setup")
    state.register_instance(
        SOURCE, "Synthetic source", "https://source.invalid", "source-export-install"
    )
    state.register_instance(TARGET, "Synthetic target", HTTPS, "target-export-install")
    profiles = ProfileService(state)
    profiles.create(
        ConnectionProfile(
            TARGET,
            "Synthetic target",
            HTTPS,
            "target-export-install",
            "gitferry-admin",
            "trusted.pem",
        )
    )
    plan_worker.ROOT = ROOT
    plan_worker.URL = URL
    with plan_worker.proxy():
        evidence = probe_connection(profiles, TARGET, expected_revision=1, references=_references())
    approve_connection(
        profiles, TARGET, expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    evidence["upload"] = {
        "status": upload["uploaded"]["status"],
        "package_ref": upload["detail"]["package_ref"],
        "digest": upload["detail"]["package_digest"],
        "archive_sha256": upload["archive_sha256"],
        "resumed_status": upload["resumed"]["status"],
        "listed": len(upload["listed"]),
        "counts_unchanged": before_upload == after_upload,
    }
    return PackageAdmissions(state, paths), PACKAGE_REF, evidence


def _bind_authors(service: PackageAdmissions, package_ref: str) -> dict[str, str]:
    artifact = service.artifact(package_ref)
    for realm in artifact.document["realms"]:
        service.state.register_realm(
            realm["realm_key"], "Locally accepted synthetic realm", "synthetic"
        )
    refs: dict[str, str] = {}
    expected_realm = "gitea-" + hashlib.sha256(b"source:source-export-install").hexdigest()[:32]
    allowed_logins = {"issue-author": "issue-author", "comment-author": "comment-author"}
    for claim in json.loads((artifact.directory / "metadata/manifest.json").read_text())[
        "identity_claims"
    ]:
        login = allowed_logins.get(claim.get("login"))
        if login is None or claim.get("realm_key") != expected_realm or claim.get("ou_path") != "/":
            raise RuntimeError("unexpected source identity claim")
        account = remote("GET", f"/users/{login}")
        service.state.confirm_identity_binding(
            binding_id=login,
            source_realm_key=claim["realm_key"],
            source_login=login,
            source_external_id=claim["external_id"],
            source_ou_path=claim["ou_path"],
            target_instance_key=TARGET,
            target_account_id=account["id"],
            target_login=login,
            mapping_version=1,
        )
        refs[login] = login
    return refs


def _counts(service: PackageAdmissions) -> dict[str, int]:
    with service.state.read_connection() as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in (
                "instances",
                "realms",
                "identity_bindings",
                "package_receipts",
                "object_origins",
                "object_mappings",
                "operations",
            )
        }


def _git_visibility() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Wait for Gitea's post-receive metadata, not merely a successful Git push."""
    deadline = time.monotonic() + 20
    observations: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        try:
            repository = remote("GET", "/repos/source-export-lab/selected", timeout=2)
            observations.append({"repository_empty": repository.get("empty")})
            if repository.get("empty") is False:
                branch = remote("GET", "/repos/source-export-lab/selected/branches/main", timeout=2)
                return repository, branch, observations
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status not in (404, 500, 502, 503):
                raise
            observations.append({"read_status": status})
        time.sleep(0.1)
    raise RuntimeError("Gitea did not expose the imported Git branch within 20 seconds")


def initial() -> dict[str, Any]:
    service, package_ref, evidence = _initialize()
    admitted = service.admit(
        package_ref,
        target_instance_key=TARGET,
        profile_revision=1,
        expected_digest=service.artifact(package_ref).digest_sha256,
    )
    review_without_bindings = service.plan_review(
        package_ref, target_instance_key=TARGET, profile_revision=1, apply_members=True
    )
    blocked_without_bindings = False
    try:
        service.prepare_plan(
            package_ref,
            target_instance_key=TARGET,
            profile_revision=1,
            apply_members=True,
            expected_review_digest=review_without_bindings["review_digest"],
        )
    except PackageAdmissionConflict:
        blocked_without_bindings = True
    refs = _bind_authors(service, package_ref)
    review = service.plan_review(
        package_ref,
        target_instance_key=TARGET,
        profile_revision=1,
        apply_members=True,
        author_credential_refs=refs,
    )
    plan = service.prepare_plan(
        package_ref,
        target_instance_key=TARGET,
        profile_revision=1,
        apply_members=True,
        author_credential_refs=refs,
        expected_review_digest=review["review_digest"],
    )
    plans = PlanService(service.state)
    plans.queue(plan["plan_id"], plan["digest"])
    paths = service.paths
    plan_worker.ROOT = ROOT
    plan_worker.URL = URL
    with plan_worker.proxy():
        result = PlanRunner(
            plans,
            lambda _: GiteaPlanAdapter(service.state, paths, _references()),
        ).run(plan["plan_id"])
    repository, refs_remote, git_visibility = _git_visibility()
    issue = remote("GET", "/repos/source-export-lab/selected/issues/1")
    comments = remote("GET", "/repos/source-export-lab/selected/issues/1/comments")
    bundle = next((ROOT / "packages" / PACKAGE_REF / "bundles").glob("*.bundle"))
    bundle_refs = {
        line.split(maxsplit=1)[1]: line.split(maxsplit=1)[0]
        for line in subprocess.run(
            [shutil.which("git") or "/usr/bin/git", "bundle", "list-heads", str(bundle)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        # Bundle HEAD is a symbolic/default-branch alias, not a transferred ref.
        if line.split(maxsplit=1)[1].startswith("refs/")
    }
    events = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    with service.state.read_connection() as connection:
        source_exports_rows = connection.execute("SELECT count(*) FROM source_exports").fetchone()[
            0
        ]
    return {
        "status": result["status"],
        "package_digest": admitted["digest_sha256"],
        "review_missing_bindings": bool(review_without_bindings["selection"]["missing_bindings"]),
        "blocked_without_bindings": blocked_without_bindings,
        "plan_id": plan["plan_id"],
        "repository": repository,
        "issue_author": issue["user"]["login"],
        "issue_author_id": issue["user"]["id"],
        "comment_author": comments[0]["user"]["login"] if comments else "",
        "comment_author_id": comments[0]["user"]["id"] if comments else None,
        "target_author_ids": {
            login: remote("GET", f"/users/{login}")["id"]
            for login in ("issue-author", "comment-author")
        },
        "comment_parent": comments[0]["body"] if comments else "",
        "comment_issue_url": comments[0].get("issue_url") if comments else "",
        "issue_url": issue.get("url", ""),
        "issue_html_url": issue["html_url"],
        "git_ref": refs_remote["commit"]["id"],
        "bundle_refs": bundle_refs,
        "target_refs": {"refs/heads/main": refs_remote["commit"]["id"]},
        "git_visibility": git_visibility,
        "post_count": len(events),
        "objects": _counts(service),
        "evidence_digest": evidence["evidence_digest"],
        "target_only": _unreachable("source", 3000),
        "external_unreachable": _unreachable("1.1.1.1", 80),
        "source_exports_rows": source_exports_rows,
        "upload": evidence["upload"],
    }


def replay() -> dict[str, Any]:
    state = StateStore(DATABASE)
    paths = RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work")
    service = PackageAdmissions(state, paths)
    before_events = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    before_objects = _counts(service)
    before_upload = _counts(service)
    upload = _managed_upload(state, paths)
    after_upload = _counts(service)
    receipt = service.admit(
        PACKAGE_REF,
        target_instance_key=TARGET,
        profile_revision=1,
        expected_digest=service.artifact(PACKAGE_REF).digest_sha256,
    )
    refs = {"issue-author": "issue-author", "comment-author": "comment-author"}
    review = service.plan_review(
        PACKAGE_REF,
        target_instance_key=TARGET,
        profile_revision=1,
        apply_members=True,
        author_credential_refs=refs,
    )
    plan = service.prepare_plan(
        PACKAGE_REF,
        target_instance_key=TARGET,
        profile_revision=1,
        apply_members=True,
        author_credential_refs=refs,
        expected_review_digest=review["review_digest"],
    )
    plans = PlanService(state)
    if plan["status"] not in ("queued", "preflighting", "running", "confirmed"):
        plans.queue(plan["plan_id"], plan["digest"])
    before_counts = {
        "issues": len(remote("GET", "/repos/source-export-lab/selected/issues")),
        "comments": len(remote("GET", "/repos/source-export-lab/selected/issues/1/comments")),
    }
    result = PlanRunner(
        plans,
        lambda _: GiteaPlanAdapter(state, paths, _references()),
    ).run(plan["plan_id"])
    after_counts = {
        "issues": len(remote("GET", "/repos/source-export-lab/selected/issues")),
        "comments": len(remote("GET", "/repos/source-export-lab/selected/issues/1/comments")),
    }
    after_events = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    return {
        "replay": receipt["replay"] and result["status"] == "confirmed",
        "plan_id": plan["plan_id"],
        "plan_reviewed": bool(review["selection"]),
        "plan_prepared": bool(plan["plan_id"]),
        "same_remote_objects": before_counts == after_counts,
        "no_extra_posts": len(before_events) == len(after_events),
        "no_extra_objects": before_objects == _counts(service),
        "package_digest": service.artifact(PACKAGE_REF).digest_sha256,
        "upload_package_ref": upload["detail"]["package_ref"],
        "upload_digest": upload["detail"]["package_digest"],
        "upload_archive_sha256": upload["archive_sha256"],
        "upload_resumed_status": upload["resumed"]["status"],
        "upload_counts_unchanged": before_upload == after_upload,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("initial", "replay"))
    args = parser.parse_args()
    print(json.dumps(initial() if args.mode == "initial" else replay(), sort_keys=True))


if __name__ == "__main__":
    main()
