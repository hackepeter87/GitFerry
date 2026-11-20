from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from gitferry.api import create_app
from gitferry.auth import AuthService
from gitferry.state_store import StateStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


def configured_app(tmp_path: Path, *, login_limit: int = 10) -> tuple[TestClient, AuthService]:
    state = StateStore(tmp_path / "state.sqlite3")
    clock = Clock()
    auth = AuthService(state, clock=clock, login_limit=login_limit)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    application = create_app(auth_service=auth, session_cookie_secure=True)
    return TestClient(application, base_url="https://testserver"), auth


def login(client: TestClient, *, username: str = "synthetic.admin") -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct synthetic passphrase"},
        headers={"sec-fetch-site": "same-origin"},
    )
    assert response.status_code == 200
    return client.cookies["gitferry_csrf"]


def test_health_and_readiness_remain_public_while_sensitive_api_requires_session(
    tmp_path: Path,
) -> None:
    client, _ = configured_app(tmp_path)

    assert client.get("/healthz").status_code == 200
    readiness = client.get("/readyz")
    assert readiness.status_code == 503
    assert readiness.json()["status"] == "starting"
    denied = client.post(
        "/api/v1/blueprints/validate",
        content="apiVersion: gitferry.io/v1\nkind: TransferBlueprint\n",
    )

    assert denied.status_code == 401
    assert denied.json() == {
        "error": {"code": "authentication_required", "message": "Authentication required"}
    }


def test_login_sets_host_only_secure_cookie_and_authorizes_admin(tmp_path: Path) -> None:
    client, _ = configured_app(tmp_path)

    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "correct synthetic passphrase",
        },
        headers={"sec-fetch-site": "same-origin"},
    )

    assert response.status_code == 200
    assert response.json()["principal"] == {
        "username": "synthetic.admin",
        "role": "admin",
    }
    session_cookie = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith("__Host-gitferry_session=")
    )
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "Path=/" in session_cookie
    assert "Domain=" not in session_cookie
    assert "Max-Age=28800" in session_cookie
    assert "session" not in response.json()
    assert "csrf" not in response.json()

    accepted = client.post(
        "/api/v1/blueprints/validate",
        content="apiVersion: gitferry.io/v99\nkind: TransferBlueprint\n",
        headers={"x-gitferry-csrf": client.cookies["gitferry_csrf"]},
    )
    assert accepted.status_code == 422
    assert accepted.json()["valid"] is False


def test_session_logout_requires_csrf_and_revokes_server_side(tmp_path: Path) -> None:
    client, _ = configured_app(tmp_path)
    csrf = login(client)

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["principal"]["role"] == "admin"
    assert session.headers["cache-control"] == "no-store"

    missing = client.post("/api/v1/auth/logout")
    assert missing.status_code == 403
    wrong = client.post("/api/v1/auth/logout", headers={"x-gitferry-csrf": "wrong-token"})
    assert wrong.status_code == 403

    logout = client.post("/api/v1/auth/logout", headers={"x-gitferry-csrf": csrf})
    assert logout.status_code == 204
    assert logout.headers["clear-site-data"] == '"cache", "cookies", "storage"'
    assert client.get("/api/v1/auth/session").status_code == 401


def test_viewer_session_cannot_call_admin_endpoint(tmp_path: Path) -> None:
    client, auth = configured_app(tmp_path)
    connection = sqlite3.connect(auth.state.path)
    admin = connection.execute(
        "SELECT password_algorithm, password_salt, password_hash, password_iterations, "
        "password_changed_at FROM local_accounts WHERE username = ?",
        ("synthetic.admin",),
    ).fetchone()
    assert admin is not None
    connection.execute(
        "INSERT INTO local_accounts "
        "(account_id, username, role, password_algorithm, password_salt, password_hash, "
        "password_iterations, password_changed_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "account-viewer",
            "synthetic.viewer",
            "viewer",
            *admin,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    csrf = login(client, username="synthetic.viewer")

    response = client.post(
        "/api/v1/blueprints/validate",
        content="apiVersion: gitferry.io/v99\nkind: TransferBlueprint\n",
        headers={"x-gitferry-csrf": csrf},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "admin_required", "message": "Administrator access required"}
    }


