from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from gitferry.auth import (
    AuthService,
    BootstrapAlreadyCompleted,
    InvalidCredentials,
    LoginRateLimited,
    PasswordPolicyError,
    SessionDenied,
)
from gitferry.state_store import StateStore


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def service(tmp_path: Path, clock: Clock, *, login_limit: int = 10) -> AuthService:
    return AuthService(
        StateStore(tmp_path / "state.sqlite3"),
        clock=clock,
        login_limit=login_limit,
    )


def test_bootstrap_creates_one_durable_admin_without_storing_password(
    tmp_path: Path,
) -> None:
    clock = Clock()
    auth = service(tmp_path, clock)

    account = auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")

    assert account.username == "synthetic.admin"
    assert account.role == "admin"
    with pytest.raises(BootstrapAlreadyCompleted, match="already completed"):
        auth.bootstrap_admin("other.admin", "another synthetic passphrase")

    reopened = service(tmp_path, clock)
    issued = reopened.login(
        "synthetic.admin",
        "correct synthetic passphrase",
        client_key="client-a",
    )
    assert issued.principal.account_id == account.account_id
    assert issued.principal.role == "admin"

    database_bytes = (tmp_path / "state.sqlite3").read_bytes()
    wal = tmp_path / "state.sqlite3-wal"
    if wal.exists():
        database_bytes += wal.read_bytes()
    assert b"correct synthetic passphrase" not in database_bytes
    assert issued.session_token.encode() not in database_bytes
    assert issued.csrf_token.encode() not in database_bytes


def test_bootstrap_password_policy_rejects_short_or_oversized_secrets(tmp_path: Path) -> None:
    auth = service(tmp_path, Clock())

    with pytest.raises(PasswordPolicyError, match="at least 15"):
        auth.bootstrap_admin("synthetic.admin", "too short")
    with pytest.raises(PasswordPolicyError, match="at most 1024 bytes"):
        auth.bootstrap_admin("synthetic.admin", "x" * 1025)


def test_login_rate_limit_is_durable_and_does_not_reveal_account_existence(
    tmp_path: Path,
) -> None:
    clock = Clock()
    auth = service(tmp_path, clock, login_limit=2)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")

    for username in ("synthetic.admin", "missing.admin"):
        with pytest.raises(InvalidCredentials, match="invalid credentials"):
            auth.login(username, "wrong synthetic passphrase", client_key="client-a")

    reopened = service(tmp_path, clock, login_limit=2)
    with pytest.raises(LoginRateLimited, match="try again later"):
        reopened.login(
            "synthetic.admin",
            "correct synthetic passphrase",
            client_key="client-a",
        )
    with pytest.raises(LoginRateLimited, match="try again later"):
        reopened.login(
            "synthetic.admin",
            "correct synthetic passphrase",
            client_key="client-a",
        )
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
    assert connection.execute(
        "SELECT count(*) FROM auth_events WHERE outcome = 'rate_limited'"
    ).fetchone() == (1,)
    connection.close()

    clock.advance(timedelta(minutes=16))
    assert (
        reopened.login(
            "synthetic.admin",
            "correct synthetic passphrase",
            client_key="client-a",
        ).principal.role
        == "admin"
    )


def test_session_is_durable_revocable_and_expires_server_side(tmp_path: Path) -> None:
    clock = Clock()
    auth = service(tmp_path, clock)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    first = auth.login(
        "synthetic.admin",
        "correct synthetic passphrase",
        client_key="client-a",
    )

    reopened = service(tmp_path, clock)
    principal = reopened.authenticate(first.session_token)
    assert principal.username == "synthetic.admin"
    assert reopened.verify_csrf(first.session_token, first.csrf_token) == principal

    reopened.logout(first.session_token)
    with pytest.raises(SessionDenied, match="session is not valid"):
        reopened.authenticate(first.session_token)

    second = reopened.login(
        "synthetic.admin",
        "correct synthetic passphrase",
        client_key="client-a",
    )
    clock.advance(timedelta(hours=9))
    with pytest.raises(SessionDenied, match="session is not valid"):
        reopened.authenticate(second.session_token)


def test_recovery_changes_password_and_revokes_every_existing_session(tmp_path: Path) -> None:
    clock = Clock()
    auth = service(tmp_path, clock)
    account = auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    issued = auth.login(
        "synthetic.admin",
        "correct synthetic passphrase",
        client_key="client-a",
    )

    recovered = auth.recover_bootstrap_admin("replacement synthetic passphrase")

    assert recovered.account_id == account.account_id
    with pytest.raises(SessionDenied, match="session is not valid"):
        auth.authenticate(issued.session_token)
    with pytest.raises(InvalidCredentials, match="invalid credentials"):
        auth.login(
            "synthetic.admin",
            "correct synthetic passphrase",
            client_key="client-b",
        )
    assert (
        auth.login(
            "synthetic.admin",
            "replacement synthetic passphrase",
            client_key="client-b",
        ).principal.account_id
        == account.account_id
    )


def test_non_admin_session_is_authenticated_but_not_authorized_as_admin(tmp_path: Path) -> None:
    clock = Clock()
    auth = service(tmp_path, clock)
    auth.bootstrap_admin("synthetic.admin", "correct synthetic passphrase")
    connection = sqlite3.connect(tmp_path / "state.sqlite3")
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
            clock().isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    issued = auth.login(
        "synthetic.viewer",
        "correct synthetic passphrase",
        client_key="client-viewer",
    )

    assert issued.principal.role == "viewer"
    assert issued.principal.is_admin is False


def test_auth_schema_uses_hashes_and_foreign_keys(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite3")
    connection = sqlite3.connect(state.path)

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'auth_%'"
        )
    }
    session_columns = {row[1] for row in connection.execute("PRAGMA table_info(auth_sessions)")}

    assert tables == {
        "auth_bootstrap",
        "auth_events",
        "auth_login_limits",
        "auth_sessions",
    }
    assert "verifier_hash" in session_columns
    assert "csrf_hash" in session_columns
    assert "session_token" not in session_columns
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
