"""Durable local administrator authentication and server-side sessions."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from gitferry.state_store import StateStore

PBKDF2_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_BYTES = 1024
SESSION_LIFETIME = timedelta(hours=8)
LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LIMIT = 10
AUTH_EVENT_RETENTION = timedelta(days=90)

_USERNAME = re.compile(r"^[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?$")
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_INVALID_SALT = b"GitFerry-invalid"
_INVALID_HASH = hashlib.pbkdf2_hmac(
    "sha256", b"invalid synthetic password", _INVALID_SALT, PBKDF2_ITERATIONS, dklen=32
)


class AuthError(RuntimeError):
    """Base class for local authentication failures safe to translate at the API boundary."""


class BootstrapAlreadyCompleted(AuthError):
    """Raised when the one-time bootstrap account already exists."""


class BootstrapRequired(AuthError):
    """Raised when authentication is attempted before local bootstrap."""


class PasswordPolicyError(AuthError):
    """Raised when a new local password violates the documented policy."""


class InvalidCredentials(AuthError):
    """Raised for every username/password mismatch without revealing which field failed."""


class LoginRateLimited(AuthError):
    """Raised after the durable login failure limit is reached."""


class SessionDenied(AuthError):
    """Raised for malformed, unknown, revoked, expired, or disabled sessions."""


class CsrfDenied(AuthError):
    """Raised when a state-changing request lacks the session-bound CSRF token."""


@dataclass(frozen=True)
class LocalAccount:
    account_id: str
    username: str
    role: str
    created_at: str


@dataclass(frozen=True)
class AuthPrincipal:
    account_id: str
    username: str
    role: str
    session_id: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    expires_at: str
    principal: AuthPrincipal


@dataclass(frozen=True)
class _PasswordRecord:
    account_id: str
    username: str
    role: str
    salt: bytes
    password_hash: bytes
    iterations: int
    changed_at: str
    disabled_at: str | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authentication clock must return a timezone-aware datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionDenied("session is not valid")
    return parsed.astimezone(UTC)


def _normalize_username(value: str, *, strict: bool) -> str:
    normalized = value.strip().lower()
    if _USERNAME.fullmatch(normalized):
        return normalized
    if strict:
        raise ValueError(
            "username must contain 1-64 lowercase letters, digits, dots, dashes, or underscores"
        )
    return "<invalid>"


def _validate_new_password(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError("password must contain at least 15 characters")
    if len(encoded) > PASSWORD_MAX_BYTES:
        raise PasswordPolicyError("password must contain at most 1024 bytes")
    return encoded


def _password_bytes_for_login(password: str) -> bytes:
    encoded = password.encode("utf-8")
    return encoded if len(encoded) <= PASSWORD_MAX_BYTES else b""


def _password_hash(password: bytes, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, salt, iterations, dklen=32)


def _token_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


def _parse_session_token(value: str) -> tuple[str, str]:
    session_id, separator, verifier = value.partition(".")
    if (
        separator != "."
        or not _SESSION_ID.fullmatch(session_id)
        or not verifier
        or len(verifier) > 128
    ):
        raise SessionDenied("session is not valid")
    try:
        verifier.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SessionDenied("session is not valid") from exc
    return session_id, verifier


class AuthService:
    """Authentication service sharing GitFerry's durable SQLite transaction boundary."""

    def __init__(
        self,
        state: StateStore,
        *,
        clock: Callable[[], datetime] = _utc_now,
        login_limit: int = LOGIN_LIMIT,
    ) -> None:
        if login_limit < 1 or login_limit > LOGIN_LIMIT:
            raise ValueError(f"login_limit must be between 1 and {LOGIN_LIMIT}")
        self.state = state
        self._clock = clock
        self._login_limit = login_limit

    def bootstrap_admin(self, username: str, password: str) -> LocalAccount:
        normalized = _normalize_username(username, strict=True)
        encoded = _validate_new_password(password)
        salt = secrets.token_bytes(16)
        password_hash = _password_hash(encoded, salt, PBKDF2_ITERATIONS)
        account_id = f"account-{uuid.uuid4().hex}"
        now = _timestamp(self._clock())
        try:
            with self.state.transaction() as connection:
                connection.execute(
                    "INSERT INTO local_accounts "
                    "(account_id, username, role, password_algorithm, password_salt, "
                    "password_hash, password_iterations, password_changed_at, created_at) "
                    "VALUES (?, ?, 'admin', 'pbkdf2-sha256', ?, ?, ?, ?, ?)",
                    (
                        account_id,
                        normalized,
                        salt,
                        password_hash,
                        PBKDF2_ITERATIONS,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO auth_bootstrap(singleton, account_id, completed_at) "
                    "VALUES (1, ?, ?)",
                    (account_id, now),
                )
                self._record_event(
                    connection,
                    account_id=account_id,
                    username=normalized,
                    event_type="bootstrap",
                    outcome="succeeded",
                    now=now,
                )
        except sqlite3.IntegrityError as exc:
            raise BootstrapAlreadyCompleted("administrator bootstrap is already completed") from exc
        return LocalAccount(account_id, normalized, "admin", now)

    def bootstrap_completed(self) -> bool:
        with self.state.read_connection() as connection:
            return (
                connection.execute("SELECT 1 FROM auth_bootstrap WHERE singleton = 1").fetchone()
                is not None
            )

    def recover_bootstrap_admin(self, password: str) -> LocalAccount:
        encoded = _validate_new_password(password)
        salt = secrets.token_bytes(16)
        password_hash = _password_hash(encoded, salt, PBKDF2_ITERATIONS)
        now = _timestamp(self._clock())
        with self.state.transaction() as connection:
            row = connection.execute(
                "SELECT a.account_id, a.username, a.role, a.created_at "
                "FROM auth_bootstrap b JOIN local_accounts a ON a.account_id = b.account_id "
                "WHERE b.singleton = 1"
            ).fetchone()
            if row is None:
                raise BootstrapRequired("administrator bootstrap is required")
            connection.execute(
                "UPDATE local_accounts SET password_salt = ?, password_hash = ?, "
                "password_iterations = ?, password_changed_at = ?, disabled_at = NULL "
                "WHERE account_id = ?",
                (salt, password_hash, PBKDF2_ITERATIONS, now, row["account_id"]),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE account_id = ? AND revoked_at IS NULL",
                (now, row["account_id"]),
            )
            self._record_event(
                connection,
                account_id=str(row["account_id"]),
                username=str(row["username"]),
                event_type="recovery",
                outcome="succeeded",
                now=now,
            )
            return LocalAccount(
                str(row["account_id"]),
                str(row["username"]),
                str(row["role"]),
                str(row["created_at"]),
            )

    def login(self, username: str, password: str, *, client_key: str) -> IssuedSession:
        normalized = _normalize_username(username, strict=False)
        now_value = self._clock()
        rate_key = self._rate_key(client_key)
        if self._is_rate_limited(rate_key, now_value):
            raise LoginRateLimited("too many login attempts; try again later")

        account = self._password_record(normalized)
        encoded = _password_bytes_for_login(password)
        if account is None:
            candidate = _password_hash(encoded, _INVALID_SALT, PBKDF2_ITERATIONS)
            _ = hmac.compare_digest(candidate, _INVALID_HASH)
            password_matches = False
        else:
            candidate = _password_hash(encoded, account.salt, account.iterations)
            password_matches = hmac.compare_digest(candidate, account.password_hash)

        if account is None or not password_matches or account.disabled_at is not None:
            self._record_failed_login(normalized, account, rate_key, now_value)
            raise InvalidCredentials("invalid credentials")

        return self._issue_session(account, rate_key, now_value)

    def authenticate(self, session_token: str) -> AuthPrincipal:
        session_id, verifier = _parse_session_token(session_token)
        with self.state.read_connection() as connection:
            row = self._session_row(connection, session_id)
        return self._principal_for_session(row, verifier, self._clock())

    def verify_csrf(self, session_token: str, csrf_token: str) -> AuthPrincipal:
        session_id, verifier = _parse_session_token(session_token)
        with self.state.read_connection() as connection:
            row = self._session_row(connection, session_id)
        principal = self._principal_for_session(row, verifier, self._clock())
        if not csrf_token or len(csrf_token) > 128:
            raise CsrfDenied("CSRF validation failed")
        try:
            actual = _token_hash(csrf_token)
        except UnicodeEncodeError as exc:
            raise CsrfDenied("CSRF validation failed") from exc
        if not hmac.compare_digest(actual, bytes(row["csrf_hash"])):
            raise CsrfDenied("CSRF validation failed")
        return principal

    def logout(self, session_token: str) -> None:
        session_id, verifier = _parse_session_token(session_token)
        now = _timestamp(self._clock())
        with self.state.transaction() as connection:
            row = self._session_row(connection, session_id)
            self._principal_for_session(row, verifier, self._clock())
            connection.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE session_id = ? AND revoked_at IS NULL",
                (now, session_id),
            )
            self._record_event(
                connection,
                account_id=str(row["account_id"]),
                username=str(row["username"]),
                event_type="logout",
                outcome="revoked",
                now=now,
            )

    def _password_record(self, username: str) -> _PasswordRecord | None:
        with self.state.read_connection() as connection:
            row = connection.execute(
                "SELECT account_id, username, role, password_salt, password_hash, "
                "password_iterations, password_changed_at, disabled_at "
                "FROM local_accounts WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return None
        return _PasswordRecord(
            account_id=str(row["account_id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            salt=bytes(row["password_salt"]),
            password_hash=bytes(row["password_hash"]),
            iterations=int(row["password_iterations"]),
            changed_at=str(row["password_changed_at"]),
            disabled_at=(str(row["disabled_at"]) if row["disabled_at"] is not None else None),
        )

    def _issue_session(
        self, account: _PasswordRecord, rate_key: str, now_value: datetime
    ) -> IssuedSession:
        session_id = secrets.token_hex(16)
        verifier = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = _timestamp(now_value)
        expires_at = _timestamp(now_value + SESSION_LIFETIME)
        with self.state.transaction() as connection:
            self._raise_if_rate_limited(connection, rate_key, now_value)
            current = connection.execute(
                "SELECT password_hash, password_changed_at, disabled_at "
                "FROM local_accounts WHERE account_id = ?",
                (account.account_id,),
            ).fetchone()
            if (
                current is None
                or current["disabled_at"] is not None
                or not hmac.compare_digest(bytes(current["password_hash"]), account.password_hash)
                or str(current["password_changed_at"]) != account.changed_at
            ):
                raise InvalidCredentials("invalid credentials")
            connection.execute("DELETE FROM auth_login_limits WHERE key_hash = ?", (rate_key,))
            connection.execute(
                "INSERT INTO auth_sessions "
                "(session_id, account_id, verifier_hash, csrf_hash, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    account.account_id,
                    _token_hash(verifier),
                    _token_hash(csrf_token),
                    now,
                    expires_at,
                ),
            )
            self._record_event(
                connection,
                account_id=account.account_id,
                username=account.username,
                event_type="login",
                outcome="succeeded",
                now=now,
            )
        principal = AuthPrincipal(account.account_id, account.username, account.role, session_id)
        return IssuedSession(
            session_token=f"{session_id}.{verifier}",
            csrf_token=csrf_token,
            expires_at=expires_at,
            principal=principal,
        )

    def _record_failed_login(
        self,
        username: str,
        account: _PasswordRecord | None,
        rate_key: str,
        now_value: datetime,
    ) -> None:
        now = _timestamp(now_value)
        with self.state.transaction() as connection:
            failures = self._current_failures(connection, rate_key, now_value)
            if failures >= self._login_limit:
                return
            else:
                outcome = "rate_limited" if failures + 1 >= self._login_limit else "failed"
                connection.execute(
                    "INSERT INTO auth_login_limits "
                    "(key_hash, window_started_at, failures, updated_at) VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(key_hash) DO UPDATE SET "
                    "failures = auth_login_limits.failures + 1, updated_at = excluded.updated_at",
                    (rate_key, now, now),
                )
            self._record_event(
                connection,
                account_id=account.account_id if account else None,
                username=username,
                event_type="login",
                outcome=outcome,
                now=now,
            )

    def _is_rate_limited(self, rate_key: str, now: datetime) -> bool:
        with self.state.read_connection() as connection:
            return self._current_failures(connection, rate_key, now) >= self._login_limit

    def _raise_if_rate_limited(
        self, connection: sqlite3.Connection, rate_key: str, now: datetime
    ) -> None:
        if self._current_failures(connection, rate_key, now) >= self._login_limit:
            raise LoginRateLimited("too many login attempts; try again later")

    @staticmethod
    def _current_failures(connection: sqlite3.Connection, rate_key: str, now: datetime) -> int:
        row = connection.execute(
            "SELECT window_started_at, failures FROM auth_login_limits WHERE key_hash = ?",
            (rate_key,),
        ).fetchone()
        if row is None:
            return 0
        started = _parse_timestamp(str(row["window_started_at"]))
        if now.astimezone(UTC) - started >= LOGIN_WINDOW:
            connection.execute("DELETE FROM auth_login_limits WHERE key_hash = ?", (rate_key,))
            return 0
        return int(row["failures"])

    @staticmethod
    def _rate_key(client_key: str) -> str:
        if not client_key or len(client_key.encode("utf-8")) > 512:
            client_key = "<invalid-client>"
        return hashlib.sha256(client_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _session_row(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT s.session_id, s.account_id, s.verifier_hash, s.csrf_hash, "
            "s.expires_at, s.revoked_at, a.username, a.role, a.disabled_at "
            "FROM auth_sessions s JOIN local_accounts a ON a.account_id = s.account_id "
            "WHERE s.session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise SessionDenied("session is not valid")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _principal_for_session(row: sqlite3.Row, verifier: str, now: datetime) -> AuthPrincipal:
        if (
            row["revoked_at"] is not None
            or row["disabled_at"] is not None
            or _parse_timestamp(str(row["expires_at"])) <= now.astimezone(UTC)
            or not hmac.compare_digest(_token_hash(verifier), bytes(row["verifier_hash"]))
        ):
            raise SessionDenied("session is not valid")
        return AuthPrincipal(
            account_id=str(row["account_id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            session_id=str(row["session_id"]),
        )

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        *,
        account_id: str | None,
        username: str,
        event_type: str,
        outcome: str,
        now: str,
    ) -> None:
        retention_cutoff = _timestamp(_parse_timestamp(now) - AUTH_EVENT_RETENTION)
        connection.execute("DELETE FROM auth_events WHERE created_at < ?", (retention_cutoff,))
        connection.execute(
            "INSERT INTO auth_events "
            "(account_id, username, event_type, outcome, created_at) VALUES (?, ?, ?, ?, ?)",
            (account_id, username[:64], event_type, outcome, now),
        )
