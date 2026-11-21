"""Executed feasibility probes against two real Gitea instances."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from comment_transport_roundtrip_probe import run_comment_transport_roundtrip_probe
from connection_profile_probe import run_connection_profile_probe
from metadata_roundtrip_probe import run_metadata_roundtrip_probe
from oci_runtime_probe import run_oci_runtime_probe
from openapi_migration_probe import run_openapi_migration_probe
from permissions_probe import run_permissions_probe
from persistent_state_probe import run_persistent_state_probe
from provisioning_probe import run_provisioning_probe
from support import (
    ADMIN,
    PACKAGE,
    RESULTS,
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    WORK,
    ProbeFailure,
    api,
    authenticated_url,
    create_user,
    docker_exec,
    run,
)
from transport_roundtrip_probe import run_transport_roundtrip_probe


def create_org_and_repo(base_url: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    org = api(
        base_url, "POST", "/orgs", json_body={"username": "ferry-lab", "visibility": "private"}
    )
    team = api(
        base_url,
        "POST",
        "/orgs/ferry-lab/teams",
        json_body={
            "name": "developers",
            "permission": "write",
            "units_map": {"repo.code": "write", "repo.issues": "write"},
        },
    )
    repo = api(
        base_url,
        "POST",
        "/orgs/ferry-lab/repos",
        json_body={"name": "selected-repo", "private": True, "auto_init": False},
    )
    for username in ("developer", "viewer"):
        api(base_url, "PUT", f"/teams/{team['id']}/members/{username}")
    api(base_url, "PUT", f"/teams/{team['id']}/repos/ferry-lab/selected-repo")
    return org, team, repo


def seed_git_repository() -> Path:
    repository = WORK / "seed-repo"
    shutil.rmtree(repository, ignore_errors=True)
    repository.mkdir(parents=True)
    run(["git", "init", "-b", "main"], cwd=repository)
    run(["git", "config", "user.name", "Synthetic Developer"], cwd=repository)
    run(["git", "config", "user.email", "developer@example.invalid"], cwd=repository)
    (repository / "README.md").write_text("# Selected synthetic repository\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repository)
    run(["git", "commit", "-m", "Initial synthetic commit"], cwd=repository)
    (repository / "history.txt").write_text("second commit\n", encoding="utf-8")
    run(["git", "add", "history.txt"], cwd=repository)
    run(["git", "commit", "-m", "Add synthetic history"], cwd=repository)
    run(["git", "branch", "feature/offline-proof"], cwd=repository)
    run(["git", "tag", "-a", "v1.0.0", "-m", "Synthetic annotated tag"], cwd=repository)
    source_git = authenticated_url(SOURCE_URL, "/ferry-lab/selected-repo.git")
    run(["git", "remote", "add", "source", source_git], cwd=repository)
    run(["git", "push", "source", "--all"], cwd=repository)
    run(["git", "push", "source", "--tags"], cwd=repository)
    return repository


def ref_map(url: str) -> dict[str, str]:
    output = run(["git", "ls-remote", url]).stdout
    return {
        ref: object_id
        for object_id, ref in (line.split("\t", 1) for line in output.splitlines())
        if ref != "HEAD" and not ref.endswith("^{}")
    }


def git_bundle_probe() -> dict[str, Any]:
    mirror = WORK / "source-mirror.git"
    bundle = PACKAGE / "selected-repo.bundle"
    shutil.rmtree(mirror, ignore_errors=True)
    bundle.unlink(missing_ok=True)
    source_git = authenticated_url(SOURCE_URL, "/ferry-lab/selected-repo.git")
    run(["git", "clone", "--mirror", source_git, str(mirror)])
    run(["git", "bundle", "create", str(bundle), "--all"], cwd=mirror)
    run(["git", "bundle", "verify", str(bundle)], cwd=mirror)

    source_refs = ref_map(source_git)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()

    source_isolation = (
        docker_exec(
            ["sh", "-c", "wget -T 2 -qO- http://source:3000/api/v1/version >/dev/null"], check=False
        ).returncode
        != 0
    )
    internet_isolation = (
        docker_exec(
            ["sh", "-c", "wget -T 2 -qO- http://1.1.1.1 >/dev/null"], check=False
        ).returncode
        != 0
    )
    if not source_isolation or not internet_isolation:
        raise ProbeFailure("offline importer unexpectedly reached source or public network")

    docker_exec(
        [
            "sh",
            "-c",
            "rm -rf /tmp/imported.git && "
            "git clone --mirror /package/selected-repo.bundle /tmp/imported.git",
        ]
    )
    target_git = authenticated_url("http://target:3000", "/ferry-lab/selected-repo.git", ADMIN)
    docker_exec(["git", "-C", "/tmp/imported.git", "push", "--mirror", target_git])
    target_refs = ref_map(authenticated_url(TARGET_URL, "/ferry-lab/selected-repo.git"))
    refs_equal = source_refs == target_refs
    if not refs_equal:
        raise ProbeFailure(f"Git refs differ: source={source_refs}, target={target_refs}")

    corrupt = WORK / "selected-repo.corrupt.bundle"
    data = bytearray(bundle.read_bytes())
    data[-1] ^= 0xFF
    corrupt.write_bytes(data)
    bundle_verify_detected_corruption = (
        run(["git", "bundle", "verify", str(corrupt)], cwd=mirror, check=False).returncode != 0
    )
    corrupt_digest = hashlib.sha256(corrupt.read_bytes()).hexdigest()
    sha256_detected_corruption = corrupt_digest != digest
    corrupt_clone = WORK / "corrupt-clone.git"
    shutil.rmtree(corrupt_clone, ignore_errors=True)
    clone_rejected_corruption = (
        run(
            ["git", "clone", "--mirror", str(corrupt), str(corrupt_clone)],
            check=False,
        ).returncode
        != 0
    )
    if not sha256_detected_corruption or not clone_rejected_corruption:
        raise ProbeFailure("corrupted bundle was not rejected by integrity and clone checks")

    return {
        "status": "passed",
        "sha256": digest,
        "bytes": bundle.stat().st_size,
        "source_refs": source_refs,
        "target_refs": target_refs,
        "refs_equal": refs_equal,
        "source_unreachable_from_importer": source_isolation,
        "internet_unreachable_from_importer": internet_isolation,
        "sha256_detected_corruption": sha256_detected_corruption,
        "clone_rejected_corruption": clone_rejected_corruption,
        "bundle_verify_detected_corruption": bundle_verify_detected_corruption,
    }


def metadata_probe(
    source_users: Mapping[str, Any], target_users: Mapping[str, Any]
) -> dict[str, Any]:
    source_label = api(
        SOURCE_URL,
        "POST",
        "/repos/ferry-lab/selected-repo/labels",
        json_body={"name": "probe", "color": "2f6f44", "description": "Synthetic probe label"},
    )
    api(
        SOURCE_URL,
        "PUT",
        "/repos/ferry-lab/selected-repo/collaborators/viewer",
        json_body={"permission": "write"},
    )
    source_issue = api(
        SOURCE_URL,
        "POST",
        "/repos/ferry-lab/selected-repo/issues",
        auth=("developer", USER_PASSWORD),
        json_body={
            "title": "Synthetic transfer issue",
            "body": "Issue body with an explicit relationship to its comment.",
            "labels": [source_label["id"]],
        },
    )
    source_comment = api(
        SOURCE_URL,
        "POST",
        f"/repos/ferry-lab/selected-repo/issues/{source_issue['number']}/comments",
        auth=("viewer", USER_PASSWORD),
        json_body={"body": "Synthetic issue comment"},
    )

    # Prevent one-second timestamp resolution from looking like preservation by accident.
    time.sleep(2)

    target_label = api(
        TARGET_URL,
        "POST",
        "/repos/ferry-lab/selected-repo/labels",
        json_body={
            "name": source_label["name"],
            "color": source_label["color"],
            "description": source_label["description"],
        },
    )
    target_issue = api(
        TARGET_URL,
        "POST",
        "/repos/ferry-lab/selected-repo/issues",
        auth=("developer", USER_PASSWORD),
        json_body={
            "title": source_issue["title"],
            "body": source_issue["body"],
            "closed": source_issue["state"] == "closed",
            "labels": [target_label["id"]],
        },
    )
    target_comment = api(
        TARGET_URL,
        "POST",
        f"/repos/ferry-lab/selected-repo/issues/{target_issue['number']}/comments",
        auth=("viewer", USER_PASSWORD),
        json_body={"body": source_comment["body"], "created_at": source_comment["created_at"]},
    )

    fields = {
        "issue.title": source_issue["title"] == target_issue["title"],
        "issue.body": source_issue["body"] == target_issue["body"],
        "issue.state": source_issue["state"] == target_issue["state"],
        "issue.author_login": source_issue["user"]["login"] == target_issue["user"]["login"],
        "issue.created_at": source_issue["created_at"] == target_issue["created_at"],
        "issue.label": source_issue["labels"][0]["name"] == target_issue["labels"][0]["name"],
        "comment.body": source_comment["body"] == target_comment["body"],
        "comment.author_login": source_comment["user"]["login"] == target_comment["user"]["login"],
        "comment.created_at": source_comment["created_at"] == target_comment["created_at"],
        "comment.issue_relation": target_comment["issue_url"].endswith(
            f"/{target_issue['number']}"
        ),
    }
    essential = [key for key in fields if not key.endswith("created_at")]
    if not all(fields[key] for key in essential):
        raise ProbeFailure(f"essential metadata mismatch: {fields}")

    return {
        "status": "passed",
        "field_comparison": fields,
        "id_mapping": {
            "issue": {"source": source_issue["id"], "target": target_issue["id"]},
            "comment": {"source": source_comment["id"], "target": target_comment["id"]},
            "developer": {
                "source": source_users["developer"]["id"],
                "target": target_users["developer"]["id"],
            },
            "viewer": {
                "source": source_users["viewer"]["id"],
                "target": target_users["viewer"]["id"],
            },
        },
        "source": {"issue": source_issue, "comment": source_comment},
        "target": {"issue": target_issue, "comment": target_comment},
    }


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    PACKAGE.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)

    source_version = api(SOURCE_URL, "GET", "/version")["version"]
    target_version = api(TARGET_URL, "GET", "/version")["version"]
    if source_version != "1.27.2" or target_version != "1.27.2":
        raise ProbeFailure(f"unexpected Gitea versions: {source_version}, {target_version}")

    source_users = {name: create_user(SOURCE_URL, name) for name in ("developer", "viewer")}
    create_user(TARGET_URL, "target-id-offset")
    target_users = {name: create_user(TARGET_URL, name) for name in ("viewer", "developer")}

    create_org_and_repo(SOURCE_URL)
    api(
        SOURCE_URL,
        "POST",
        "/orgs/ferry-lab/repos",
        json_body={"name": "unselected-repo", "private": True, "auto_init": True},
    )
    create_org_and_repo(TARGET_URL)
    seed_git_repository()

    permissions, access_context = run_permissions_probe()

    report = {
        "gitea": {"source": source_version, "target": target_version},
        "connection_profiles": run_connection_profile_probe(),
        "git_bundle": git_bundle_probe(),
        "metadata": metadata_probe(source_users, target_users),
        "identity_and_permissions": permissions,
        "durable_provisioning": run_provisioning_probe(access_context),
        "metadata_roundtrip": run_metadata_roundtrip_probe(access_context),
        "persistent_state_restart": run_persistent_state_probe(access_context),
        "portable_transport_roundtrip": run_transport_roundtrip_probe(),
        "comment_transport_roundtrip": run_comment_transport_roundtrip_probe(),
        "openapi_and_migration": run_openapi_migration_probe(access_context),
        "oci_runtime": run_oci_runtime_probe(),
    }
    (RESULTS / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
