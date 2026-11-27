from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.connection_profiles import ProfileConflict
from gitferry.source_export_api import _stream
from gitferry.source_export_jobs import SourceExportConflict
from gitferry.state_store import StateStore


class _File(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.closed_by_test = False

    def close(self) -> None:
        self.closed_by_test = True
        super().close()


class _Worker:
    def __init__(self, alive: bool = True) -> None:
        self.thread = _Thread(alive)
        self.wake = threading.Event()


class _Thread:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class _Jobs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.file: _File | None = None

    def list(self) -> list[dict[str, object]]:
        return [{"export_id": "exp-1", "status": "queued"}]

    def get(self, export_id: str) -> dict[str, object]:
        return {"export_id": export_id, "status": "queued"}

    def recipients(self) -> list[dict[str, object]]:
        return [{"instance_key": "recipient-1"}]

    def create(self, export_id: str, request: dict[str, object]) -> dict[str, object]:
        self.calls.append(("create", (export_id, request)))
        return {"export_id": export_id, "status": "queued"}

    def queue(self, export_id: str) -> dict[str, object]:
        self.calls.append(("queue", export_id))
        return {"export_id": export_id, "status": "queued"}

    def open_download(self, export_id: str) -> tuple[_File, dict[str, object]]:
        self.file = _File(b"synthetic zip bytes")
        return self.file, {
            "export_id": export_id,
            "status": "ready",
            "archive_sha256": "a" * 64,
            "archive_size": len(b"synthetic zip bytes"),
        }


def _client(tmp_path: Path) -> tuple[TestClient, str, _Jobs]:
    auth = AuthService(StateStore(tmp_path / "state.sqlite3"))
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    app = create_app(auth_service=auth)
    jobs = _Jobs()
    app.state.export_jobs = jobs
    app.state.plan_worker = _Worker()
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.admin", "password": "correct synthetic passphrase"},
    )
    assert response.status_code == 200
    return client, client.cookies["gitferry_csrf"], jobs


def test_exports_require_authentication_and_csrf(tmp_path: Path) -> None:
    client, csrf, jobs = _client(tmp_path)
    assert client.get("/api/v1/exports").status_code == 200
    assert client.post("/api/v1/exports", json={"export_id": "x", "request": {}}).status_code == 403
    response = client.post(
        "/api/v1/exports",
        json={"export_id": "x", "request": {}},
        headers={"x-gitferry-csrf": csrf},
    )
    assert response.status_code == 202
    assert jobs.calls[0][0] == "create"
    assert client.app.state.plan_worker.wake.is_set()
    client.cookies.clear()
    for method, path in (
        ("GET", ""),
        ("GET", "/recipients"),
        ("GET", "/x"),
        ("GET", "/x/download"),
        ("POST", ""),
        ("POST", "/x/queue"),
    ):
        assert client.request(method, "/api/v1/exports" + path).status_code == 401
    assert len(jobs.calls) == 1


def test_api_preserves_the_bounded_git_return_request(tmp_path: Path) -> None:
    client, csrf, jobs = _client(tmp_path)
    request = {
        "version": 5,
        "mode": "return",
        "forward_package_id": "forward-1",
        "forward_package_digest": "a" * 64,
        "source_instance_key": "b",
        "source_profile_revision": 4,
        "recipient_instance_key": "a",
        "repositories": [{"repository": "synthetic/selected", "global_id": "91"}],
        "include_metadata": False,
        "include_members": False,
    }
    response = client.post(
        "/api/v1/exports",
        json={"export_id": "return-1", "request": request},
        headers={"x-gitferry-csrf": csrf},
    )

    assert response.status_code == 202
    assert jobs.calls == [("create", ("return-1", request))]


def test_create_and_queue_reject_extra_fields_without_service_call(tmp_path: Path) -> None:
    client, csrf, jobs = _client(tmp_path)
    headers = {"x-gitferry-csrf": csrf}
    response = client.post(
        "/api/v1/exports",
        json={"export_id": "x", "request": {}, "approval": True},
        headers=headers,
    )
    assert response.status_code == 400
    response = client.post(
        "/api/v1/exports/x/queue",
        json={"execution": "now"},
        headers=headers,
    )
    assert response.status_code == 400
    assert jobs.calls == []


@pytest.mark.parametrize("create", [False, True])
def test_dead_worker_prevents_queue_and_returns_503(tmp_path: Path, create: bool) -> None:
    client, csrf, jobs = _client(tmp_path)
    client.app.state.plan_worker = _Worker(False)
    path = "/api/v1/exports" if create else "/api/v1/exports/x/queue"
    body = {"export_id": "x", "request": {}} if create else {}
    response = client.post(path, json=body, headers={"x-gitferry-csrf": csrf})
    assert response.status_code == 503
    assert jobs.calls == []


def test_queue_wakes_live_worker_and_recipients_route_precedes_id(tmp_path: Path) -> None:
    client, csrf, jobs = _client(tmp_path)
    response = client.get("/api/v1/exports/recipients")
    assert response.status_code == 200
    assert response.json() == [{"instance_key": "recipient-1"}]
    response = client.post("/api/v1/exports/x/queue", json={}, headers={"x-gitferry-csrf": csrf})
    assert response.status_code == 202
    assert jobs.calls == [("queue", "x")]
    assert client.app.state.plan_worker.wake.is_set()


def test_download_streams_verified_archive_and_closes_file(tmp_path: Path) -> None:
    client, _, jobs = _client(tmp_path)
    response = client.get("/api/v1/exports/x/download")
    assert response.status_code == 200
    assert response.content == b"synthetic zip bytes"
    assert response.headers["content-length"] == str(len(response.content))
    assert response.headers["x-archive-sha256"] == "a" * 64
    assert response.headers["content-disposition"] == 'attachment; filename="source-export.zip"'
    assert jobs.file is not None and jobs.file.closed_by_test


@pytest.mark.parametrize("conflict", [SourceExportConflict, ProfileConflict])
def test_errors_are_sanitized(tmp_path: Path, conflict: type[Exception]) -> None:
    client, _, jobs = _client(tmp_path)

    def fail(_: str) -> dict[str, object]:
        raise conflict("secret /private/path and token")

    jobs.get = fail  # type: ignore[method-assign]
    response = client.get("/api/v1/exports/secret")
    assert response.status_code == 409
    assert "private/path" not in response.text
    assert "token" not in response.text


def test_download_iterator_closes_on_early_exit() -> None:
    file = _File(b"x" * (1024 * 1024 + 1))
    stream = _stream(file, file.close)
    assert len(next(stream)) == 1024 * 1024
    assert not file.closed_by_test
    stream.close()
    assert file.closed_by_test
