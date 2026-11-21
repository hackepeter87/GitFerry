from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.application_package import PackageError, write_package
from gitferry.auth import AuthService
from gitferry.operations import ExecutorBusy, OperationExecutor
from gitferry.package_archive import write_package_archive
from gitferry.package_uploads import PackageUploads, UploadConflict, UploadLimit
from gitferry.runtime import RuntimePaths
from gitferry.state_store import StateStore
from test_application_package import with_metadata


def setup(tmp_path: Path, *, limit: int = 1024**2) -> tuple[PackageUploads, bytes]:
    source = tmp_path / "source"
    document, _ = with_metadata(source, 2)
    write_package(source, document)
    archive = tmp_path / "source.zip"
    write_package_archive(source, archive)
    paths = RuntimePaths(tmp_path / "state.sqlite3", tmp_path / "packages", tmp_path / "work")
    paths.package_directory.mkdir()
    paths.work_directory.mkdir()
    return PackageUploads(
        StateStore(paths.state_database), paths, max_bytes=limit
    ), archive.read_bytes()


def receive(service: PackageUploads, data: bytes, identifier: str = "upload-test") -> dict:
    with service.receiving(identifier, len(data)) as sink:
        for position in range(0, len(data), 31):
            sink.write(data[position : position + 31])
        return sink.finish()


def counts(service: PackageUploads) -> dict[str, int]:
    with service.state.read_connection() as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in (
                "instances",
                "realms",
                "identity_bindings",
                "object_origins",
                "object_mappings",
                "baselines",
                "operations",
                "package_receipts",
            )
        }


def test_files_only_receipt_and_identical_replay_grant_no_authority(tmp_path) -> None:
    service, data = setup(tmp_path)
    before = counts(service)
    result = receive(service, data)
    assert result["status"] == "ready" and result["package_ref"] == "upload-test"
    assert result["archive_sha256"] == hashlib.sha256(data).hexdigest()
    assert result["summary"]["inventory_complete"] is False
    assert result["summary"]["metadata_present"] is True
    destination = service.paths.package_directory / "upload-test"
    assert (destination / "metadata/manifest.json").read_bytes() == (
        tmp_path / "source/metadata/manifest.json"
    ).read_bytes()
    assert receive(service, data)["package_digest"] == result["package_digest"]
    assert service.resume("upload-test")["package_digest"] == result["package_digest"]
    assert len(service.list()) == 1
    assert counts(service) == before == dict.fromkeys(before, 0)


def test_conflicting_repetition_cannot_replace_confirmed_bytes(tmp_path) -> None:
    service, data = setup(tmp_path)
    original = receive(service, data)
    changed = data[:-1] + bytes([data[-1] ^ 1])
    with pytest.raises(UploadConflict, match="different bytes"):
        receive(service, changed)
    with pytest.raises(UploadConflict, match="different declared size"):
        receive(service, data + b"extra")
    assert service.get("upload-test") == original
    assert not list(service.paths.work_directory.rglob("incoming.part"))


@pytest.mark.parametrize("fault", ["too-large", "truncated", "exception", "invalid-zip"])
def test_incomplete_and_invalid_uploads_publish_no_package(tmp_path, fault) -> None:
    service, data = setup(tmp_path)
    with pytest.raises((UploadLimit, UploadConflict, PackageError, RuntimeError)):
        with service.receiving("broken", len(data)) as sink:
            if fault == "too-large":
                sink.write(data + b"extra")
            elif fault == "truncated":
                sink.write(data[:5])
                sink.finish()
            elif fault == "exception":
                sink.write(data[:5])
                raise RuntimeError("synthetic disconnect")
            else:
                sink.write(b"x" * len(data))
                sink.finish()
    assert service.get("broken")["status"] == "failed"
    assert not (service.paths.package_directory / "broken").exists()
    assert not list(service.paths.work_directory.rglob("incoming.part"))
    assert all(value == 0 for value in counts(service).values())


def test_size_limit_and_active_executor_reject_before_creating_intent(tmp_path) -> None:
    service, data = setup(tmp_path, limit=32)
    with pytest.raises(UploadLimit):
        receive(service, data)
    assert service.list() == []
    service.max_bytes = len(data)
    with OperationExecutor(service.state, "another-executor"), pytest.raises(ExecutorBusy):
        receive(service, data)
    assert service.list() == []


def test_existing_unproven_package_is_not_adopted(tmp_path) -> None:
    service, data = setup(tmp_path)
    destination = service.paths.package_directory / "upload-test"
    destination.mkdir()
    (destination / "keep").write_text("unrelated")
    with pytest.raises(UploadConflict, match="unproven"):
        receive(service, data)
    assert (destination / "keep").read_text() == "unrelated"
    assert service.get("upload-test")["status"] == "failed"


