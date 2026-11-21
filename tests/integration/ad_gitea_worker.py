"""Bounded Samba-AD to Gitea LDAP-simple authentication proof.

The common integration runner mounts the AD CA into the target's existing CA
volume before invoking this worker. This fixture creates an authentication source
and explicit local alias through
documented interfaces; it never writes a Gitea database.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from gitferry.directory_bootstrap import prepare_ldap_simple_bootstrap
from support import ADMIN, COMPOSE, TARGET_URL, ProbeFailure, run


def _request(
    user: str, password: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(  # noqa: S310
        f"{TARGET_URL}/api/v1{path}",
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


def run_ad_gitea_probe() -> dict[str, Any]:
    cli = [
        *COMPOSE,
        "exec",
        "-T",
        "--user",
        "git",
        "target",
        "gitea",
        "--config",
        "/data/gitea/conf/app.ini",
    ]
    source = {
        "name": "synthetic-ad-simple",
        "type": "ldaps",
        "mode": "simple",
        "directoryRealm": "SYNTH-AD",
        "host": "synthdc",
        "port": 636,
        "userDn": "CN=%s,OU=People,DC=synth,DC=ad",
        "userFilter": "(&(sAMAccountName=%s)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
        "usernameAttribute": "sAMAccountName",
        "emailAttribute": "mail",
        "caSecretRef": "ad-ca",
    }
    intent = prepare_ldap_simple_bootstrap(
        source,
        gitea_binary=Path("/usr/local/bin/gitea"),
        gitea_config=Path("/data/gitea/conf/app.ini"),
        ca_path=Path("/run/directory-ca/ca.pem"),
    )
    result = run([*cli, *intent.command[3:]], check=False)
    if result.returncode:
        raise ProbeFailure("Gitea AD LDAP-simple bootstrap failed")
    listing = run([*cli, "admin", "auth", "list"]).stdout
    source_id = next(
        (int(line.split()[0]) for line in listing.splitlines() if "synthetic-ad-simple" in line),
        None,
    )
    if source_id is None:
        raise ProbeFailure("Gitea did not list the Samba AD LDAP source")
    checks = {
        "ldap_same": _request("ldap-same", "Synthetic-User-42!", "/user"),
        "ad_nomail": _request("ad-nomail", "Synthetic-User-43!", "/user"),
        "ad_disabled": _request("ad-disabled", "Synthetic-User-44!", "/user"),
        "wrong_password": _request("ldap-same", "wrong-synthetic", "/user"),
    }
    if checks["ldap_same"][0] != 200 or checks["ad_nomail"][0] != 200:
        raise ProbeFailure("Gitea rejected valid Samba AD LDAP-simple login")
    if checks["ad_disabled"][0] not in (401, 403) or checks["wrong_password"][0] != 401:
        raise ProbeFailure("Gitea accepted disabled or wrong-password AD login")
    alias_status, alias = _request(
        ADMIN[0],
        ADMIN[1],
        "/admin/users",
        {
            "username": "ad-local-alias",
            "source_id": source_id,
            "login_name": "ldap-same",
            "email": "alias@example.invalid",
            "must_change_password": False,
        },
    )
    alias_login = _request("ad-local-alias", "Synthetic-User-42!", "/user")
    if alias_status != 201 or alias_login[0] != 200:
        raise ProbeFailure("Gitea native alias did not authenticate through Samba AD")
    accounts = (checks["ldap_same"][1], checks["ad_nomail"][1], alias_login[1])
    if any(
        account.get("source_id") != source_id or account.get("is_admin") is not False
        for account in accounts
    ):
        raise ProbeFailure("Samba account source or administrative status differs")
    if (
        alias.get("id") != alias_login[1].get("id")
        or alias.get("id") == checks["ldap_same"][1].get("id")
        or alias_login[1].get("login_name") != "ldap-same"
        or alias_login[1].get("login") != "ad-local-alias"
    ):
        raise ProbeFailure("native alias did not preserve the explicit external identity")
    return {
        "status": "passed",
        "authentication_source": {"id": source_id, "type": "LDAP (simple auth)"},
        "checks": {
            key: {
                "status": status,
                "account": {
                    field: account.get(field)
                    for field in ("id", "login", "source_id", "login_name", "is_admin", "email")
                },
            }
            for key, (status, account) in checks.items()
        },
        "assertions": dict.fromkeys(
            (
                "valid_login",
                "missing_directory_mail_login",
                "disabled_denied",
                "wrong_password_denied",
                "non_admin",
                "explicit_source",
                "native_alias_distinct",
            ),
            True,
        ),
        "alias": {
            "create_status": alias_status,
            "login_status": alias_login[0],
            "native_id": alias["id"],
            "source_id": source_id,
            "login_name": alias_login[1]["login_name"],
            "is_admin": alias_login[1]["is_admin"],
        },
    }
