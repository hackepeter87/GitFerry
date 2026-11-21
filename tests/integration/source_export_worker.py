"""Run a real source-only capture inside the source-network container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import httpx
import plan_worker
from browser_runtime import process_exited, serve
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.application_package import inspect_package
from gitferry.auth import AuthService
from gitferry.connection_probe import LocalReferences, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.runtime import RuntimePaths
from gitferry.source_export_jobs import SourceExportJobs
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
    reader_team = _admin_request(
        token,
        "POST",
        "/orgs/source-export-lab/teams",
        json={
            "name": "reader",
            "permission": "read",
            "units_map": {"repo.code": "read", "repo.issues": "read"},
        },
    ).json()
    writer_team = _admin_request(
        token,
        "POST",
        "/orgs/source-export-lab/teams",
        json={
            "name": "writer",
            "permission": "write",
            "units_map": {"repo.code": "write", "repo.issues": "write"},
        },
    ).json()
    _admin_request(
        token,
        "POST",
        "/orgs/source-export-lab/repos",
        json={"name": "selected", "private": True, "auto_init": True},
    )
    for team_id, username in (
        (reader_team["id"], "issue-author"),
        (writer_team["id"], "comment-author"),
    ):
        _admin_request(token, "PUT", f"/teams/{team_id}/members/{username}")
        _admin_request(token, "PUT", f"/teams/{team_id}/repos/source-export-lab/selected")
    for name in ("selected-extra", "not-selected"):
        _admin_request(
            token,
            "POST",
            "/orgs/source-export-lab/repos",
            json={"name": name, "private": True, "auto_init": True},
        )
        for team_id in (reader_team["id"], writer_team["id"]):
            _admin_request(token, "PUT", f"/teams/{team_id}/repos/source-export-lab/selected-extra")


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
    AuthService(state).bootstrap_admin("synthetic.admin", "synthetic integration passphrase")
    DATABASE.chmod(0o600)
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
        return {"status": "prepared"}


def capture_result() -> dict[str, Any]:
    state = StateStore(DATABASE)
    service = SourceExports(
        state,
        RuntimePaths(DATABASE, PACKAGE_DIR, WORK_DIR),
        LocalReferences(ROOT / "secrets", ROOT / "ca"),
    )
    jobs = service.list()
    if len(jobs) != 1:
        raise RuntimeError("browser did not create exactly one source export")
    export_id = jobs[0]["export_id"]
    captured = service.get(export_id)
    (ROOT / "browser-export-id").write_text(export_id)
    artifact = inspect_package(PACKAGE_DIR / export_id)
    archive = ROOT / "source-export-browser.zip"
    if not archive.is_file():
        raise RuntimeError("browser archive was not copied into source container")
    browser_evidence = json.loads((ROOT / "browser-evidence.json").read_text())
    archive_info = {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_bytes": archive.stat().st_size,
        "header_sha256": browser_evidence.get("download_header_sha256", ""),
        "header_bytes": browser_evidence.get("download_header_bytes", 0),
    }
    manifest = artifact.directory / "metadata" / "manifest.json"
    events = [json.loads(line) for line in (ROOT / "remote-events.jsonl").read_text().splitlines()]
    return {
        "job": captured,
        "api_queue_status": browser_evidence["queue_status"],
        "api_queue_export_id": browser_evidence["export_id"],
        "api_delivery": SourceExportJobs(state, RuntimePaths(DATABASE, PACKAGE_DIR, WORK_DIR)).get(
            export_id
        ),
        "api_download": archive_info,
        "package_digest": artifact.digest_sha256,
        "archive_sha256": archive_info["archive_sha256"],
        "archive_bytes": archive_info["archive_bytes"],
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
    export_id = (ROOT / "browser-export-id").read_text().strip()
    before = service.get(export_id)
    events_before = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    # Keep the original core replay proof, then repeat the browser command too.
    # The source TLS proxy is no longer running in this fresh process.
    captured_again = service.run(export_id)
    replay_archive = ROOT / "source-export-replay.zip"
    with TestClient(_api_application(state), base_url="http://testserver") as client:
        client.get("/readyz").raise_for_status()
        headers = _api_login(client)
        after = client.post(
            "/api/v1/exports",
            json={"export_id": export_id, "request": before["request"]},
            headers=headers,
        )
        after.raise_for_status()
        details = after.json()
        downloaded = _download(client, export_id, replay_archive)
    events_after = (ROOT / "remote-events.jsonl").read_text(encoding="utf-8").splitlines()
    return {
        "status": before["status"],
        "package_digest": before["package_digest"],
        "archive_sha256": downloaded["archive_sha256"],
        "api_status": details["status"],
        "no_source_access": events_before == events_after,
        "unchanged": before == captured_again == service.get(export_id),
    }


def serve_browser() -> None:
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
    plan_worker.ROOT, plan_worker.URL = ROOT, "http://source:3000"
    with plan_worker.proxy():
        serve(ROOT)


def start_browser() -> dict[str, Any]:
    pid_path = ROOT / "browser-server.pid"
    if pid_path.exists():
        raise RuntimeError("source browser server already has a registered process")
    with (ROOT / "browser-server.log").open("ab") as log:
        process = subprocess.Popen(  # noqa: S603 -- fixed worker entrypoint
            [sys.executable, __file__, "serve-browser"],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path.write_text(str(process.pid))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("source browser server stopped before readiness")
        try:
            if (
                httpx.get("http://localhost:8080/healthz", timeout=1, trust_env=False).status_code
                == 200
            ):
                return {"status": "ready", "pid": process.pid}
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("source browser server readiness timed out")


def stop_browser() -> dict[str, Any]:
    pid_path = ROOT / "browser-server.pid"
    pid = int(pid_path.read_text())
    command = Path(f"/proc/{pid}/cmdline").read_bytes()
    if b"source_export_worker.py" not in command or b"serve-browser" not in command:
        raise RuntimeError("source browser process identity changed")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status = Path(f"/proc/{pid}/stat")
        if process_exited(status):
            pid_path.unlink()
            return {"status": "stopped"}
        time.sleep(0.1)
    raise RuntimeError("source browser server did not stop")


def main() -> None:
    parser = argparse.ArgumentParser()
    actions = {
        "capture": capture_result,
        "prepare-browser": capture,
        "replay": replay,
        "start-browser": start_browser,
        "stop-browser": stop_browser,
    }
    parser.add_argument("mode", choices=(*actions, "serve-browser"))
    args = parser.parse_args()
    if args.mode == "serve-browser":
        serve_browser()
        return
    try:
        print(json.dumps(actions[args.mode](), sort_keys=True))
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
