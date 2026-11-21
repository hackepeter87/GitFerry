"""Real LDAP drift/restart evidence for an already approved directory binding.

This probe assumes the Desktop identity flow has left an approved directory-backed
candidate in the target's package-admission state. LDAP mutations use ``ldapmodify``
with the fixture's mounted password file on stdin/``-y``; no secret is placed in
argv or output.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import support
from support import ProbeFailure

WORKER = Path(__file__).with_name("directory_identity_recovery_worker.py")
SERVICE = os.environ.get("GITFERRY_RECOVERY_SERVICE", "gitferry-b")
LDAP_SERVICE = os.environ.get("GITFERRY_RECOVERY_LDAP_SERVICE", "openldap")
READER_DN = "uid=ldap-reader,ou=People,dc=example,dc=invalid"


def _worker(mode: str, login: str = "ldap-reader") -> dict[str, Any]:
    result = support.docker_service_exec(
        SERVICE,
        ["python", "/probe/directory_identity_recovery_worker.py", mode, "--login", login],
        check=False,
        timeout=180,
    )
    if result.returncode:
        raise ProbeFailure(f"directory identity recovery worker failed: {mode}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ProbeFailure(
            f"directory identity recovery worker returned invalid JSON: {mode}"
        ) from error
    if value.get("status") == "failed":
        raise ProbeFailure(f"directory identity recovery worker reported failure: {mode}")
    return value


def _ldap_modify(ldif: str) -> None:
    # Keep the LDIF on stdin so neither credentials nor directory values are
    # accidentally exposed through process arguments.
    command = [
        *support.COMPOSE,
        "exec",
        "-T",
        LDAP_SERVICE,
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
    )  # noqa: S603
    if result.returncode:
        raise ProbeFailure("synthetic directory modification failed")


def _ldap_values() -> dict[str, Any]:
    prefix = [
        *support.COMPOSE,
        "exec",
        "-T",
        LDAP_SERVICE,
        "ldapsearch",
        "-x",
        "-H",
        "ldap://127.0.0.1:389",
        "-D",
        "cn=admin,dc=example,dc=invalid",
        "-y",
        "/run/slapd/admin-password",
    ]
    command = [
        *prefix,
        "-b",
        "ou=People,dc=example,dc=invalid",
        "(&(uid=ldap-reader))",
        "employeeNumber",
    ]
    reader = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)  # noqa: S603
    if reader.returncode:
        raise ProbeFailure("could not read synthetic LDAP reader facts")
    external = next(
        (
            line.split(": ", 1)[1]
            for line in reader.stdout.splitlines()
            if line.startswith("employeeNumber: ")
        ),
        None,
    )
    if not external:
        raise ProbeFailure("synthetic LDAP reader has no external employeeNumber")
    return {"external_id": external}


def _replace_external(value: str) -> None:
    _ldap_modify(
        f"dn: {READER_DN}\nchangetype: modify\nreplace: employeeNumber\nemployeeNumber: {value}\n\n"
    )


def _wait_directory() -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = support.docker_service_exec(
            LDAP_SERVICE,
            ["ldapwhoami", "-x", "-H", "ldap://127.0.0.1:389"],
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return
        time.sleep(0.5)
    raise ProbeFailure("directory did not become ready after outage")


def _group_change(add: bool) -> None:
    action = "add" if add else "delete"
    _ldap_modify(
        "dn: cn=gitea-users,ou=Groups,dc=example,dc=invalid\n"
        f"changetype: modify\n{action}: member\n"
        "member: uid=ldap-reader,ou=People-B,dc=example,dc=invalid\n\n"
    )


def run() -> dict[str, Any]:
    support.docker_copy(WORKER, f"{SERVICE}:/probe/directory_identity_recovery_worker.py")
    initial = _worker("verify")
    if initial.get("status") != "passed" or initial.get("after_status") != "approved":
        raise ProbeFailure("approved directory-backed binding was not healthy before drift")
    unavailable = _worker("unavailable-ca")
    if unavailable.get("status") != "blocked" or unavailable.get("after_status") != "approved":
        raise ProbeFailure("missing directory CA did not fail closed without changing approval")
    support.run([*support.COMPOSE, "stop", LDAP_SERVICE])
    try:
        outage = _worker("verify")
        if (
            outage.get("status") != "blocked"
            or outage.get("after_status") != "approved"
            or not outage.get("binding_unchanged")
        ):
            raise ProbeFailure("directory outage did not block without changing binding")
    finally:
        support.run([*support.COMPOSE, "start", LDAP_SERVICE])
        _wait_directory()
    recovered = _worker("verify")
    if recovered.get("status") != "passed":
        raise ProbeFailure("directory did not recover after availability returned")
    writer_initial = _worker("verify", "ldap-writer")
    if writer_initial.get("status") != "passed":
        raise ProbeFailure("writer binding was not healthy before group change")
    _group_change(True)
    try:
        group_drift = _worker("verify", "ldap-writer")
        if (
            group_drift.get("status") != "blocked"
            or group_drift.get("after_status") != "conflict"
            or not group_drift.get("changed_groups")
        ):
            raise ProbeFailure("group change did not block the mapped writer")
    finally:
        _group_change(False)
    group_restored = _worker("verify", "ldap-writer")
    if (
        group_restored.get("status") != "blocked"
        or group_restored.get("after_status") != "conflict"
        or not group_restored.get("binding_unchanged")
    ):
        raise ProbeFailure("restoring group membership silently reopened binding")
    facts = _ldap_values()
    drift_value = facts["external_id"] + "-drift"
    try:
        _replace_external(drift_value)
        drift = _worker("verify")
        if (
            drift.get("status") != "blocked"
            or drift.get("after_status") != "conflict"
            or not drift.get("changed_external_id")
        ):
            raise ProbeFailure("external directory drift did not persist a blocked conflict")
        restarted = _worker("verify")
        if restarted.get("status") != "blocked" or restarted.get("after_status") != "conflict":
            raise ProbeFailure("fresh recovery process did not preserve the conflict")
    finally:
        _replace_external(facts["external_id"])
    restored = _worker("verify")
    if restored.get("status") != "blocked" or restored.get("after_status") != "conflict":
        raise ProbeFailure("restoring LDAP facts silently reopened the binding")
    if drift.get("binding_before") != restored.get("binding_after"):
        raise ProbeFailure("directory drift redirected or changed the existing binding")
    return {
        "status": "passed",
        "initial": initial,
        "unavailable_ca": unavailable,
        "drift": drift,
        "fresh_process": restarted,
        "restored": restored,
        "original_external_id": facts["external_id"],
        "mutated_external_id": drift_value,
        "outage": outage,
        "outage_recovered": recovered,
        "writer_initial": writer_initial,
        "group_drift": group_drift,
        "group_restored": group_restored,
        "group_mutated": True,
        "binding_unchanged": True,
        "restored_facts_do_not_reopen": True,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(run(), sort_keys=True))
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(error).__name__, "reason": str(error)}
            )
        )
        raise SystemExit(1) from None
