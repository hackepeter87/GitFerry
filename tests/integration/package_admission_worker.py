"""Exercise real package admission and first import in the target container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import plan_worker
from browser_runtime import process_exited, serve
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
from native_browser_fixture import verify as verify_native_browser

ROOT = Path("/state/package-admission")
DATABASE = ROOT / "state" / "state.sqlite3"
URL = "http://target:3000"
HTTPS = "https://localhost:34443"
PACKAGE_REF = "source-export-real"
TARGET = "target"
SOURCE = "source"
CONTEXT = "normal"
DIRECTORY_USERS = {
    "ldap-reader": "synthetic-directory-pass",
    "ldap-writer": "synthetic-directory-writer-pass",
    "ldap-outsider": "synthetic-directory-outsider-pass",
}


def remote(method: str, path: str, *, username: str = "gitferry-admin", **kwargs: Any) -> Any:
    if username in DIRECTORY_USERS:
        password = DIRECTORY_USERS[username]
    else:
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
    usernames = (
        ("gitferry-admin", "issue-author", "comment-author")
        if CONTEXT == "collision"
        else ("gitferry-admin", "ldap-reader", "ldap-writer")
    )
    for username in usernames:
        if CONTEXT == "collision":
            # Operator credentials are local to B; copy no application facts or database.
            existing = Path("/state/package-admission/secrets") / username
            if existing.is_file():
                shutil.copyfile(existing, secrets / username)
                (secrets / username).chmod(0o600)
                continue
        scopes = {
            "gitferry-admin": ["all"],
            "ldap-reader": ["read:user", "read:repository", "write:issue"],
            "ldap-writer": ["read:user", "write:repository", "write:issue"],
        }.get(username, ["all"])
        token = remote(
            "POST",
            f"/users/{username}/tokens",
            username=username,
            json={"name": "package-admission-probe", "scopes": scopes},
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
    # Test-side HTTP client explicitly carries its own session over the isolated
    # loopback tunnel. The actual browser separately exercises secure cookies.
    return {
        "x-gitferry-csrf": csrf,
        "cookie": "; ".join(f"{key}={value}" for key, value in client.cookies.items()),
    }


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
    directory_ca = Path("/run/directory-ca/ca.pem")
    if directory_ca.is_file():
        shutil.copyfile(directory_ca, ROOT / "directory.pem")
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
    with plan_worker.proxy(preserve_origin=True):
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
    allowed_logins = {"issue-author": "ldap-reader", "comment-author": "ldap-writer"}
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
            source_login=claim["login"],
            source_external_id=claim["external_id"],
            source_ou_path=claim["ou_path"],
            target_instance_key=TARGET,
            target_account_id=account["id"],
            target_login=login,
            mapping_version=1,
        )
        refs[claim["login"]] = login
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


def _git_visibility(
    scope: str = "source-export-lab/selected",
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Wait for Gitea's post-receive metadata, not merely a successful Git push."""
    deadline = time.monotonic() + 20
    observations: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        try:
            repository = remote("GET", f"/repos/{scope}", timeout=2)
            observations.append({"repository_empty": repository.get("empty")})
            if repository.get("empty") is False:
                branch = remote("GET", f"/repos/{scope}/branches/main", timeout=2)
                return repository, branch, observations
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status not in (404, 500, 502, 503):
                raise
            observations.append({"read_status": status})
        time.sleep(0.1)
    raise RuntimeError("Gitea did not expose the imported Git branch within 20 seconds")


