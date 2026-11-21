"""Run a real source-only capture inside the source-network container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import httpx
import plan_worker
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.application_package import inspect_package
from gitferry.auth import AuthService
from gitferry.connection_probe import LocalReferences, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.runtime import RuntimePaths
from gitferry.source_exports import SourceExports
from gitferry.state_store import StateStore

ROOT = Path("/state/source-export")
DATABASE = ROOT / "state" / "state.sqlite3"
PACKAGE_DIR = ROOT / "packages"
WORK_DIR = ROOT / "work"
REPOSITORY = "source-export-lab/selected"
SOURCE = "source"
RECIPIENT = "target"


def _source_request(
    method: str, path: str, *, username: str = "issue-author", **kwargs: Any
) -> httpx.Response:
    response = httpx.request(
        method,
        "http://source:3000/api/v1" + path,
        auth=(username, os.environ["GITFERRY_IT_USER_PASSWORD"]),
        timeout=20,
        trust_env=False,
        **kwargs,
    )
    response.raise_for_status()
    return response


def _token() -> str:
    supplied = Path("/tmp/source-export-admin-token")
    if supplied.is_file():
        return supplied.read_text(encoding="ascii").strip()
    value = _source_request(
        "POST",
        "/users/issue-author/tokens",
        json={"name": "source-export-probe", "scopes": ["all"]},
    ).json()
    return str(value["sha1"])


def _admin_request(token: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = "token " + token
    response = httpx.request(
        method,
        "http://source:3000/api/v1" + path,
        headers=headers,
        timeout=20,
        trust_env=False,
        **kwargs,
    )
    response.raise_for_status()
    return response


def _setup_source_repository(token: str) -> None:
    """Create the bounded source fixture before capture begins."""
    _admin_request(
        token,
        "POST",
        "/orgs",
        json={"username": "source-export-lab", "visibility": "private"},
    )
    team = _admin_request(
        token,
        "POST",
        "/orgs/source-export-lab/teams",
        json={
            "name": "read-write",
            "permission": "write",
            "units_map": {"repo.code": "write", "repo.issues": "write"},
        },
    ).json()
    for username in ("issue-author", "comment-author"):
        _admin_request(token, "PUT", f"/teams/{team['id']}/members/{username}")
    _admin_request(
        token,
        "POST",
        "/orgs/source-export-lab/repos",
        json={"name": "selected", "private": True, "auto_init": True},
    )
    _admin_request(token, "PUT", f"/teams/{team['id']}/repos/source-export-lab/selected")


def _service() -> SourceExports:
    state = StateStore(DATABASE)
    return SourceExports(
        state,
        RuntimePaths(DATABASE, PACKAGE_DIR, WORK_DIR),
        LocalReferences(ROOT / "secrets", ROOT / "ca"),
    )


def _unreachable(host: str, port: int) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=2):
            return False
    except OSError:
        return True


def _api_application(state: StateStore):
    # Exercise the actual managed runtime, including its worker wiring. Keep
    # storage roots disjoint and references local to this source-only context.
    os.environ.update(
        {
            "GITFERRY_RUNTIME_MANAGED": "1",
            "GITFERRY_STATE_DB": str(DATABASE),
            "GITFERRY_PACKAGE_DIR": str(PACKAGE_DIR),
            "GITFERRY_WORK_DIR": str(WORK_DIR),
            "GITFERRY_SECRET_DIR": str(ROOT / "secrets"),
            "GITFERRY_CA_DIR": str(ROOT / "ca"),
        }
    )
    DATABASE.chmod(0o600)
    auth = AuthService(state)
    if not auth.bootstrap_completed():
        auth.bootstrap_admin("synthetic.admin", "synthetic integration passphrase")
    return create_app(auth_service=auth, session_cookie_secure=False)


def _wait_for_ready(client: TestClient, export_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 180
    poll_wait = threading.Event()
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/exports/{export_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] in {"ready", "failed"}:
            if job["status"] == "failed":
                raise RuntimeError("source export delivery failed")
            return job
        poll_wait.wait(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
    raise RuntimeError("source export delivery did not reach a terminal state")


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
        raise RuntimeError("API login did not issue a CSRF cookie")
    return {"x-gitferry-csrf": csrf}


def _download(client: TestClient, export_id: str, archive: Path) -> dict[str, str | int]:
    with client.stream("GET", f"/api/v1/exports/{export_id}/download") as response:
        response.raise_for_status()
        digest = hashlib.sha256()
        size = 0
        with archive.open("wb") as output:
            for chunk in response.iter_bytes():
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        return {
            "archive_sha256": digest.hexdigest(),
            "archive_bytes": size,
            "header_sha256": response.headers["x-archive-sha256"],
            "header_bytes": int(response.headers["content-length"]),
        }


def capture() -> dict[str, Any]:
    shutil.rmtree(ROOT, ignore_errors=True)
    for directory in (DATABASE.parent, PACKAGE_DIR, WORK_DIR, ROOT / "secrets", ROOT / "ca"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = _token()
    _setup_source_repository(token)
    # Seed the selected source object before the read-only capture starts.  The
    # capture itself is then limited to GETs plus Git upload-pack POSTs.
    issue = _source_request(
        "POST",
        "/repos/" + REPOSITORY + "/issues",
        json={"title": "Source export issue", "body": "Synthetic source-only capture"},
    ).json()
    _source_request(
        "POST",
        f"/repos/{REPOSITORY}/issues/{issue['number']}/comments",
        username="comment-author",
        json={"body": "Synthetic source export comment"},
    )
    shutil.copyfile("/tmp/source-export-trusted.pem", ROOT / "trusted.pem")
    shutil.copyfile("/tmp/source-export-trusted.key", ROOT / "trusted.key")
    shutil.copyfile(ROOT / "trusted.pem", ROOT / "ca" / "source-ca.pem")
    (ROOT / "secrets" / "source-token").write_text(token, encoding="ascii")
    (ROOT / "secrets" / "source-token").chmod(0o600)
    state = StateStore(DATABASE)
    profiles = ProfileService(state)
    plan_worker.ROOT = ROOT
    plan_worker.URL = "http://source:3000"
    with plan_worker.proxy():
        profiles.create(
            ConnectionProfile(
                SOURCE,
                "Synthetic source",
                plan_worker.HTTPS,
                "source-export-install",
                "source-token",
                "source-ca.pem",
            )
        )
        state.register_instance(
            RECIPIENT, "Synthetic target", "https://target.invalid", "target-export-install"
        )
        evidence = probe_connection(
            profiles,
            SOURCE,
            expected_revision=1,
            references=LocalReferences(ROOT / "secrets", ROOT / "ca"),
        )
        approve_connection(
            profiles, SOURCE, expected_revision=1, evidence_digest=evidence["evidence_digest"]
        )
        request = {
            "version": 1,
            "source_instance_key": SOURCE,
            "source_profile_revision": 1,
            "recipient_instance_key": RECIPIENT,
            "repositories": [{"repository": REPOSITORY, "global_id": ""}],
            "include_metadata": True,
            "include_members": True,
        }
        request["repositories"][0]["global_id"] = str(
            _source_request("GET", "/repos/" + REPOSITORY).json()["id"]
        )
        with TestClient(_api_application(state), base_url="http://testserver") as client:
            client.get("/readyz").raise_for_status()
            headers = _api_login(client)
            queued = client.post(
                "/api/v1/exports",
                json={"export_id": "source-export-real", "request": request},
                headers=headers,
            )
            queued.raise_for_status()
            if queued.status_code != 202:
                raise RuntimeError("source queue did not acknowledge acceptance")
            delivered = _wait_for_ready(client, "source-export-real")
            api_archive = _download(client, "source-export-real", ROOT / "source-export.zip")
        service = SourceExports(
            state,
            RuntimePaths(DATABASE, PACKAGE_DIR, WORK_DIR),
            LocalReferences(ROOT / "secrets", ROOT / "ca"),
        )
        # Preserve the capture record's historical `captured` semantics while
        # recording the separate API delivery state and streamed response facts.
        captured = service.get("source-export-real")
        artifact = inspect_package(PACKAGE_DIR / "source-export-real")
        archive = ROOT / "source-export.zip"
        manifest = artifact.directory / "metadata" / "manifest.json"
        events = [
            json.loads(line) for line in (ROOT / "remote-events.jsonl").read_text().splitlines()
        ]
        return {
            "job": captured,
            "api_queue_status": queued.json()["status"],
            "api_queue_export_id": queued.json().get("export_id"),
            "api_delivery": delivered,
            "api_download": api_archive,
            "package_digest": artifact.digest_sha256,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "archive_bytes": archive.stat().st_size,
            "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "proxy_calls": events,
            "source_api_writes": [
                call
                for call in events
                if call["method"] in {"POST", "PUT", "PATCH", "DELETE"}
                and "/git-upload-pack" not in call["path"]
            ],
            "git_upload_pack_posts": [
                call
                for call in events
                if call["method"] == "POST" and call["path"].endswith("/git-upload-pack")
            ],
            "source_db": str(DATABASE),
            "source_db_sha256": hashlib.sha256(DATABASE.read_bytes()).hexdigest(),
            "source_exports_rows": len(service.list()),
            "source_target_unreachable": _unreachable("target", 3000),
            "source_external_unreachable": _unreachable("1.1.1.1", 80),
            "package": str(archive),
        }


def replay() -> dict[str, Any]:
    state = StateStore(DATABASE)
    service = _service()
    before = service.get("source-export-real")
    events_before = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    # Keep the original core replay proof, then repeat the browser command too.
    # The source TLS proxy is no longer running in this fresh process.
    captured_again = service.run("source-export-real")
    replay_archive = ROOT / "source-export-replay.zip"
    with TestClient(_api_application(state), base_url="http://testserver") as client:
        client.get("/readyz").raise_for_status()
        headers = _api_login(client)
        after = client.post(
            "/api/v1/exports",
            json={"export_id": "source-export-real", "request": before["request"]},
            headers=headers,
        )
        after.raise_for_status()
        details = after.json()
        downloaded = _download(client, "source-export-real", replay_archive)
    events_after = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    return {
        "status": before["status"],
        "package_digest": before["package_digest"],
        "archive_sha256": downloaded["archive_sha256"],
        "api_status": details["status"],
        "no_source_access": events_before == events_after,
        "unchanged": before == captured_again == service.get("source-export-real"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "replay"))
    args = parser.parse_args()
    try:
        print(json.dumps(capture() if args.mode == "capture" else replay(), sort_keys=True))
    except Exception as error:
        # Retain useful code locations without exception messages, request data,
        # credentials or private runtime paths in publishable diagnostics.
        evidence = {
            "mode": args.mode,
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
