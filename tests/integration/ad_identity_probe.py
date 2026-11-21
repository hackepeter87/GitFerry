"""AD-specific drift and restart proof using the target-local application core."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import support
from support import ProbeFailure

ROOT = Path(__file__).parent


def worker(mode: str) -> dict[str, Any]:
    result = support.docker_service_exec(
        "gitferry-b", ["python", "/probe/ad_identity_worker.py", mode], check=False, timeout=120
    )
    if result.returncode:
        raise ProbeFailure("AD application worker failed: " + mode + ": " + result.stdout.strip())
    value = json.loads(result.stdout)
    if value.get("status") == "failed":
        raise ProbeFailure("AD application worker reported failure: " + mode)
    return value


def mutate(mode: str) -> dict[str, Any]:
    result = support.docker_service_exec(
        "ad", ["python3", "/tmp/ad-mutate.py", mode], check=False, timeout=45
    )
    if result.returncode:
        raise ProbeFailure("AD fixture mutation failed: " + mode)
    return json.loads(result.stdout)


def ready() -> None:
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        container = support.run([*support.COMPOSE, "ps", "-q", "ad"]).stdout.strip()
        result = support.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container]
        )
        if result.stdout.strip() == "healthy":
            return
        time.sleep(0.5)
    raise ProbeFailure("AD fixture did not become healthy after outage")


def writes() -> str:
    return support.docker_service_exec(
        "gitferry-b", ["wc", "-l", "/state/package-admission/remote-events.jsonl"]
    ).stdout


def run() -> dict[str, Any]:
    for filename in ("ad_identity_worker.py", "directory_identity_recovery_worker.py"):
        support.docker_copy(ROOT / filename, "gitferry-b:/probe/" + filename)
    support.docker_copy(ROOT / "ad" / "mutate.py", "ad:/tmp/ad-mutate.py")
    initialized = worker("initialize")
    initial = worker("verify")
    if initial.get("status") != "passed":
        raise ProbeFailure("initial AD candidate was not usable")
    before_writes = writes()
    support.run([*support.COMPOSE, "stop", "ad"])
    try:
        outage = worker("verify")
    finally:
        support.run([*support.COMPOSE, "start", "ad"])
        ready()
    available = worker("verify")
    mutate("group-add")
    try:
        changed_group = worker("verify")
        group_restart = worker("verify")
    finally:
        mutate("group-remove")
    group_restored = worker("verify")
    group_reviewed = worker("reopen")
    group_recovered = worker("verify")
    mutate("disable")
    try:
        disabled = worker("verify")
    finally:
        mutate("enable")
    enabled = worker("verify")
    disabled_reviewed = worker("reopen")
    enabled_reviewed = worker("verify")
    recreated = mutate("recreate")
    sid_changed = worker("verify")
    sid_restart = worker("verify")
    reopen_denied = worker("reopen-denied")
    after_denied = worker("verify")
    checks = {
        "missing_mail_binding_approved": initialized["missing_mail_binding_approved"],
        "native_account_login_without_implicit_rights": initial.get("native_auth_status") == 200
        and initial.get("no_implicit_repository_access") is True,
        "outage_blocks_without_rebinding": outage["status"] == "blocked"
        and outage["after_status"] == "approved"
        and outage["binding_unchanged"]
        and outage["native_auth_status"] != 200
        and available["status"] == "passed",
        "ad_group_change_persists_conflict": changed_group["status"] == "blocked"
        and changed_group["changed_groups"]
        and group_restart["after_status"] == "conflict",
        "restored_groups_need_explicit_review": group_restored["after_status"] == "conflict"
        and group_reviewed["explicit_reapproval"]
        and group_recovered["status"] == "passed",
        "disabled_subject_blocks": disabled["status"] == "blocked"
        and disabled["after_status"] == "conflict"
        and disabled["native_auth_status"] in (401, 403),
        "enabled_subject_needs_explicit_review": enabled["after_status"] == "conflict"
        and disabled_reviewed["explicit_reapproval"]
        and enabled_reviewed["status"] == "passed",
        "same_login_new_sid_blocks": recreated["before"]["sid"] != recreated["after"]["sid"]
        and recreated["before"]["dn"] == recreated["after"]["dn"]
        and recreated["before"]["login"] == recreated["after"]["login"]
        and sid_changed["changed_external_id"]
        and sid_changed["status"] == "blocked"
        and sid_changed["native_auth_status"] == 200
        and sid_changed["observed_external_id"] == recreated["after"]["sid"],
        "sid_conflict_survives_restart_and_review": sid_restart["after_status"] == "conflict"
        and reopen_denied["new_sid_cannot_reopen"]
        and after_denied["after_status"] == "conflict",
        "binding_unchanged": initial["binding_before"] == after_denied["binding_after"],
        "no_mapped_writes": writes() == before_writes,
    }
    if not all(checks.values()):
        raise ProbeFailure("AD identity recovery assertions failed: " + json.dumps(checks))
    return {
        "status": "passed",
        "assertions": checks,
        "initial": initial,
        "outage": outage,
        "group_change": changed_group,
        "group_restored": group_restored,
        "disabled": disabled,
        "enabled": enabled,
        "recreated": recreated,
        "sid_changed": sid_changed,
        "fresh_process": sid_restart,
        "reopen_denied": reopen_denied,
        "limits": [
            "Samba AD-compatible server, not Microsoft Windows AD",
            "Direct group observations, not transitive permission expansion",
            "AD guard/core recovery; the full desktop rights flow is separately LDAP-qualified",
        ],
    }