@pytest.mark.parametrize("phase", ["archive", "package"])
def test_actual_process_crash_replays_only_persisted_local_file_evidence(tmp_path, phase) -> None:
    service, data = setup(tmp_path)
    program = """
import json, os, sys
from pathlib import Path
from gitferry.package_uploads import PackageUploads
from gitferry.runtime import RuntimePaths
from gitferry.state_store import StateStore
root=Path(sys.argv[1]); db=root/'state.sqlite3'
service=PackageUploads(StateStore(db),RuntimePaths(db,root/'packages',root/'work'))
if sys.argv[2]=='resume':
    print(json.dumps(service.resume('crash')))
else:
    data=(root/'source.zip').read_bytes()
    with service.receiving('crash',len(data)) as sink:
        sink.write(data)
        hook=lambda: os._exit(91)
        phase='after_archive_hook' if sys.argv[2]=='archive' else 'after_publish_hook'
        sink.finish(**{phase:hook})
"""
    command = [sys.executable, "-c", program, str(tmp_path)]
    first = subprocess.run([*command, phase], capture_output=True, text=True, timeout=20)  # noqa: S603
    assert first.returncode == 91, first.stderr
    assert service.get("crash")["status"] == "validating"
    second = subprocess.run([*command, "resume"], capture_output=True, text=True, timeout=20)  # noqa: S603
    assert second.returncode == 0, second.stderr
    result = json.loads(second.stdout)
    assert result["status"] == "ready"
    assert result["archive_sha256"] == hashlib.sha256(data).hexdigest()
    assert all(value == 0 for value in counts(service).values())


def api(tmp_path: Path) -> tuple[TestClient, PackageUploads, bytes, dict[str, str]]:
    service, data = setup(tmp_path)
    auth = AuthService(service.state)
    auth.bootstrap_admin("synthetic.admin", "synthetic upload password")
    application = create_app(auth_service=auth)
    application.state.package_uploads = service
    client = TestClient(application, base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "synthetic upload password",
        },
    )
    assert response.status_code == 200
    return (
        client,
        service,
        data,
        {
            "content-type": "application/zip",
            "x-gitferry-archive-size": str(len(data)),
            "x-gitferry-csrf": client.cookies["gitferry_csrf"],
        },
    )


def test_protected_api_upload_resume_and_saved_summary(tmp_path) -> None:
    client, service, data, headers = api(tmp_path)
    route = "/api/v1/package-uploads/upload-http"
    assert (
        client.put(
            route,
            content=data,
            headers={k: v for k, v in headers.items() if k != "x-gitferry-csrf"},
        ).status_code
        == 403
    )
    assert service.list() == []
    uploaded = client.put(route, content=data, headers=headers)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "ready"
    assert client.get(route).json()["summary"]["metadata_present"]
    assert len(client.get("/api/v1/package-uploads").json()) == 1
    response = client.post(
        route + "/resume", json={}, headers={"x-gitferry-csrf": headers["x-gitferry-csrf"]}
    )
    assert response.status_code == 200
    assert response.json()["package_digest"] == uploaded.json()["package_digest"]
    client.cookies.clear()
    assert client.get(route).status_code == 401
    assert client.put(route, content=data, headers=headers).status_code == 401


def test_http_size_and_content_type_are_checked_before_writes(tmp_path) -> None:
    client, service, data, headers = api(tmp_path)
    route = "/api/v1/package-uploads/invalid"
    assert (
        client.put(
            route, content=data, headers={**headers, "content-type": "text/plain"}
        ).status_code
        == 415
    )
    assert (
        client.put(
            route, content=data, headers={**headers, "x-gitferry-archive-size": "1"}
        ).status_code
        == 400
    )
    assert (
        client.put(
            route, content=data, headers={**headers, "x-gitferry-archive-size": "NaN"}
        ).status_code
        == 400
    )
    assert service.list() == []


def test_receive_timeout_releases_executor_and_preserves_failed_receipt(tmp_path, monkeypatch):
    import asyncio

    import httpx

    client, service, data, headers = api(tmp_path)
    monkeypatch.setenv("GITFERRY_UPLOAD_TIMEOUT_SECONDS", "0.02")

    async def stalled_body():
        yield data[:5]
        await asyncio.Event().wait()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=client.app),
            base_url="https://testserver",
            cookies=client.cookies,
        ) as asynchronous:
            return await asynchronous.put(
                "/api/v1/package-uploads/stalled", content=stalled_body(), headers=headers
            )

    response = asyncio.run(run())
    assert response.status_code == 408
    assert service.get("stalled")["status"] == "failed"
    assert not list(service.paths.work_directory.rglob("incoming.part"))
    assert not (service.paths.package_directory / "stalled").exists()
    with OperationExecutor(service.state, "after-timeout"):
        assert all(value == 0 for value in counts(service).values())
