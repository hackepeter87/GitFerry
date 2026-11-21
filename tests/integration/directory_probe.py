"""Bounded LDAP-simple probe for the pinned Gitea integration target.

The directory service is supplied by a separately attached fixture service.  This
probe never writes a directory password to argv, stdout, reports, or source.
Set GITFERRY_IT_DIRECTORY_HOST to the fixture DNS name (default ``openldap``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import support
from gitferry.directory_bootstrap import prepare_ldap_simple_bootstrap
from support import TARGET_URL, ProbeFailure, api

DIRECTORY_COMPOSE = (
    [
        "docker",
        "compose",
        "-p",
        os.environ.get("GITFERRY_IT_PROJECT", "gitferry-it-directory-local"),
        "-f",
        os.environ.get(
            "GITFERRY_DIRECTORY_COMPOSE_FILE", "tests/integration/directory/compose.yaml"
        ),
    ]
    if os.environ.get("GITFERRY_DIRECTORY_COMPOSE_FILE")
    else support.COMPOSE
)

SOURCE_NAME = "synthetic-openldap-simple"
DIRECTORY_HOST = os.environ.get("GITFERRY_IT_DIRECTORY_HOST", "openldap")
DIRECTORY_PORT = int(os.environ.get("GITFERRY_IT_DIRECTORY_PORT", "636"))
DIRECTORY_CA = os.environ.get("GITFERRY_IT_DIRECTORY_CA", "/run/directory-ca/ca.pem")
DIRECTORY_USER = os.environ.get("GITFERRY_IT_DIRECTORY_USER", "ldap-reader")
DIRECTORY_PASSWORD = os.environ.get("GITFERRY_IT_DIRECTORY_PASSWORD", "synthetic-directory-pass")
DIRECTORY_EMAIL = os.environ.get("GITFERRY_IT_DIRECTORY_EMAIL", "ldap-reader@example.invalid")


def _cli(*args: str) -> str:
    result = support.run(
        [
            *DIRECTORY_COMPOSE,
            "exec",
            "-T",
            "--user",
            "git",
            "target",
            "gitea",
            "--config",
            "/data/gitea/conf/app.ini",
            *args,
        ]
    )
    return result.stdout


def _source_id(listing: str) -> int:
    for line in listing.splitlines():
        if len(line.split()) > 1 and line.split()[1] == SOURCE_NAME:
            match = re.match(r"\s*(\d+)\s+", line)
            if match:
                return int(match.group(1))
    raise ProbeFailure("LDAP-simple source was not listed by Gitea")


def _application_bootstrap() -> dict[str, Any]:
    """Qualify the actual local adapter with the pinned binary and server volume.

    This dedicated fixture has explicit server access. Neither file importer gains
    access to the Gitea data volume or to the opposite GitFerry state.
    """
    prefix = [*DIRECTORY_COMPOSE, "exec", "-T", "directory-bootstrap"]
    binary = support.WORK / "gitea-bootstrap-binary"
    support.docker_copy("target:/app/gitea/gitea", binary)
    with binary.open("rb") as stream:
        binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        support.docker_copy(binary, "directory-bootstrap:/usr/local/bin/gitea")
    finally:
        binary.unlink()
    support.run([*prefix, "useradd", "--uid", "1000", "--create-home", "git"])
    support.run([*prefix, "chown", "1000:1000", "/state"])

    def worker(mode: str, expected_exit: int = 0) -> dict[str, Any]:
        result = support.run(
            [
                *DIRECTORY_COMPOSE,
                "exec",
                "-T",
                "--user",
                "1000:1000",
                "directory-bootstrap",
                "python",
                "/probe/directory_bootstrap_worker.py",
                mode,
            ],
            check=False,
            timeout=120,
        )
        if result.returncode != expected_exit:
            try:
                evidence = json.loads(result.stderr)
            except ValueError:
                evidence = {"mode": mode, "exit_code": result.returncode}
            (support.WORK / "directory-bootstrap-failure.json").write_text(json.dumps(evidence))
            raise ProbeFailure("target-local directory bootstrap worker failed: " + mode)
        if expected_exit:
            return {"exit_code": result.returncode}
        return json.loads(result.stdout)

    first = worker("apply")
    replay = worker("replay")
    observed = worker("status")
    changed = worker("changed-intent")
    crash = worker("crash", 73)
    recovered_crash = worker("unknown-replay-crash")
    lost = worker("lost-response")
    recovered_lost = worker("unknown-replay-lost-response")
    if any(
        result["status"] != "passed"
        for result in (first, replay, observed, changed, recovered_crash, lost, recovered_lost)
    ):
        raise ProbeFailure("local bootstrap did not complete its mandatory assertions")
    if first["source_count"] != replay["source_count"] or first["events"] != replay["events"]:
        raise ProbeFailure("confirmed local bootstrap replay changed state")
    if recovered_crash["source_count"] != 2 or recovered_lost["source_count"] != 3:
        raise ProbeFailure("uncertain bootstrap replay duplicated a native source")
    return {
        "first": first,
        "replay": replay,
        "observed": observed,
        "changed": changed,
        "crash": crash,
        "recovered_crash": recovered_crash,
        "lost_response": lost,
        "recovered_lost_response": recovered_lost,
        "binary_sha256": binary_digest,
        "execution_context": "isolated target-local CLI with explicit server volume access",
    }


def _ldap_request(path: str, password: str, username: str = DIRECTORY_USER) -> httpx.Response:
    return httpx.get(f"{TARGET_URL}/api/v1{path}", auth=(username, password), timeout=15)


def _ldap_modify(ldif: str) -> None:
    command = [
        *DIRECTORY_COMPOSE,
        "exec",
        "-T",
        "openldap",
        "ldapmodify",
        "-x",
        "-H",
        "ldap://127.0.0.1:389",
        "-D",
        "cn=admin,dc=example,dc=invalid",
        "-y",
        "/run/slapd/admin-password",
    ]
    result = subprocess.run(
        command, input=ldif, text=True, capture_output=True, timeout=30, check=False
    )
    if result.returncode:
        raise ProbeFailure("synthetic directory modification failed")


def _replace_attribute(dn: str, attribute: str, value: str) -> None:
    _ldap_modify(f"dn: {dn}\nchangetype: modify\nreplace: {attribute}\n{attribute}: {value}\n\n")


def _add_group_member(dn: str, member: str) -> None:
    _ldap_modify(f"dn: {dn}\nchangetype: modify\nadd: member\nmember: {member}\n\n")


def _delete_group_member(dn: str, member: str) -> None:
    _ldap_modify(f"dn: {dn}\nchangetype: modify\ndelete: member\nmember: {member}\n\n")


def _gitea_version() -> str:
    response = httpx.get(f"{TARGET_URL}/api/v1/version", timeout=15)
    if response.status_code >= 400:
        raise ProbeFailure(f"Gitea version endpoint returned HTTP {response.status_code}")
    return str(response.json().get("version", ""))


def _directory_facts() -> dict[str, Any]:
    result = support.run(
        [
            *DIRECTORY_COMPOSE,
            "exec",
            "-T",
            "openldap",
            "ldapsearch",
            "-x",
            "-H",
            "ldap://127.0.0.1:389",
            "-b",
            "dc=example,dc=invalid",
            "(&(uid=ldap-reader))",
            "dn",
            "employeeNumber",
        ]
    )
    lines = result.stdout.splitlines()
    groups = support.run(
        [
            *DIRECTORY_COMPOSE,
            "exec",
            "-T",
            "openldap",
            "ldapsearch",
            "-x",
            "-H",
            "ldap://127.0.0.1:389",
            "-b",
            "ou=Groups,dc=example,dc=invalid",
            "(cn=gitea-users)",
            "dn",
            "member",
        ]
    ).stdout.splitlines()
    return {
        "ldap_reader_dns": [line[4:] for line in lines if line.startswith("dn: ")],
        "external_ids": [line[16:] for line in lines if line.startswith("employeeNumber: ")],
        "duplicate_uid_scopes": len([line for line in lines if line.startswith("dn: ")]) == 2,
        "group_dns": [line[4:] for line in groups if line.startswith("dn: ")],
        "group_members": [line[8:] for line in groups if line.startswith("member: ")],
    }


def _restart_target() -> None:
    support.run([*DIRECTORY_COMPOSE, "restart", "target"], timeout=60)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{TARGET_URL}/api/v1/version", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise ProbeFailure("Gitea did not become healthy after directory trust restart")


def _wrong_ca_probe() -> bool:
    # Replace only this run's synthetic CA, restarting Gitea to clear Go's trust cache.
    prefix = [*DIRECTORY_COMPOSE, "exec", "-T", "openldap"]
    support.run([*prefix, "cp", "/run/directory-ca/ca.pem", "/run/directory-ca/ca.good"])
    try:
        support.run(
            [
                *prefix,
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=untrusted-synthetic-ca",
                "-keyout",
                "/run/directory-ca/wrong.key",
                "-out",
                "/run/directory-ca/ca.pem",
            ]
        )
        _restart_target()
        denied = _ldap_request("/user", DIRECTORY_PASSWORD).status_code
        if denied not in (401, 403):
            raise ProbeFailure("Gitea accepted LDAP authentication with the wrong CA")
    finally:
        support.run([*prefix, "cp", "/run/directory-ca/ca.good", "/run/directory-ca/ca.pem"])
        _restart_target()
    if _ldap_request("/user", DIRECTORY_PASSWORD).status_code != 200:
        raise ProbeFailure("LDAP login did not recover after restoring the trusted CA")
    return True


def run_directory_probe() -> dict[str, Any]:
    # Simple auth uses the user DN template and supplies the user password only
    # to the HTTP client. It has no bind password and no secret CLI argument.
    source = {
        "name": SOURCE_NAME,
        "type": "ldaps",
        "mode": "simple",
        "directoryRealm": "SYNTHETIC-OPENLDAP",
        "host": DIRECTORY_HOST,
        "port": DIRECTORY_PORT,
        "userDn": "uid=%s,ou=People,dc=example,dc=invalid",
        "userFilter": "(&(objectClass=posixAccount)(uid=%s)(!(employeeNumber=DISABLED)))",
        "emailAttribute": "mail",
        "usernameAttribute": "uid",
        "caSecretRef": "fixture/directory-ca",
    }
    preflight = prepare_ldap_simple_bootstrap(
        source,
        gitea_binary=Path("/usr/local/bin/gitea"),
        gitea_config=Path("/data/gitea/conf/app.ini"),
        ca_path=Path(DIRECTORY_CA),
    )
    bootstrap = (
        _application_bootstrap() if not os.environ.get("GITFERRY_DIRECTORY_COMPOSE_FILE") else None
    )
    listing = _cli("admin", "auth", "list")
    source_created = SOURCE_NAME in listing
    if not source_created:
        _cli(*preflight.command[3:])
        listing = _cli("admin", "auth", "list")
    source_id = _source_id(listing)

    # A local account with a colliding login must remain authoritative; LDAP
    # credentials must not take over that account.
    _cli(
        "admin",
        "user",
        "create",
        "--username",
        "collision-user",
        "--password",
        "synthetic-local-pass",
        "--email",
        "collision-user-local@example.invalid",
        "--must-change-password=false",
    )

    valid = _ldap_request("/user", DIRECTORY_PASSWORD)
    wrong = _ldap_request("/user", f"{DIRECTORY_PASSWORD}-wrong")
    if valid.status_code >= 400:
        raise ProbeFailure(f"LDAP user authentication failed with HTTP {valid.status_code}")
    if wrong.status_code != 401:
        raise ProbeFailure("wrong LDAP password was not rejected with HTTP 401")
    account = valid.json()
    if account.get("login") != DIRECTORY_USER or account.get("email") != DIRECTORY_EMAIL:
        raise ProbeFailure("LDAP account attributes did not match the synthetic fixture")
    account_field_availability = {
        field: field in account
        for field in (
            "id",
            "login",
            "email",
            "source_id",
            "login_name",
            "is_active",
            "prohibit_login",
        )
    }
    missing_mail = _ldap_request("/user", DIRECTORY_PASSWORD, "missing-mail")
    if missing_mail.status_code >= 400:
        raise ProbeFailure("LDAP user without mail could not authenticate")
    missing_mail_account = httpx.get(
        f"{TARGET_URL}/api/v1/users/missing-mail",
        auth=("gitferry-admin", os.environ["GITFERRY_IT_ADMIN_PASSWORD"]),
        timeout=15,
    )
    collision = _ldap_request("/user", DIRECTORY_PASSWORD, "collision-user")
    collision_rejected = collision.status_code == 401
    if not collision_rejected:
        raise ProbeFailure("LDAP credentials took over a same-name local account")
    directory_facts = _directory_facts()
    if (
        not directory_facts["duplicate_uid_scopes"]
        or len(set(directory_facts["external_ids"])) != 2
    ):
        raise ProbeFailure("fixture did not expose distinct external IDs across equal-login scopes")
    if not directory_facts["group_dns"] or not directory_facts["group_members"]:
        raise ProbeFailure("directory group relationship was not observed")

    reader_dn = "uid=ldap-reader,ou=People,dc=example,dc=invalid"
    group_dn = "cn=gitea-users,ou=Groups,dc=example,dc=invalid"
    disabled_value = "DISABLED"
    _replace_attribute(reader_dn, "employeeNumber", disabled_value)
    disabled_login = _ldap_request("/user", DIRECTORY_PASSWORD).status_code
    _replace_attribute(reader_dn, "employeeNumber", "SYNTHETIC-EXT-10001")
    restored_filter_login = _ldap_request("/user", DIRECTORY_PASSWORD).status_code
    if disabled_login not in (401, 403) or restored_filter_login != 200:
        raise ProbeFailure(
            "LDAP user filter did not exclude and restore the disabled synthetic account"
        )

    changed_external_id = "SYNTHETIC-EXT-19999"
    _replace_attribute(reader_dn, "employeeNumber", changed_external_id)
    changed_facts = _directory_facts()
    _replace_attribute(reader_dn, "employeeNumber", "SYNTHETIC-EXT-10001")
    restored_facts = _directory_facts()
    if (
        changed_external_id not in changed_facts["external_ids"]
        or directory_facts != restored_facts
    ):
        raise ProbeFailure("LDAP employeeNumber change was not observed and restored exactly")

    added_member = "uid=missing-mail,ou=People,dc=example,dc=invalid"
    _add_group_member(group_dn, added_member)
    changed_group_facts = _directory_facts()
    _delete_group_member(group_dn, added_member)
    restored_group_facts = _directory_facts()
    if (
        added_member not in changed_group_facts["group_members"]
        or directory_facts != restored_group_facts
    ):
        raise ProbeFailure("LDAP group membership change was not observed and restored exactly")

    support.run([*DIRECTORY_COMPOSE, "stop", "openldap"], timeout=30)
    outage_login = _ldap_request("/user", DIRECTORY_PASSWORD).status_code
    support.run([*DIRECTORY_COMPOSE, "start", "openldap"], timeout=30)
    deadline = time.monotonic() + 60
    restored_login = 0
    while time.monotonic() < deadline:
        restored_login = _ldap_request("/user", DIRECTORY_PASSWORD).status_code
        if restored_login == 200:
            break
        time.sleep(1)
    if outage_login not in (401, 500, 502, 503) or restored_login != 200:
        raise ProbeFailure("LDAP outage did not fail closed and recover login")

    # A normal LDAP user is not an administrator. The endpoint is deliberately
    # checked without exposing the response body in the report.
    admin_probe = _ldap_request("/admin/users", DIRECTORY_PASSWORD)
    if admin_probe.status_code not in (401, 403):
        raise ProbeFailure("LDAP user unexpectedly reached an administrator endpoint")

    # Readback must identify the same native LDAP-backed account, not another identity.
    same_login = api(TARGET_URL, "GET", f"/users/{DIRECTORY_USER}")
    if same_login.get("login") != DIRECTORY_USER or same_login.get("id") != account["id"]:
        raise ProbeFailure("authenticated LDAP account was not readable by the administrator")
    wrong_ca_rejected = _wrong_ca_probe()
    version = _gitea_version()
    if version != "1.27.2":
        raise ProbeFailure("unexpected Gitea version in directory fixture")
    package_versions = support.run(
        [
            *DIRECTORY_COMPOSE,
            "exec",
            "-T",
            "openldap",
            "dpkg-query",
            "-W",
            "-f=${Package}=${Version}\n",
            "slapd",
            "ldap-utils",
        ]
    ).stdout
    expected_packages = {"slapd=2.6.10+dfsg-1", "ldap-utils=2.6.10+dfsg-1"}
    if set(package_versions.splitlines()) != expected_packages:
        raise ProbeFailure("directory fixture package versions differ from pinned contract")
    return {
        "status": "passed",
        "local_application_bootstrap": bootstrap,
        "gitea_version": version,
        "fixture": {
            "base_image": (
                "python:3.13.9-slim@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86"
            ),
            "slapd_package": "2.6.10+dfsg-1",
            "ldap_utils_package": "2.6.10+dfsg-1",
        },
        "authentication_source": {
            "id": source_id,
            "name": SOURCE_NAME,
            "type": "LDAP (simple auth)",
            "security_protocol": "ldaps",
            "bind_password_in_command": False,
            "intent_digest": preflight.intent_digest,
            "source_already_existed": source_created,
        },
        "valid_password_login": True,
        "authenticated_user_field_availability": account_field_availability,
        "wrong_password_rejected": True,
        "wrong_ca_rejected_and_restored": wrong_ca_rejected,
        "disabled_filter_excluded_and_restored": disabled_login in (401, 403)
        and restored_filter_login == 200,
        "external_employee_number_changed_and_restored": changed_external_id
        in changed_facts["external_ids"]
        and directory_facts == restored_facts,
        "group_membership_changed_and_restored": added_member
        in changed_group_facts["group_members"]
        and directory_facts == restored_group_facts,
        "ldap_outage_failed_closed_and_restored": outage_login in (401, 500, 502, 503)
        and restored_login == 200,
        "ldap_user_not_administrator": True,
        "missing_mail_native_email": (
            missing_mail_account.json().get("email")
            if missing_mail_account.status_code == 200
            else None
        ),
        "same_login_collision_ldap_rejected": collision_rejected,
        "directory_facts": directory_facts,
        "ad_sid_compatibility": "not-tested",
        "nested_group_behavior": "not-tested",
        "authenticated_account_readback": True,
        "ca_reference": "fixture/directory-ca",
    }


if __name__ == "__main__":
    print(json.dumps(run_directory_probe(), sort_keys=True))
