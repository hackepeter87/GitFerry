"""Real Gitea proof for the bounded, journaled provisioning application service."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from gitea_compat import PRIMARY_GITEA_VERSION
from gitferry.git_bundle import create_git_bundle
from permissions_probe import CONTROL_REPO, AccessContext
from support import (
    PACKAGE,
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    WORK,
    ProbeFailure,
    api,
    docker_service_exec,
    run,
)

ORG = "provision-service-lab"
REPOSITORY = f"{ORG}/selected-private"
PLAN_MEMBER = "provisioning/plan.json"
CRASH_ORG = "provision-crash-lab"
LOST_ORG = "provision-lost-lab"
GIT = shutil.which("git")
if GIT is None:
    raise RuntimeError("git executable is required for the provisioning integration probe")


def _create_source_structure(organization: str) -> dict[str, Any]:
    org = api(
        SOURCE_URL,
        "POST",
        "/orgs",
        json_body={"username": organization, "visibility": "private"},
    )
    repository = api(
        SOURCE_URL,
        "POST",
        f"/orgs/{organization}/repos",
        json_body={"name": "selected-private", "private": True, "auto_init": False},
    )
    teams: dict[str, Any] = {}
    for name, permission in (("readers", "read"), ("writers", "write")):
        teams[name] = api(
            SOURCE_URL,
            "POST",
            f"/orgs/{organization}/teams",
            json_body={
                "name": name,
                "permission": permission,
                "can_create_org_repo": False,
                "includes_all_repositories": False,
                "units_map": {"repo.code": permission, "repo.issues": permission},
            },
        )
    return {"organization": org, "repository": repository, "teams": teams}


def _seed_source_repository() -> Path:
    repository = WORK / "provisioning-seed"
    shutil.rmtree(repository, ignore_errors=True)
    repository.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=repository)
    run(["git", "config", "user.name", "Synthetic Provisioning Probe"], cwd=repository)
    run(["git", "config", "user.email", "provisioning@example.invalid"], cwd=repository)
    (repository / "README.md").write_text("# Durable provisioning proof\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repository)
    run(["git", "commit", "-m", "Seed durable provisioning proof"], cwd=repository)
    run(
        ["git", "tag", "-a", "provisioning-v1", "-m", "Annotated provisioning proof"],
        cwd=repository,
    )
    source_repository_url = f"{SOURCE_URL}/{REPOSITORY}.git"
    if (
        _git_with_password(
            "gitferry-admin",
            os.environ["GITFERRY_IT_ADMIN_PASSWORD"],
            ["push", source_repository_url, "main", "refs/tags/provisioning-v1"],
            cwd=repository,
        ).returncode
        != 0
    ):
        raise ProbeFailure("could not seed the source repository")
    return repository


def _binding(
    context: AccessContext, username: str, role: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = context.source_users[username]
    target = context.target_users[username]
    if source["id"] == target["id"]:
        raise ProbeFailure("provisioning binding requires deliberately different Gitea ids")
    return (
        {
            "binding_id": f"provision-{role}-binding",
            "source_realm_key": "synthetic-directory-a",
            "source_login": username,
            "source_external_id": f"S-1-5-21-PROVISION-{source['id']}",
            "source_ou_path": "OU=People,DC=source,DC=example,DC=invalid",
            "target_account_id": target["id"],
            "target_login": username,
            "mapping_version": 1,
        },
        {"binding_id": f"provision-{role}-binding", "role": role},
    )


def _write_plan(
    path: Path,
    structure: dict[str, Any],
    context: AccessContext,
    *,
    bundle: dict[str, Any] | None,
) -> None:
    reader_binding, reader_member = _binding(context, "perm-reader", "read")
    writer_binding, writer_member = _binding(context, "perm-writer", "write")
    organization = structure["organization"]
    repository = structure["repository"]
    value = {
        "organization": {
            "name": organization["username"],
            "origin_global_id": str(organization["id"]),
        },
        "repository": {
            "name": repository["full_name"],
            "origin_global_id": str(repository["id"]),
        },
        "teams": [
            {
                "name": "readers",
                "permission": "read",
                "origin_global_id": str(structure["teams"]["readers"]["id"]),
                "members": [reader_member],
            },
            {
                "name": "writers",
                "permission": "write",
                "origin_global_id": str(structure["teams"]["writers"]["id"]),
                "members": [writer_member],
            },
        ],
        "bindings": [reader_binding, writer_binding],
        "bundle": bundle,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _minimal_plan(path: Path, structure: dict[str, Any], packaging_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "packaging_id": packaging_id,
                "organization": {
                    "name": structure["organization"]["username"],
                    "origin_global_id": str(structure["organization"]["id"]),
                },
                "repository": {
                    "name": structure["repository"]["full_name"],
                    "origin_global_id": str(structure["repository"]["id"]),
                },
                "teams": [],
                "bindings": [],
                "bundle": None,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _worker(mode: str, member: str, expected: int = 0) -> dict[str, Any]:
    completed = docker_service_exec(
        "gitferry-b",
        ["python", "/probe/provisioning_worker.py", mode, f"/package/{member}"],
        check=False,
    )
    if completed.returncode != expected:
        raise ProbeFailure(
            f"provisioning worker {mode} returned {completed.returncode}, expected {expected}: "
            f"stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
    if not completed.stdout.strip():
        return {"status": "process-exited", "returncode": completed.returncode}
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _target_counts(organization: str) -> dict[str, int]:
    orgs = api(TARGET_URL, "GET", "/orgs?limit=50")
    matching = [item for item in orgs if item["username"] == organization]
    if not matching:
        return {"organizations": 0, "repositories": 0, "teams": 0}
    return {
        "organizations": len(matching),
        "repositories": len(api(TARGET_URL, "GET", f"/orgs/{organization}/repos?limit=50")),
        "teams": len(api(TARGET_URL, "GET", f"/orgs/{organization}/teams?limit=50")),
    }


def _git_with_password(
    username: str, password: str, args: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", prefix="gitferry-user-askpass-", delete=False) as script:
        script.write("#!/bin/sh\nprintf '%s\\n' \"$GITFERRY_ASKPASS_PASSWORD\"\n")
        script_path = Path(script.name)
    try:
        script_path.chmod(0o700)
        environment = {
            **os.environ,
            "GIT_ASKPASS": str(script_path),
            "GIT_TERMINAL_PROMPT": "0",
            "GITFERRY_ASKPASS_PASSWORD": password,
        }
        completed = subprocess.run(
            [GIT, "-c", f"credential.username={username}", *args],
            cwd=cwd,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
            timeout=120,
        )
        return completed
    finally:
        script_path.unlink(missing_ok=True)


def _git_as_user(username: str, args: list[str], *, cwd: Path | None = None) -> int:
    return _git_with_password(username, USER_PASSWORD, args, cwd=cwd).returncode


def _secure_refs(base_url: str, repository: str) -> dict[str, str]:
    completed = _git_with_password(
        "gitferry-admin",
        os.environ["GITFERRY_IT_ADMIN_PASSWORD"],
        ["ls-remote", f"{base_url}/{repository}.git"],
    )
    if completed.returncode != 0:
        raise ProbeFailure(f"could not read refs for {repository}")
    return {
        ref: object_id
        for object_id, ref in (line.split("\t", 1) for line in completed.stdout.splitlines())
        if ref != "HEAD"
    }


def _operation_evidence(operation_type: str, organization: str) -> dict[str, Any]:
    script = (
        "import json,sqlite3,sys;"
        "c=sqlite3.connect('/state/provisioning.sqlite3');c.row_factory=sqlite3.Row;"
        'r=c.execute("SELECT operation_id,status FROM operations WHERE operation_type=? '
        "AND intent_json LIKE ?\",(sys.argv[1],'%'+sys.argv[2]+'%')).fetchone();"
        'e=c.execute("SELECT count(*) FROM operation_events WHERE operation_id=? '
        "AND to_status='executing'\",(r['operation_id'],)).fetchone()[0];"
        "print(json.dumps({'status':r['status'],'executing_events':e}))"
    )
    completed = docker_service_exec(
        "gitferry-b", ["python", "-c", script, operation_type, organization]
    )
    return json.loads(completed.stdout)


def _state_contains_admin_secret() -> bool:
    script = (
        "import os,sys;from pathlib import Path;"
        "secret=os.environ['GITFERRY_IT_ADMIN_PASSWORD'].encode();"
        "raise SystemExit(1 if secret in Path('/state/provisioning.sqlite3').read_bytes() else 0)"
    )
    return docker_service_exec("gitferry-b", ["python", "-c", script], check=False).returncode != 0


def run_provisioning_probe(context: AccessContext) -> dict[str, Any]:
    control_before = _secure_refs(TARGET_URL, CONTROL_REPO)
    accounts_before = len(api(TARGET_URL, "GET", "/admin/users?limit=50"))
    structure = _create_source_structure(ORG)
    seed = _seed_source_repository()
    bundle = create_git_bundle(seed, PACKAGE, "provisioning/selected-private.bundle")
    plan_path = PACKAGE / PLAN_MEMBER
    _write_plan(
        plan_path,
        structure,
        context,
        bundle={"member": bundle.member, "sha256": bundle.sha256, "size": bundle.size},
    )
    plan_text = plan_path.read_text(encoding="utf-8")
    if os.environ["GITFERRY_IT_ADMIN_PASSWORD"] in plan_text or USER_PASSWORD in plan_text:
        raise ProbeFailure("provisioning plan contains a credential")

    source_refs = _secure_refs(SOURCE_URL, REPOSITORY)
    first = _worker("apply", PLAN_MEMBER)
    counts_after_first = _target_counts(ORG)
    target_refs = _secure_refs(TARGET_URL, REPOSITORY)
    replay = _worker("apply", PLAN_MEMBER)
    counts_after_replay = _target_counts(ORG)
    refs_after_replay = _secure_refs(TARGET_URL, REPOSITORY)

    repository_url = f"{TARGET_URL}/{REPOSITORY}.git"
    access = {
        "reader_can_read": _git_as_user("perm-reader", ["ls-remote", repository_url]) == 0,
        "reader_push_denied": _git_as_user(
            "perm-reader",
            ["push", repository_url, "HEAD:refs/heads/reader-must-not-exist"],
            cwd=seed,
        )
        != 0,
        "writer_can_read": _git_as_user("perm-writer", ["ls-remote", repository_url]) == 0,
        "writer_push_allowed": _git_as_user(
            "perm-writer",
            ["push", repository_url, "HEAD:refs/heads/writer-provisioning-proof"],
            cwd=seed,
        )
        == 0,
        "outsider_denied": _git_as_user("perm-outsider", ["ls-remote", repository_url]) != 0,
    }
    final_refs = _secure_refs(TARGET_URL, REPOSITORY)
    access["reader_branch_absent"] = "refs/heads/reader-must-not-exist" not in final_refs
    access["writer_branch_present"] = "refs/heads/writer-provisioning-proof" in final_refs
    if not all(access.values()):
        raise ProbeFailure(f"application-service permission proof failed: {access}")

    crash_structure = _create_source_structure(CRASH_ORG)
    crash_member = "provisioning/crash-plan.json"
    _minimal_plan(PACKAGE / crash_member, crash_structure, "first-package")
    _worker("crash-after-remote", crash_member, expected=91)
    crash_after_remote = _target_counts(CRASH_ORG)
    crash_replay = _worker("apply", crash_member, expected=5)
    crash_after_replay = _target_counts(CRASH_ORG)
    crash_evidence = _operation_evidence("create_organization", CRASH_ORG)

    lost_structure = _create_source_structure(LOST_ORG)
    lost_member = "provisioning/lost-plan.json"
    _minimal_plan(PACKAGE / lost_member, lost_structure, "first-package")
    lost_first = _worker("lost-response", lost_member, expected=5)
    lost_after_remote = _target_counts(LOST_ORG)
    repackaged_member = "provisioning/lost-plan-repackaged.json"
    _minimal_plan(PACKAGE / repackaged_member, lost_structure, "new-package-id")
    lost_replay = _worker("apply", repackaged_member, expected=5)
    lost_after_replay = _target_counts(LOST_ORG)
    lost_evidence = _operation_evidence("create_organization", LOST_ORG)

    control_after = _secure_refs(TARGET_URL, CONTROL_REPO)
    accounts_after = len(api(TARGET_URL, "GET", "/admin/users?limit=50"))
    assertions = {
        "all_operations_confirmed": all(
            item["status"] == "confirmed" for item in first["operations"]
        ),
        "replay_all_operations_confirmed": all(
            item["status"] == "confirmed" for item in replay["operations"]
        ),
        "expected_private_structure_created": counts_after_first
        == {"organizations": 1, "repositories": 1, "teams": 3},
        "replay_created_no_structure": counts_after_first == counts_after_replay,
        "bundle_refs_match_source": target_refs == source_refs,
        "replay_changed_no_refs": refs_after_replay == target_refs,
        "functional_access_matches_plan": all(access.values()),
        "control_repository_untouched": control_before == control_after,
        "no_accounts_created": accounts_before == accounts_after,
        "crash_remote_post_succeeded": crash_after_remote["organizations"] == 1,
        "crash_restart_is_outcome_unknown": crash_evidence["status"] == "outcome_unknown",
        "crash_replay_sent_no_second_post": crash_evidence["executing_events"] == 1
        and crash_after_replay == crash_after_remote,
        "lost_response_remote_post_succeeded": lost_after_remote["organizations"] == 1,
        "lost_response_is_outcome_unknown": lost_evidence["status"] == "outcome_unknown",
        "new_packaging_cannot_bypass_unknown": lost_evidence["executing_events"] == 1
        and lost_after_replay == lost_after_remote,
        "credentials_absent_from_plan_and_state": not _state_contains_admin_secret(),
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"durable provisioning assertions failed: {assertions}")
    return {
        "status": "passed",
        "interfaces": [
            "GitFerry durable provisioning service",
            f"Gitea {PRIMARY_GITEA_VERSION} REST API",
            "Git bundle and Git smart HTTP",
        ],
        "assertions": assertions,
        "target_counts": counts_after_replay,
        "effective_git_access": access,
        "crash_probe": {
            "worker": {"status": "process-exited", "returncode": 91},
            "restart": crash_replay,
            "journal": crash_evidence,
        },
        "lost_response_probe": {
            "first": lost_first,
            "repackaged_replay": lost_replay,
            "journal": lost_evidence,
        },
        "known_limits": [
            "Only repository code and issue units are configured in this bounded proof.",
            "SQLite uniqueness does not provide an external exactly-once guarantee.",
        ],
    }


__all__ = ["run_provisioning_probe"]