def _directory_effective_rights() -> dict[str, bool]:
    """Exercise the imported repository with LDAP identities, without token/admin auth."""
    askpass = ROOT / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        'case "$1" in *Username*) printf \'%s\\n\' "$GITFERRY_GIT_USERNAME";; '
        "*) printf '%s\\n' \"$GITFERRY_GIT_PASSWORD\";; esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    repository_url = "http://target:3000/source-export-lab/selected.git"

    def run_as(
        username: str, password: str, arguments: list[str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
            "GITFERRY_GIT_USERNAME": username,
            "GITFERRY_GIT_PASSWORD": password,
        }
        return subprocess.run(  # noqa: S603
            [shutil.which("git") or "/usr/bin/git", *arguments],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def commit_fixture(directory: Path, filename: str) -> subprocess.CompletedProcess[str]:
        (directory / filename).write_text("synthetic directory rights proof\n", encoding="utf-8")
        configured = run_as(
            "fixture",
            "unused",
            ["-C", directory.name, "config", "user.email", "fixture@example.invalid"],
            directory.parent,
        )
        if configured.returncode:
            return configured
        configured = run_as(
            "fixture",
            "unused",
            ["-C", directory.name, "config", "user.name", "Synthetic Fixture"],
            directory.parent,
        )
        if configured.returncode:
            return configured
        return run_as(
            "fixture",
            "unused",
            ["-C", directory.name, "add", filename],
            directory.parent,
        )

    with tempfile.TemporaryDirectory(prefix="directory-rights-", dir=ROOT / "work") as directory:
        work = Path(directory)
        reader_clone = run_as(
            "ldap-reader", DIRECTORY_USERS["ldap-reader"], ["clone", repository_url, "reader"], work
        )
        reader_commit = (
            commit_fixture(work / "reader", "reader-proof.txt")
            if reader_clone.returncode == 0
            else reader_clone
        )
        if reader_commit.returncode == 0:
            reader_commit = run_as(
                "fixture",
                "unused",
                ["-C", "reader", "commit", "-m", "synthetic rights proof"],
                work,
            )
        reader_push = (
            run_as(
                "ldap-reader",
                DIRECTORY_USERS["ldap-reader"],
                ["-C", "reader", "push", "origin", "HEAD:refs/heads/reader-must-not-exist"],
                work,
            )
            if reader_commit.returncode == 0
            else reader_commit
        )
        writer_clone = run_as(
            "ldap-writer", DIRECTORY_USERS["ldap-writer"], ["clone", repository_url, "writer"], work
        )
        writer_commit = (
            commit_fixture(work / "writer", "writer-proof.txt")
            if writer_clone.returncode == 0
            else writer_clone
        )
        if writer_commit.returncode == 0:
            writer_commit = run_as(
                "fixture",
                "unused",
                ["-C", "writer", "commit", "-m", "synthetic rights proof"],
                work,
            )
        writer_push = (
            run_as(
                "ldap-writer",
                DIRECTORY_USERS["ldap-writer"],
                ["-C", "writer", "push", "origin", "HEAD:refs/heads/writer-designated-branch"],
                work,
            )
            if writer_commit.returncode == 0
            else writer_commit
        )
        outsider_clone = run_as(
            "ldap-outsider",
            DIRECTORY_USERS["ldap-outsider"],
            ["clone", repository_url, "outsider"],
            work,
        )
    return {
        "reader_clone_allowed": reader_clone.returncode == 0,
        "reader_push_denied": reader_push.returncode != 0,
        "writer_clone_allowed": writer_clone.returncode == 0,
        "writer_push_allowed": writer_push.returncode == 0,
        "outsider_clone_denied": outsider_clone.returncode != 0,
    }


def _directory_authority_snapshot() -> dict[str, Any]:
    users = remote("GET", "/admin/users?limit=50")
    functional = {
        user["login"]: {
            "id": user["id"],
            "is_admin": bool(user.get("is_admin")),
            "source_id": user.get("source_id"),
        }
        for user in users
        if user.get("login") in {"ldap-reader", "ldap-writer", "ldap-outsider"}
    }
    teams = remote("GET", "/orgs/source-export-lab/teams")
    team_snapshot: dict[str, Any] = {}
    for team in teams:
        if team["name"] not in {"reader", "writer", "Owners"}:
            continue
        members = remote("GET", f"/teams/{team['id']}/members")
        repositories = remote("GET", f"/teams/{team['id']}/repos")
        team_snapshot[team["name"]] = {
            "permission": team.get("permission"),
            "units": sorted(team.get("units", [])),
            "unit_permissions": team.get("units_map", {}),
            "members": sorted(member["login"] for member in members),
            "repositories": sorted(repository["full_name"] for repository in repositories),
        }
    reader = team_snapshot.get("reader", {})
    writer = team_snapshot.get("writer", {})
    owners = team_snapshot.get("Owners", {})
    selected = "source-export-lab/selected"
    return {
        "functional_users": functional,
        "teams": team_snapshot,
        "all_functional_users_non_admin": set(functional)
        == {
            "ldap-reader",
            "ldap-writer",
            "ldap-outsider",
        }
        and all(not item["is_admin"] for item in functional.values()),
        "owners_members_only_admin": owners.get("members") == ["gitferry-admin"],
        "intended_team_members": reader.get("members") == ["ldap-reader"]
        and writer.get("members") == ["ldap-writer"],
        "selected_repository_relations": selected in reader.get("repositories", [])
        and selected in writer.get("repositories", [])
        and "source-export-lab/not-selected" not in reader.get("repositories", [])
        and "source-export-lab/not-selected" not in writer.get("repositories", []),
        "reader_units_observed": reader.get("units")
        == ["repo.code", "repo.issues", "repo.releases"],
        "writer_units_observed": writer.get("units")
        == ["repo.code", "repo.issues", "repo.releases"],
        "reader_unit_permissions": reader.get("unit_permissions")
        == {
            "repo.code": "read",
            "repo.issues": "read",
            "repo.releases": "read",
        },
        "writer_unit_permissions": writer.get("unit_permissions")
        == {
            "repo.code": "write",
            "repo.issues": "write",
            "repo.releases": "write",
        },
    }


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
    with plan_worker.proxy(preserve_origin=True):
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
            source_login: remote("GET", f"/users/{target_login}")["id"]
            for source_login, target_login in (
                ("issue-author", "ldap-reader"),
                ("comment-author", "ldap-writer"),
            )
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


def prepare_browser() -> dict[str, Any]:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (DATABASE.parent, ROOT / "packages", ROOT / "work", ROOT / "secrets"):
        directory.mkdir(mode=0o700, exist_ok=True)
    shutil.copyfile("/tmp/package-admission-trusted.pem", ROOT / "trusted.pem")
    shutil.copyfile("/tmp/package-admission-trusted.key", ROOT / "trusted.key")
    directory_ca = Path("/run/directory-ca/ca.pem")
    if directory_ca.is_file():
        shutil.copyfile(directory_ca, ROOT / "directory.pem")
    _mint_user_tokens(ROOT / "secrets")
    state = StateStore(DATABASE)
    DATABASE.chmod(0o600)
    AuthService(state).bootstrap_admin("synthetic.admin", "synthetic integration passphrase")
    service = PackageAdmissions(state, RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work"))
    counts = _counts(service)
    if any(counts.values()):
        raise RuntimeError("browser setup unexpectedly contains application authority")
    return {
        "counts": counts,
        "target_only": _unreachable("source", 3000),
        "external_unreachable": _unreachable("1.1.1.1", 80),
    }


def serve_browser() -> None:
    os.environ.update(
        {
            "GITFERRY_RUNTIME_MANAGED": "1",
            "GITFERRY_STATE_DB": str(DATABASE),
            "GITFERRY_PACKAGE_DIR": str(ROOT / "packages"),
            "GITFERRY_WORK_DIR": str(ROOT / "work"),
            "GITFERRY_SECRET_DIR": str(ROOT / "secrets"),
            "GITFERRY_CA_DIR": str(ROOT),
        }
    )
    plan_worker.ROOT, plan_worker.URL = ROOT, URL
    with plan_worker.proxy(preserve_origin=True):
        serve(ROOT)


def start_browser() -> dict[str, Any]:
    pid_path = ROOT / "browser-server.pid"
    if pid_path.exists():
        raise RuntimeError("browser server already has a registered process")
    with (ROOT / "browser-server.log").open("ab") as log:
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, __file__, "serve-browser", "--context", CONTEXT],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path.write_text(str(process.pid))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("managed browser server stopped before readiness")
        try:
            if (
                httpx.get("http://localhost:8080/healthz", timeout=1, trust_env=False).status_code
                == 200
            ):
                return {"status": "ready", "pid": process.pid}
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("managed browser server readiness timed out")


def stop_browser() -> dict[str, Any]:
    pid_path = ROOT / "browser-server.pid"
    pid = int(pid_path.read_text())
    command = Path(f"/proc/{pid}/cmdline").read_bytes()
    if b"package_admission_worker.py" not in command or b"serve-browser" not in command:
        raise RuntimeError("browser process identity changed")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = Path(f"/proc/{pid}/stat")
        if process_exited(status):
            pid_path.unlink()
            return {"status": "stopped"}
        time.sleep(0.1)
    raise RuntimeError("browser server did not stop")


def browser_upload_evidence() -> dict[str, Any]:
    state = StateStore(DATABASE)
    service = PackageAdmissions(state, RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work"))
    counts = _counts(service)
    with state.read_connection() as connection:
        uploads = connection.execute("SELECT * FROM package_uploads").fetchall()
        instances = connection.execute("SELECT instance_key FROM instances").fetchall()
    if len(uploads) != 1 or uploads[0]["status"] != "ready":
        raise RuntimeError("browser did not upload exactly one validated package")
    if [row[0] for row in instances] != [TARGET] or any(
        v for k, v in counts.items() if k != "instances"
    ):
        raise RuntimeError("upload granted local authority or admitted facts")
    row = dict(uploads[0])
    with httpx.Client(base_url="http://localhost:8080", trust_env=False, timeout=30) as client:
        headers = _api_login(client)
        resumed = client.post(
            f"/api/v1/package-uploads/{row['upload_id']}/resume", json={}, headers=headers
        )
        resumed.raise_for_status()
    after = _counts(service)
    if counts != after or resumed.json()["status"] != "ready":
        raise RuntimeError("explicit receipt resume changed admission state")
    result = {
        "status": row["status"],
        "package_ref": row["upload_id"],
        "digest": row["package_digest"],
        "archive_sha256": row["archive_sha256"],
        "resumed_status": resumed.json()["status"],
        "counts_unchanged": counts == after,
    }
    # Boolean derives from the complete asserted snapshot above, not a placeholder.
    (ROOT / "browser-upload.json").write_text(json.dumps(result))
    accounts = remote("GET", "/admin/users?limit=50")
    (ROOT / "browser-accounts-before.json").write_text(
        json.dumps(sorted((user["id"], user["login"], user["is_admin"]) for user in accounts))
    )
    return result


def browser_structure_result() -> dict[str, Any]:
    """Verify the first plan wrote only structure/Git facts, before bindings."""
    state = StateStore(DATABASE)
    service = PackageAdmissions(state, RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work"))
    plans = PlanService(state).list()
    if len(plans) != 1 or plans[0]["status"] != "confirmed":
        raise RuntimeError("structure-only browser plan was not confirmed")
    plan = plans[0]
    operation_types = [step["operation_type"] for step in plan["operations"]]
    forbidden = {
        "create_issue",
        "update_issue",
        "create_comment",
        "update_comment",
        "add_team_member",
    }
    account_operations = [
        operation for operation in operation_types if "user" in operation or "account" in operation
    ]
    if account_operations:
        raise RuntimeError("structure-only plan contained account operations")
    if forbidden.intersection(operation_types):
        raise RuntimeError("structure-only plan contained metadata or membership operations")
    counts = _counts(service)
    with state.read_connection() as connection:
        metadata_mappings = connection.execute(
            "SELECT count(*) FROM object_mappings WHERE target_instance_key=? "
            "AND target_object_type IN ('issue', 'comment')",
            (TARGET,),
        ).fetchone()[0]
    if counts["identity_bindings"] or metadata_mappings:
        raise RuntimeError("structure-only phase created identity bindings or metadata mappings")
    teams = remote("GET", "/orgs/source-export-lab/teams")
    owners = [team for team in teams if team["name"] == "Owners"]
    if len(owners) != 1:
        raise RuntimeError("technical organization ownership is not explicit")
    owner_members = remote("GET", f"/teams/{owners[0]['id']}/members")
    if [user["login"] for user in owner_members] != ["gitferry-admin"]:
        raise RuntimeError("unexpected functional organization owner")
    memberships = sum(
        len(remote("GET", f"/teams/{team['id']}/members"))
        for team in teams
        if team["name"] != "Owners"
    )
    if memberships:
        raise RuntimeError("structure-only phase created functional team memberships")
    accounts = remote("GET", "/admin/users?limit=50")
    actual_accounts = sorted([user["id"], user["login"], user["is_admin"]] for user in accounts)
    if actual_accounts != json.loads((ROOT / "browser-accounts-before.json").read_text()):
        raise RuntimeError("structure-only import changed native accounts")
    for repository in ("source-export-lab/selected", "source-export-lab/selected-extra"):
        if remote("GET", f"/repos/{repository}/issues?state=all&type=issues"):
            raise RuntimeError("structure-only import created native issues")
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "operation_types": operation_types,
        "no_metadata_or_memberships": True,
        "no_account_operations": not account_operations,
        "no_identity_bindings": counts["identity_bindings"] == 0,
        "no_target_memberships": memberships == 0,
        "account_count_before": len(
            json.loads((ROOT / "browser-accounts-before.json").read_text())
        ),
        "account_count_after": len(actual_accounts),
        "account_counts_unchanged": len(actual_accounts)
        == len(json.loads((ROOT / "browser-accounts-before.json").read_text())),
        "metadata_mappings": metadata_mappings,
        "object_counts": counts,
    }


def browser_result() -> dict[str, Any]:
    global PACKAGE_REF
    state = StateStore(DATABASE)
    service = PackageAdmissions(state, RuntimePaths(DATABASE, ROOT / "packages", ROOT / "work"))
    upload_evidence = json.loads((ROOT / "browser-upload.json").read_text())
    browser = json.loads((ROOT / "browser-evidence.json").read_text())
    PACKAGE_REF = upload_evidence["package_ref"]
    admitted = service.list()[0]
    plans = PlanService(state)
    summaries = plans.list()
    if len(summaries) != 2:
        raise RuntimeError("browser did not create structure and full plans")
    plans_by_id = {summary["plan_id"]: plans.get(summary["plan_id"]) for summary in summaries}
    structure = next(
        (
            plan
            for plan in plans_by_id.values()
            if not any(
                step["operation_type"]
                in {
                    "create_issue",
                    "create_comment",
                    "update_issue",
                    "update_comment",
                    "add_team_member",
                }
                for step in plan["operations"]
            )
        ),
        None,
    )
    plan = next(
        (candidate for candidate in plans_by_id.values() if candidate is not structure), None
    )
    if structure is None or plan is None or structure["status"] != "confirmed":
        raise RuntimeError("structure and full plans were not both confirmed")
    structure_ids = {step["operation_id"] for step in structure["operations"]}
    full_ids = {step["operation_id"] for step in plan["operations"]}
    if not structure_ids.issubset(full_ids):
        raise RuntimeError("full plan did not reuse the confirmed structure operations")
    if plan["status"] != "confirmed":
        raise RuntimeError("browser plan is not confirmed")
    result = plan
    repository, refs_remote, git_visibility = _git_visibility()
    issue = remote("GET", "/repos/source-export-lab/selected/issues/1")
    comments = remote("GET", "/repos/source-export-lab/selected/issues/1/comments")
    artifact = service.artifact(PACKAGE_REF)
    transferred = {}
    for selected in artifact.document["repositories"]:
        target, branch, _ = _git_visibility(selected["repository"])
        bundle = artifact.directory / selected["bundle"]["path"]
        heads = {
            line.split(maxsplit=1)[1]: line.split(maxsplit=1)[0]
            for line in subprocess.run(
                [shutil.which("git") or "/usr/bin/git", "bundle", "list-heads", str(bundle)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            if line.split(maxsplit=1)[1].startswith("refs/")
        }
        if heads != {"refs/heads/main": branch["commit"]["id"]}:
            raise RuntimeError("selected repository refs differ from the received bundle")
        transferred[selected["repository"]] = {"target_id": str(target["id"]), "refs": heads}
    try:
        remote("GET", "/repos/source-export-lab/not-selected")
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
    else:
        raise RuntimeError("unselected source repository was transferred")
    native_payloads = verify_native_browser(
        ROOT,
        ROOT / "packages" / PACKAGE_REF,
        (ROOT / "secrets" / "gitferry-admin").read_text().strip(),
        service.artifact(PACKAGE_REF).document,
    )
    bundle_refs = transferred["source-export-lab/selected"]["refs"]
    events = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    with service.state.read_connection() as connection:
        source_exports_rows = connection.execute("SELECT count(*) FROM source_exports").fetchone()[
            0
        ]
    return {
        "native_payloads": native_payloads,
        "selected_repositories": transferred,
        "unselected_absent": True,
        "status": result["status"],
        "package_digest": admitted["digest_sha256"],
        "review_missing_bindings": browser["review_missing_bindings"],
        "blocked_without_bindings": browser["blocked_without_bindings"],
        "plan_id": plan["plan_id"],
        "structure_plan_id": structure["plan_id"],
        "structure_operation_ids_reused": len(structure_ids),
        "repository": repository,
        "issue_author": issue["user"]["login"],
        "issue_author_id": issue["user"]["id"],
        "comment_author": comments[0]["user"]["login"] if comments else "",
        "comment_author_id": comments[0]["user"]["id"] if comments else None,
        "target_author_ids": {
            source_login: remote("GET", f"/users/{target_login}")["id"]
            for source_login, target_login in (
                ("issue-author", "ldap-reader"),
                ("comment-author", "ldap-writer"),
            )
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
        "evidence_digest": plan["evidence_digest"],
        "target_only": _unreachable("source", 3000),
        "external_unreachable": _unreachable("1.1.1.1", 80),
        "source_exports_rows": source_exports_rows,
        "upload": upload_evidence,
    }


def replay() -> dict[str, Any]:
    global PACKAGE_REF
    state = StateStore(DATABASE)
    browser_upload = ROOT / "browser-upload.json"
    if browser_upload.exists():
        PACKAGE_REF = json.loads(browser_upload.read_text())["package_ref"]
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
    if browser_upload.exists():
        saved = PlanService(state)
        summaries = saved.list()
        full_summary = next(
            summary
            for summary in summaries
            if any(
                step["operation_type"]
                in {
                    "create_issue",
                    "create_comment",
                    "update_issue",
                    "update_comment",
                    "add_team_member",
                }
                for step in summary["operations"]
            )
        )
        confirmed = saved.get(full_summary["plan_id"])
        refs = confirmed["document"]["inventory"]["request"]["author_credential_refs"]
    else:
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
    # Run after replay/reference checks so the writer proof's new branch cannot
    # alter the original imported refs or the replay no-write evidence.
    effective_rights = _directory_effective_rights()
    authority_snapshot = _directory_authority_snapshot()
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
        "effective_rights": effective_rights,
        "authority_snapshot": authority_snapshot,
    }


def collision_snapshot() -> dict[str, Any]:
    teams = remote("GET", "/orgs/source-export-lab/teams")
    # Gitea enumerates this permission set in varying order; retain every value.
    for team in teams:
        team["units"] = sorted(team["units"])
    return {
        "repository": remote("GET", "/repos/source-export-lab/selected"),
        "issues": remote("GET", "/repos/source-export-lab/selected/issues"),
        "comments": remote("GET", "/repos/source-export-lab/selected/issues/1/comments"),
        "teams": teams,
    }


def browser_preview() -> dict[str, Any]:
    state = StateStore(DATABASE)
    plans = PlanService(state).list()
    drafts = [plan for plan in plans if plan["status"] == "draft"]
    if len(drafts) != 1 or drafts[0]["preview"]["status"] != "passed":
        raise RuntimeError("browser did not finish a separate draft preview")
    confirmed_ids = {
        step["operation_id"]
        for plan in plans
        if plan["status"] == "confirmed"
        for step in plan["operations"]
        if step["status"] == "confirmed"
    }
    if any(
        step["status"] not in {"prepared", "blocked_parent"}
        and not (step["status"] == "confirmed" and step["operation_id"] in confirmed_ids)
        for step in drafts[0]["operations"]
    ):
        raise RuntimeError("preview changed an operation to an execution state")
    events = ROOT / "remote-events.jsonl"
    observed = (
        [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
    )
    before_count_path = ROOT / "browser-preview-before-count"
    before_count = int(before_count_path.read_text()) if before_count_path.exists() else 0
    writes = [
        event for event in observed[before_count:] if not event["path"].endswith("/git-upload-pack")
    ]
    fresh_ids = {
        step["operation_id"]
        for step in drafts[0]["operations"]
        if step["operation_id"] not in confirmed_ids
    }
    with state.read_connection() as connection:
        sent = any(
            row[0] in fresh_ids
            for row in connection.execute(
                "SELECT operation_id FROM operation_events WHERE to_status='executing'"
            )
        )
    if writes or sent:
        raise RuntimeError("preview sent a target write before operator confirmation")
    return {
        "passed": True,
        "no_remote_writes": True,
        "generation": drafts[0]["preview"]["generation"],
    }


def browser_preview_snapshot() -> dict[str, Any]:
    events = ROOT / "remote-events.jsonl"
    count = len(events.read_text().splitlines()) if events.exists() else 0
    (ROOT / "browser-preview-before-count").write_text(str(count), encoding="ascii")
    return {"remote_event_count": count}


def collision_result() -> dict[str, Any]:
    state = StateStore(DATABASE)
    plans = PlanService(state).list()
    if len(plans) != 1:
        raise RuntimeError("collision probe must contain exactly one draft")
    plan = plans[0]
    if plan["status"] != "draft" or plan["preview"]["status"] != "blocked":
        raise RuntimeError("occupied target was not blocked before execution")
    if not any(step["status"] == "conflict" for step in plan["operations"]):
        raise RuntimeError("collision did not preserve the operation conflict")
    if any(
        step["status"] not in {"prepared", "conflict", "blocked_parent"}
        for step in plan["operations"]
    ):
        raise RuntimeError("collision probe sent or confirmed an operation")
    with state.read_connection() as connection:
        sent = connection.execute(
            "SELECT count(*) FROM operation_events WHERE to_status='executing'"
        ).fetchone()[0]
    events = ROOT / "remote-events.jsonl"
    if sent or (events.exists() and events.read_text().strip()):
        raise RuntimeError("collision preview performed a target write")
    return {"blocked": True, "no_remote_writes": True, "snapshot": collision_snapshot()}


def main() -> None:
    global CONTEXT, ROOT, DATABASE
    parser = argparse.ArgumentParser()
    actions = {
        "initial": initial,
        "replay": replay,
        "prepare-browser": prepare_browser,
        "start-browser": start_browser,
        "stop-browser": stop_browser,
        "browser-upload": browser_upload_evidence,
        "browser-result": browser_result,
        "browser-structure-result": browser_structure_result,
        "browser-preview": browser_preview,
        "browser-preview-snapshot": browser_preview_snapshot,
        "collision-snapshot": collision_snapshot,
        "collision-result": collision_result,
    }
    parser.add_argument("mode", choices=(*actions, "serve-browser"))
    parser.add_argument("--context", choices=("normal", "collision"), default="normal")
    args = parser.parse_args()
    CONTEXT = args.context
    if CONTEXT == "collision":
        ROOT = Path("/state/package-collision")
        DATABASE = ROOT / "state" / "state.sqlite3"
    if args.mode == "serve-browser":
        serve_browser()
        return
    try:
        print(json.dumps(actions[args.mode](), sort_keys=True))
    except Exception as error:
        evidence = {
            "exception_type": type(error).__name__,
            "frames": [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(error.__traceback__)
            ],
        }
        print("GITFERRY_PROBE_ERROR " + json.dumps(evidence), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
