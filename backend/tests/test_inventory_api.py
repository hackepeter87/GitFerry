from __future__ import annotations

import copy
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.cli import main
from test_inventory_jobs import _configured, _document, _request


class _Thread:
    def __init__(self, alive: bool = True) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class _Worker:
    def __init__(self, alive: bool = True) -> None:
        self.thread = _Thread(alive)
        self.wake = threading.Event()


def _client(tmp_path: Path) -> tuple[TestClient, object, str]:
    _, jobs = _configured(tmp_path)
    auth = AuthService(jobs.state)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    app = create_app(auth_service=auth, session_cookie_secure=False)
    app.state.inventory_jobs = jobs
    app.state.plan_worker = _Worker()
    client = TestClient(app, base_url="https://testserver")
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.admin", "password": "correct synthetic passphrase"},
    )
    assert login.status_code == 200
    return client, jobs, client.cookies["gitferry_csrf"]


def _viewer(client: TestClient, jobs: object) -> str:
    state = jobs.state  # type: ignore[attr-defined]
    connection = sqlite3.connect(state.path)
    admin = connection.execute(
        "SELECT password_algorithm,password_salt,password_hash,password_iterations,"
        "password_changed_at "
        "FROM local_accounts WHERE username='synthetic.admin'"
    ).fetchone()
    connection.execute(
        "INSERT INTO local_accounts(account_id,username,role,password_algorithm,password_salt,"
        "password_hash,"
        "password_iterations,password_changed_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("account-viewer", "synthetic.viewer", "viewer", *admin, "2026-11-21T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.viewer", "password": "correct synthetic passphrase"},
    )
    assert login.status_code == 200
    return client.cookies["gitferry_csrf"]


def test_inventory_auth_and_csrf_reject_before_job_creation(tmp_path: Path) -> None:
    client, jobs, csrf = _client(tmp_path)
    body = {"inventory_id": "inventory-1", "request": _request()}
    client.cookies.clear()
    assert client.post("/api/v1/inventories", json=body).status_code == 401
    client, jobs, csrf = _client(tmp_path / "viewer")
    viewer_csrf = _viewer(client, jobs)
    assert (
        client.post(
            "/api/v1/inventories", json=body, headers={"x-gitferry-csrf": viewer_csrf}
        ).status_code
        == 403
    )
    client, jobs, csrf = _client(tmp_path / "csrf")
    assert client.post("/api/v1/inventories", json=body).status_code == 403
    assert jobs.list() == []


def test_valid_post_persists_intent_and_wakes_worker(tmp_path: Path) -> None:
    client, jobs, csrf = _client(tmp_path)
    response = client.post(
        "/api/v1/inventories",
        json={"inventory_id": "inventory-1", "request": _request()},
        headers={"x-gitferry-csrf": csrf},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert client.app.state.plan_worker.wake.is_set()
    assert jobs.get("inventory-1")["request"] == response.json()["request"]


def test_post_rejects_unknown_fields_and_changed_intent_without_service_mutation(
    tmp_path: Path,
) -> None:
    client, jobs, csrf = _client(tmp_path)
    headers = {"x-gitferry-csrf": csrf}
    body = {"inventory_id": "inventory-1", "request": _request()}
    assert (
        client.post(
            "/api/v1/inventories", json={**body, "extra": True}, headers=headers
        ).status_code
        == 400
    )
    assert jobs.list() == []
    assert client.post("/api/v1/inventories", json=body, headers=headers).status_code == 202
    changed = copy.deepcopy(body)
    changed["request"]["repositories"][0]["global_id"] = "8"
    conflict = client.post("/api/v1/inventories", json=changed, headers=headers)
    assert conflict.status_code == 409
    assert "8" not in conflict.text


@pytest.mark.parametrize("status", ["ready", "blocked"])
def test_get_and_document_persist_facts_and_are_no_store(tmp_path: Path, status: str) -> None:
    client, jobs, csrf = _client(tmp_path)
    request = _request()
    jobs.create("inventory-1", request)

    def observe(job: dict[str, object]):
        document = _document(job)
        if status == "blocked":
            issue = next(
                item for item in document.repositories[0].classes if item.content_class == "issues"
            )
            issue.status = "blocked"
            issue.count = None
            issue.objects = []
            issue.reason = "synthetic collector limitation"
        return document

    result = jobs.run_next(observe=observe)
    assert result is not None and result["status"] == status
    shown = client.get("/api/v1/inventories/inventory-1")
    assert shown.status_code == 200
    assert shown.headers["cache-control"] == "no-store"
    assert shown.json()["document"] == result["document"]
    downloaded = client.get("/api/v1/inventories/inventory-1/document")
    if status == "blocked":
        assert downloaded.status_code == 200
        assert json.loads(downloaded.text) == result["document"]
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["content-disposition"] == 'attachment; filename="inventory-1.json"'


def test_unavailable_worker_denies_create_before_queueing(tmp_path: Path) -> None:
    client, jobs, csrf = _client(tmp_path)
    client.app.state.plan_worker = _Worker(False)
    response = client.post(
        "/api/v1/inventories",
        json={"inventory_id": "inventory-1", "request": _request()},
        headers={"x-gitferry-csrf": csrf},
    )
    assert response.status_code == 503
    assert jobs.list() == []


def test_cli_create_list_show_document_and_run_next_share_durable_facts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client, jobs, _ = _client(tmp_path)
    database = tmp_path / "state.sqlite3"
    arguments = [
        "--database",
        str(database),
        "--inventory-id",
        "inventory-1",
        "--source-instance",
        "a",
        "--source-revision",
        "1",
        "--repository",
        "synthetic/selected=7",
    ]
    for content_class in sorted(_request()["requested_classes"]):
        arguments.extend(["--class", content_class])
    assert main(["inventories", "create", *arguments]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "queued"
    assert jobs.get("inventory-1")["status"] == "queued"
    assert main(["inventories", "list", "--database", str(database)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == client.get("/api/v1/inventories").json()
    assert (
        main(["inventories", "show", "--database", str(database), "--inventory-id", "inventory-1"])
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["request"] == created["request"]
    result = jobs.run_next(observe=lambda job: _document(job))
    assert result is not None and result["status"] == "ready"
    assert main(["inventories", "run-next", "--database", str(database)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "idle"
    assert (
        main(
            [
                "inventories",
                "document",
                "--database",
                str(database),
                "--inventory-id",
                "inventory-1",
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document == result["document"]


def test_cli_invalid_intent_errors_are_secret_safe(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, jobs = _configured(tmp_path)
    assert (
        main(
            [
                "inventories",
                "create",
                "--database",
                str(tmp_path / "state.sqlite3"),
                "--inventory-id",
                "inventory-1",
                "--source-instance",
                "../secret-path",
                "--source-revision",
                "1",
                "--repository",
                "synthetic/selected=7",
                "--class",
                "issues",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert "secret-path" not in output
    assert "synthetic-test-token" not in output
    assert jobs.list() == []
