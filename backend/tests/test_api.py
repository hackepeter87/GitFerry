from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gitferry.api import app, create_app
from gitferry.auth import AuthService
from gitferry.state_migrations import SCHEMA_VERSION
from gitferry.state_store import StateStore

client = TestClient(app)


def authenticated_client(tmp_path: Path) -> tuple[TestClient, str]:
    auth = AuthService(StateStore(tmp_path / "auth-state.sqlite3"))
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    local_client = TestClient(
        create_app(auth_service=auth, session_cookie_secure=True),
        base_url="https://testserver",
    )
    response = local_client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "correct synthetic passphrase",
        },
        headers={"sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 200
    return local_client, local_client.cookies["gitferry_csrf"]


def test_health_reports_iteration_status() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "iteration": 1}


def test_unmanaged_development_runtime_reports_ready() -> None:
    with TestClient(create_app()) as local_client:
        response = local_client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "mode": "development-unmanaged"}


def test_managed_runtime_reports_ready_only_after_storage_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITFERRY_RUNTIME_MANAGED", "1")
    monkeypatch.setenv("GITFERRY_STATE_DB", str(tmp_path / "state" / "state.sqlite3"))
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))
    application = create_app()

    assert TestClient(application).get("/readyz").status_code == 503
    with TestClient(application) as local_client:
        response = local_client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "mode": "managed",
        "schemaVersion": SCHEMA_VERSION,
    }


def test_blueprint_validation_returns_structured_errors(tmp_path: Path) -> None:
    local_client, csrf = authenticated_client(tmp_path)
    response = local_client.post(
        "/api/v1/blueprints/validate",
        content="apiVersion: gitferry.io/v99\nkind: TransferBlueprint\n",
        headers={"content-type": "application/yaml", "x-gitferry-csrf": csrf},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["valid"] is False
    assert body["errors"]


def test_blueprint_validation_rejects_oversized_body_before_parsing(tmp_path: Path) -> None:
    local_client, csrf = authenticated_client(tmp_path)
    response = local_client.post(
        "/api/v1/blueprints/validate",
        content=b"x" * (1024 * 1024 + 1),
        headers={"content-type": "application/yaml", "x-gitferry-csrf": csrf},
    )
    assert response.status_code == 413
    assert response.json() == {
        "valid": False,
        "errors": ["blueprint exceeds the 1 MiB input limit"],
    }
