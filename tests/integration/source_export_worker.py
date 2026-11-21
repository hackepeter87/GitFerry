"""Run a real source-only capture inside the source-network container."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
import plan_worker
from gitferry.application_package import inspect_package
from gitferry.connection_probe import LocalReferences, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.package_archive import write_package_archive
from gitferry.runtime import RuntimePaths
from gitferry.source_exports import SourceExports
from gitferry.state_store import StateStore

ROOT = Path("/state/source-export")
DATABASE = ROOT / "state.sqlite3"
PACKAGE_DIR = ROOT / "packages"
WORK_DIR = ROOT / "work"
REPOSITORY = "access-lab/selected-private"
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


def capture() -> dict[str, Any]:
    shutil.rmtree(ROOT, ignore_errors=True)
    for directory in (PACKAGE_DIR, WORK_DIR, ROOT / "secrets", ROOT / "ca"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
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
    token = _token()
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
        service = SourceExports(
            state,
            RuntimePaths(DATABASE, PACKAGE_DIR, WORK_DIR),
            LocalReferences(ROOT / "secrets", ROOT / "ca"),
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
        job = service.prepare(request, export_id="source-export-real")
        captured = service.run(job["export_id"])
        artifact = inspect_package(PACKAGE_DIR / job["export_id"])
        archive = ROOT / "source-export.zip"
        write_package_archive(artifact.directory, archive)
        manifest = artifact.directory / "metadata" / "manifest.json"
        events = [
            json.loads(line) for line in (ROOT / "remote-events.jsonl").read_text().splitlines()
        ]
        return {
            "job": captured,
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
    service = _service()
    before = service.get("source-export-real")
    after = service.run("source-export-real")
    return {
        "status": after["status"],
        "package_digest": after["package_digest"],
        "unchanged": before == after,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "replay"))
    args = parser.parse_args()
    print(json.dumps(capture() if args.mode == "capture" else replay(), sort_keys=True))


if __name__ == "__main__":
    main()
