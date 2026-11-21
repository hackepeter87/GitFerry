"""Pure contract for target-local, LDAP-simple Gitea bootstrap.

This module deliberately prepares an argv/env contract only.  A caller with
authority over the target Gitea installation must execute it and perform the
version/read-back checks.  Bind-DN passwords are not supported here because
Gitea 1.27.2 only accepts them as a CLI value; operators must use Gitea's
authenticated admin UI for that mode.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class DirectoryBootstrapError(ValueError):
    """The blueprint cannot be safely prepared for local execution."""


class DirectoryBootstrapUnsupported(DirectoryBootstrapError):
    """The requested mode requires target-local Gitea admin UI handling."""


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_SECRET_MARKERS = ("password", "token", "secret", "private_key", "credential")


@dataclass(frozen=True)
class DirectoryBootstrapIntent:
    name: str
    directory_realm: str
    host: str
    port: int
    security_protocol: Literal["ldaps", "starttls"]
    user_dn: str
    user_filter: str
    email_attribute: str
    username_attribute: str | None
    ca_secret_ref: str
    expected_gitea_version: str


@dataclass(frozen=True)
class DirectoryBootstrapPreflight:
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    config_path: Path
    ca_secret_ref: str
    intent_digest: str
    expected_gitea_version: str


def _reject_secret_like(value: Mapping[str, Any]) -> None:
    for key in value:
        if any(marker in str(key).lower() for marker in _SECRET_MARKERS):
            if key != "caSecretRef":
                raise DirectoryBootstrapError(f"secret-bearing field is not allowed: {key}")


def _intent(source: Mapping[str, Any], expected_version: str) -> DirectoryBootstrapIntent:
    if source.get("mode") != "simple":
        raise DirectoryBootstrapUnsupported(
            "LDAP BindDN bootstrap is not automated; add the source in Gitea's authenticated "
            "admin UI so the bind password is submitted over HTTPS and encrypted by Gitea"
        )
    _reject_secret_like(source)
    source_type = source.get("type")
    protocol = source.get("securityProtocol")
    if source_type == "ldaps":
        protocol = protocol or "ldaps"
    elif source_type == "ldap" and protocol != "starttls":
        raise DirectoryBootstrapError("LDAP simple bootstrap requires securityProtocol=starttls")
    if protocol not in ("ldaps", "starttls"):
        raise DirectoryBootstrapError("unsupported LDAP security protocol")
    ca_ref = source.get("caSecretRef")
    if not isinstance(ca_ref, str) or not _SAFE_REF.fullmatch(ca_ref):
        raise DirectoryBootstrapError("simple LDAP bootstrap requires a safe caSecretRef")
    required = ("name", "directoryRealm", "host", "userDn", "userFilter", "emailAttribute")
    if any(
        not isinstance(source.get(key), str) or not str(source[key]).strip() for key in required
    ):
        raise DirectoryBootstrapError("simple LDAP bootstrap has missing required fields")
    name = str(source["name"])
    if not _SAFE_NAME.fullmatch(name):
        raise DirectoryBootstrapError("authentication source name contains unsafe characters")
    port = source.get("port", 636 if protocol == "ldaps" else 389)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise DirectoryBootstrapError("LDAP port is outside the valid range")
    username = source.get("usernameAttribute")
    if username is not None and (not isinstance(username, str) or not username.strip()):
        raise DirectoryBootstrapError("usernameAttribute must be a non-empty string")
    if not isinstance(expected_version, str) or not expected_version.strip():
        raise DirectoryBootstrapError("expected Gitea version is required")
    return DirectoryBootstrapIntent(
        name=name,
        directory_realm=str(source["directoryRealm"]),
        host=str(source["host"]),
        port=port,
        security_protocol=protocol,
        user_dn=str(source["userDn"]),
        user_filter=str(source["userFilter"]),
        email_attribute=str(source["emailAttribute"]),
        username_attribute=username,
        ca_secret_ref=ca_ref,
        expected_gitea_version=expected_version,
    )


def prepare_ldap_simple_bootstrap(
    source: Mapping[str, Any],
    *,
    gitea_binary: Path,
    gitea_config: Path,
    ca_path: Path,
    expected_version: str = "1.27.2",
) -> DirectoryBootstrapPreflight:
    """Prepare a secret-free ``gitea admin auth add-ldap-simple`` invocation."""

    if (
        not gitea_binary.is_absolute()
        or not gitea_config.is_absolute()
        or not ca_path.is_absolute()
    ):
        raise DirectoryBootstrapError("target-local binary, config, and CA paths must be absolute")
    if gitea_binary.is_symlink() or gitea_config.is_symlink() or ca_path.is_symlink():
        raise DirectoryBootstrapError("target-local paths must not be symlinks")
    intent = _intent(source, expected_version)
    canonical = {
        "name": intent.name,
        "directoryRealm": intent.directory_realm,
        "host": intent.host,
        "port": intent.port,
        "securityProtocol": intent.security_protocol,
        "userDn": intent.user_dn,
        "userFilter": intent.user_filter,
        "emailAttribute": intent.email_attribute,
        "usernameAttribute": intent.username_attribute,
        "caSecretRef": intent.ca_secret_ref,
        "expectedGiteaVersion": intent.expected_gitea_version,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    command = [
        os.fspath(gitea_binary),
        "--config",
        os.fspath(gitea_config),
        "admin",
        "auth",
        "add-ldap-simple",
        "--name",
        intent.name,
        "--security-protocol",
        intent.security_protocol,
        "--host",
        intent.host,
        "--port",
        str(intent.port),
        "--user-dn",
        intent.user_dn,
        "--user-filter",
        intent.user_filter,
        "--email-attribute",
        intent.email_attribute,
    ]
    if intent.username_attribute is not None:
        command.extend(("--username-attribute", intent.username_attribute))
    return DirectoryBootstrapPreflight(
        command=tuple(command),
        environment=(("SSL_CERT_FILE", os.fspath(ca_path)),),
        config_path=gitea_config,
        ca_secret_ref=intent.ca_secret_ref,
        intent_digest=digest,
        expected_gitea_version=intent.expected_gitea_version,
    )


__all__ = [
    "DirectoryBootstrapError",
    "DirectoryBootstrapPreflight",
    "DirectoryBootstrapUnsupported",
    "DirectoryBootstrapIntent",
    "prepare_ldap_simple_bootstrap",
]