def test_cross_site_login_and_repeated_failures_are_rejected(tmp_path: Path) -> None:
    client, _ = configured_app(tmp_path, login_limit=2)

    cross_site = client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "correct synthetic passphrase",
        },
        headers={"sec-fetch-site": "cross-site"},
    )
    assert cross_site.status_code == 403

    for username in ("synthetic.admin", "missing.admin"):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong synthetic passphrase"},
            headers={"sec-fetch-site": "same-origin"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "Invalid credentials"

    limited = client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "correct synthetic passphrase",
        },
        headers={"sec-fetch-site": "same-origin"},
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "900"


def test_login_replaces_untrusted_session_cookie_and_rejects_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    client, _ = configured_app(tmp_path)
    fixed = f"{'0' * 32}.attacker-selected-value"
    client.cookies.set("__Host-gitferry_session", fixed, domain="testserver.local", path="/")

    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "synthetic.admin",
            "password": "correct synthetic passphrase",
        },
        headers={"sec-fetch-site": "same-origin"},
    )

    assert response.status_code == 200
    assert client.cookies["__Host-gitferry_session"] != fixed

    duplicate = client.post(
        "/api/v1/auth/login",
        content=(
            '{"username":"synthetic.admin","username":"other",'
            '"password":"correct synthetic passphrase"}'
        ),
        headers={
            "content-type": "application/json",
            "sec-fetch-site": "same-origin",
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "invalid_request"


def test_unbootstrapped_or_unconfigured_auth_fails_closed(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite3")
    bootstrapping = TestClient(
        create_app(auth_service=AuthService(state), session_cookie_secure=True),
        base_url="https://testserver",
    )
    unavailable = TestClient(create_app(), base_url="https://testserver")

    status = bootstrapping.get("/api/v1/auth/status")
    assert status.status_code == 200
    assert status.json() == {"bootstrapRequired": True}
    assert (
        bootstrapping.post(
            "/api/v1/auth/login",
            json={"username": "synthetic.admin", "password": "synthetic passphrase"},
            headers={"sec-fetch-site": "same-origin"},
        ).status_code
        == 503
    )
    assert unavailable.get("/api/v1/auth/status").status_code == 503
    assert (
        unavailable.post(
            "/api/v1/blueprints/validate",
            content="apiVersion: gitferry.io/v99\nkind: TransferBlueprint\n",
        ).status_code
        == 503
    )


def test_security_headers_are_present_without_reflecting_credentials(tmp_path: Path) -> None:
    client, _ = configured_app(tmp_path)
    input_value = "never-reflect-this-synthetic-password"

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "synthetic.admin", "password": input_value},
        headers={"sec-fetch-site": "same-origin"},
    )

    assert response.status_code == 401
    assert input_value not in response.text
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["strict-transport-security"] == ("max-age=31536000; includeSubDomains")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["cache-control"] == "no-store"


def test_asgi_request_logs_allowlist_route_metadata_without_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client, _ = configured_app(tmp_path)
    password = "never-log-this-synthetic-password"  # noqa: S105 - synthetic redaction probe
    token_like_path = "never-log-this-synthetic-token"  # noqa: S105 - synthetic probe

    with caplog.at_level(logging.INFO, logger="gitferry.http"):
        denied = client.post(
            "/api/v1/auth/login",
            json={"username": "synthetic.admin", "password": password},
            headers={"sec-fetch-site": "same-origin"},
        )
        missing = client.get(f"/{token_like_path}")

    assert denied.status_code == 401
    assert missing.status_code == 404
    assert "method=POST route=/api/v1/auth/login status=401" in caplog.text
    assert "method=GET route=<unmatched> status=404" in caplog.text
    assert password not in caplog.text
    assert "synthetic.admin" not in caplog.text
    assert token_like_path not in caplog.text
