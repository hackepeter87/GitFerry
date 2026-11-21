"""Bounded HTTPS checks with mounted local secret references and explicit approval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from gitferry.connection_profiles import ProfileConflict, ProfileService, ProfileValidationError

CHECK_DEADLINE_SECONDS = 20.0


class ConnectionUnavailable(RuntimeError):
    """Safe diagnostics deliberately exclude remote error bodies and credential values."""


class LocalReferences:
    def __init__(self, secrets: Path | None = None, cas: Path | None = None):
        self.secrets = secrets or Path(
            os.environ.get("GITFERRY_SECRET_DIR", "/run/secrets/gitferry")
        )
        self.cas = cas or Path(os.environ.get("GITFERRY_CA_DIR", "/run/gitferry/ca"))

    @staticmethod
    def _read(directory: Path, reference: str, *, secret: bool) -> bytes:
        # Recheck reference syntax at the file boundary.
        from gitferry.connection_profiles import _identifier

        _identifier(reference)
        descriptor = None
        directory_fd = None
        try:
            if not directory.is_absolute() or directory.resolve() != directory:
                raise ConnectionUnavailable(
                    "Local reference directory must be absolute without symlinks"
                )
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptor = os.open(
                reference, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
            )
            details = os.fstat(descriptor)
            limit = 4096 if secret else 1024 * 1024
            if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= limit:
                raise ConnectionUnavailable("Local reference must be a bounded regular file")
            if secret and stat.S_IMODE(details.st_mode) & 0o077:
                raise ConnectionUnavailable(
                    "Credential file permissions must exclude group and others"
                )
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                value = stream.read(limit + 1)
            if len(value) > limit:
                raise ConnectionUnavailable("Local reference exceeds its size limit")
            return value
        except OSError:
            raise ConnectionUnavailable(
                "Local reference unavailable; check mounted file and permissions"
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory_fd is not None:
                os.close(directory_fd)

    def token(self, reference: str) -> str:
        try:
            value = self._read(self.secrets, reference, secret=True).decode("ascii").rstrip("\r\n")
        except UnicodeError:
            raise ConnectionUnavailable(
                "Credential file must contain an ASCII access token"
            ) from None
        if not value or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
            raise ConnectionUnavailable("Credential file must contain one access token")
        return value

    def tls(self, reference: str | None) -> tuple[ssl.SSLContext, str]:
        try:
            if reference is None:
                # Honor the deployment's standard trust-store override explicitly:
                # some LibreSSL builds ignore it in load_default_certs(). This is
                # local process configuration, never an imported profile field.
                return ssl.create_default_context(
                    cafile=os.environ.get("SSL_CERT_FILE")
                ), "system-trust"
            value = self._read(self.cas, reference, secret=False)
            return ssl.create_default_context(cadata=value.decode("ascii")), hashlib.sha256(
                value
            ).hexdigest()
        except (UnicodeError, OSError):
            raise ConnectionUnavailable(
                "CA reference must contain a valid PEM certificate bundle"
            ) from None


async def _json(client: httpx.AsyncClient, path: str) -> tuple[dict[str, Any], str | None]:
    async with client.stream("GET", path) as response:
        if response.status_code != 200:
            raise ConnectionUnavailable("Gitea connection rejected; check endpoint, token and TLS")
        stream = response.extensions.get("network_stream")
        tls = stream.get_extra_info("ssl_object") if stream else None
        certificate = tls.getpeercert(True) if tls else None
        fingerprint = hashlib.sha256(certificate).hexdigest() if certificate else None
        body = bytearray()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ConnectionUnavailable("Gitea check requires an uncompressed response")
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > 65536:
                raise ConnectionUnavailable("Gitea check response exceeds 64 KiB")
            body.extend(chunk)
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        raise ConnectionUnavailable("Gitea check returned invalid JSON") from None
    if not isinstance(value, dict):
        raise ConnectionUnavailable("Gitea check returned an unexpected object")
    return value, fingerprint


async def _remote_check(
    base_url: str, token: str, context: ssl.SSLContext, transport: httpx.AsyncBaseTransport | None
) -> tuple[dict[str, Any], str | None, dict[str, Any], str | None]:
    async def read() -> tuple[dict[str, Any], str | None, dict[str, Any], str | None]:
        async with httpx.AsyncClient(
            base_url=base_url + "/api/v1/",
            headers={"Authorization": "token " + token, "Accept-Encoding": "identity"},
            verify=context,
            follow_redirects=False,
            trust_env=False,
            timeout=10,
            transport=transport,
        ) as client:
            version, fingerprint = await _json(client, "version")
            account, account_fingerprint = await _json(client, "user")
            return version, fingerprint, account, account_fingerprint

    # Cancellation closes both the response and client. Unlike per-read timeouts,
    # this deadline also bounds a peer which continuously drips response bytes.
    return await asyncio.wait_for(read(), timeout=CHECK_DEADLINE_SECONDS)


def probe_connection(
    service: ProfileService,
    key: str,
    *,
    expected_revision: int,
    references: LocalReferences | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    profile = service.get(key)
    service._check_revision(profile, expected_revision)
    refs = references or LocalReferences()
    try:
        token = refs.token(profile.credential_ref)
        context, ca_digest = refs.tls(profile.ca_ref)
        version_data, fingerprint, account, account_fingerprint = asyncio.run(
            _remote_check(profile.base_url, token, context, transport)
        )
        version = version_data.get("version")
        if fingerprint != account_fingerprint or (fingerprint is None and transport is None):
            raise ConnectionUnavailable("TLS peer identity could not be established consistently")
        if (
            not isinstance(version, str)
            or not 1 <= len(version) <= 64
            or type(account.get("id")) is not int
            or account["id"] < 1
            or not isinstance(account.get("login"), str)
            or not 1 <= len(account["login"]) <= 100
            or token in version
            or token in account["login"]
        ):
            raise ConnectionUnavailable(
                "Gitea check response has invalid version or account identity"
            )
    except (httpx.HTTPError, asyncio.TimeoutError, ConnectionUnavailable):
        with service.store.transaction() as connection:
            connection.execute(
                "UPDATE connection_profiles SET locally_approved=0 "
                "WHERE instance_key=? AND revision=?",
                (key, expected_revision),
            )
            connection.execute(
                "UPDATE connection_checks SET observed_identity='' "
                "WHERE instance_key=? AND profile_revision=?",
                (key, expected_revision),
            )
        raise ConnectionUnavailable(
            "Connection check failed; verify HTTPS, local token and CA references"
        ) from None
    identity = {
        "account_id": str(account["id"]),
        "login": account["login"],
        "ca_digest": ca_digest,
        "certificate_sha256": fingerprint,
    }
    observed = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(observed.encode()).hexdigest()
    with service.store.transaction() as connection:
        current = service._get(connection, key)
        service._check_revision(current, expected_revision)
        old = connection.execute(
            "SELECT * FROM connection_checks WHERE instance_key=?", (key,)
        ).fetchone()
        approved = old["approved_identity"] if old else None
        if approved != observed:
            connection.execute(
                "UPDATE connection_profiles SET locally_approved=0 WHERE instance_key=?", (key,)
            )
        connection.execute(
            "INSERT INTO connection_checks VALUES (?,?,?,?,?) "
            "ON CONFLICT(instance_key) DO UPDATE SET profile_revision=excluded.profile_revision,"
            "observed_identity=excluded.observed_identity,approved_identity=excluded.approved_identity,"
            "checked_at=excluded.checked_at",
            (key, expected_revision, observed, approved, datetime.now(timezone.utc).isoformat()),
        )
    approved_identity = json.loads(approved) if approved else None
    return {
        "version": version,
        "account_id": str(account["id"]),
        "login": account["login"],
        "evidence_digest": digest,
        "observed_identity": identity,
        "approved_identity": approved_identity,
        "changed_fields": [
            field
            for field, value in identity.items()
            if approved_identity is None or approved_identity.get(field) != value
        ],
        "requires_approval": not current.locally_approved or approved != observed,
        "checked_capabilities": ["version-read", "authenticated-account-read"],
        "installation_identity": "operator-registered; not remotely attested",
    }


def approve_connection(
    service: ProfileService, key: str, *, expected_revision: int, evidence_digest: str
) -> None:
    if not isinstance(evidence_digest, str) or len(evidence_digest) != 64:
        raise ProfileValidationError("Invalid connection evidence digest")
    with service.store.transaction() as connection:
        profile = service._get(connection, key)
        service._check_revision(profile, expected_revision)
        check = connection.execute(
            "SELECT * FROM connection_checks WHERE instance_key=?", (key,)
        ).fetchone()
        if (
            check is None
            or not check["observed_identity"]
            or check["profile_revision"] != expected_revision
            or hashlib.sha256(check["observed_identity"].encode()).hexdigest() != evidence_digest
        ):
            raise ProfileConflict("Test the current profile and review its current evidence first")
        connection.execute(
            "UPDATE connection_checks SET approved_identity=observed_identity WHERE instance_key=?",
            (key,),
        )
        connection.execute(
            "UPDATE connection_profiles SET locally_approved=1 WHERE instance_key=?", (key,)
        )
        service._event(connection, profile, "approve")
