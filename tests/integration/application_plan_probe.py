"""Real target-only execution and interruption evidence for the durable application plan."""

from __future__ import annotations

import json
from typing import Any

from connection_profile_probe import certificate
from gitferry.application_package import write_package
from gitferry.git_bundle import create_git_bundle
from gitferry.package_archive import write_package_archive
from support import (
    PACKAGE,
    ROOT,
    TARGET_URL,
    USER_PASSWORD,
    WORK,
    ProbeFailure,
    api,
    docker_copy,
    docker_service_exec,
)

REPOSITORY = "application-plan-lab/selected"
REMOTE_ROOT = "/state/application-plans"
ASSERTIONS = frozenset(
    {
        "structure_bundle_and_metadata_confirmed",
        "distinct_native_authors",
        "parent_mapping_confirmed",
        "target_only_context",
        "application_package_bundle_received_and_used",
        "confirmed_restart_no_duplicates",
        "crash_remote_post_accepted",
        "crash_restart_outcome_unknown",
        "crash_no_second_post",
        "lost_remote_post_accepted",
        "lost_restart_outcome_unknown",
        "lost_no_second_post",
        "new_packaging_cannot_bypass_unknown",
        "explicit_resolution_rejects_wrong_parent_author_and_content",
        "explicit_resolution_then_restart_confirms_without_post",
        "control_repository_unchanged",
        "credentials_absent_from_state_and_report",
        "source_catalog_lists_real_organization_and_repository_without_writes",
    }
)


def worker(
    mode: str, case: str = "initial", expected: int = 0, *, target_comment_id: str | None = None
) -> dict[str, Any]:
    command = ["python", "/probe/plan_worker.py", mode, "--case", case]
    if target_comment_id is not None:
        command.extend(["--target-comment-id", target_comment_id])
    result = docker_service_exec(
        "gitferry-b",
        command,
        check=False,
        timeout=90,
    )
    if result.returncode != expected:
        raise ProbeFailure(
            f"Application plan worker {mode} exited {result.returncode}; expected {expected}"
        )
    if expected:
        return {}
    return json.loads(result.stdout)


def events() -> list[dict[str, Any]]:
    value = docker_service_exec("gitferry-b", ["cat", REMOTE_ROOT + "/remote-events.jsonl"])
    return [json.loads(line) for line in value.stdout.splitlines()]


def comments() -> list[dict[str, Any]]:
    return api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/comments")


def run_application_plan_probe() -> dict[str, Any]:
    package_root = WORK / "application-package"
    package_root.mkdir()
    bundle = create_git_bundle(WORK / "permission-seed", package_root, "bundles/selected.bundle")
    package_document = json.loads((ROOT / "examples/application-package.v1.json").read_text())
    package_document["sender"] = {
        "instance_key": "source",
        "installation_marker": "source-plan-install",
    }
    package_document["recipient"] = {
        "instance_key": "target",
        "installation_marker": "target-plan-install",
    }
    selected = package_document["repositories"][0]
    selected["repository"] = REPOSITORY
    selected["organization"]["name"] = "application-plan-lab"
    selected["bundle"] = {"path": bundle.member, "size": bundle.size, "sha256": bundle.sha256}
    package_artifact = write_package(package_root, package_document)
    write_package_archive(package_root, PACKAGE / "application-plan.zip")
    directory = WORK / "application-plan-tls"
    directory.mkdir()
    cert, key = certificate(directory, "trusted")
    docker_service_exec("gitferry-b", ["mkdir", "-p", REMOTE_ROOT])
    docker_copy(cert, "gitferry-b:" + REMOTE_ROOT + "/trusted.pem")
    docker_copy(key, "gitferry-b:" + REMOTE_ROOT + "/trusted.key")
    control = api(TARGET_URL, "GET", "/repos/access-lab/control-private")
    first = worker("initial")
    plan = first["plan"]
    if plan["status"] != "confirmed":
        # The plan stores only non-secret intents; retain useful bounded diagnostics.
        (WORK / "application-plan-failure.json").write_text(json.dumps(plan, indent=2))
        raise ProbeFailure(
            "Application plan did not confirm; inspect application-plan-failure.json"
        )
    issue = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1")
    initial_comments = comments()
    assertions = {
        "structure_bundle_and_metadata_confirmed": all(
            step["status"] == "confirmed" for step in plan["operations"]
        )
        and any(step["operation_type"] == "push_git_bundle" for step in plan["operations"]),
        "distinct_native_authors": issue["user"]["login"] == "issue-author"
        and len(initial_comments) == 1
        and initial_comments[0]["user"]["login"] == "comment-author",
        "parent_mapping_confirmed": len(initial_comments) == 1
        and initial_comments[0]["issue_url"].endswith("/1"),
        "target_only_context": first["isolated"] is True,
        "application_package_bundle_received_and_used": (
            first["package_digest"] == package_artifact.digest_sha256
            and first["isolated"] is True
            and any(
                step["operation_type"] == "push_git_bundle" and step["status"] == "confirmed"
                for step in plan["operations"]
            )
        ),
    }
    before = events()
    catalog = worker("browse")
    assertions["source_catalog_lists_real_organization_and_repository_without_writes"] = (
        any(item["name"] == "application-plan-lab" for item in catalog["organizations"]["entries"])
        and any(item["repository"] == REPOSITORY for item in catalog["repositories"]["entries"])
        and catalog["repositories"]["inventory_complete"] is False
        and events() == before
    )
    replay = worker("replay")
    assertions["confirmed_restart_no_duplicates"] = (
        replay["plan"]["status"] == "confirmed"
        and events() == before
        and comments() == initial_comments
    )
    details: dict[str, Any] = {}
    for case, expected in (("crash", 91), ("lost", 0)):
        before = events()
        count = len(comments())
        interrupted = worker(case, case, expected)
        accepted = events()[len(before) :]
        assertions[case + "_remote_post_accepted"] = (
            len(accepted) == 1
            and accepted[0]["method"] == "POST"
            and accepted[0]["status"] == 201
            and len(comments()) == count + 1
        )
        after = events()
        resumed = worker("resume", case)
        states = [step["status"] for step in resumed["plan"]["operations"]]
        assertions[case + "_restart_outcome_unknown"] = resumed["plan"][
            "status"
        ] == "blocked" and states == ["outcome_unknown"]
        assertions[case + "_no_second_post"] = events() == after and len(comments()) == count + 1
        repackaged = worker("repackage", case)
        assertions["new_packaging_cannot_bypass_unknown"] = (
            assertions.get("new_packaging_cannot_bypass_unknown", True)
            and repackaged["blocked"]
            and events() == after
        )
        details[case] = {
            "remote": accepted,
            "worker_exit": expected,
            "restart_plan_status": resumed["plan"]["status"],
            "operation_statuses": states,
            "interrupted_status": interrupted.get("plan", {}).get(
                "status", "process exited before confirmation"
            ),
        }
    # Only after restart/repackaging has proved the unresolved halt, simulate an
    # explicit operator selection. IDs are never fed into automatic recovery.
    other_issue = api(
        TARGET_URL,
        "POST",
        f"/repos/{REPOSITORY}/issues",
        json_body={"title": "Resolution decoy", "body": "synthetic"},
    )
    rejected = []
    for parent, username, body in (
        (other_issue["number"], "comment-author", "crash comment"),
        (1, "issue-author", "crash comment"),
        (1, "comment-author", "different content"),
    ):
        decoy = api(
            TARGET_URL,
            "POST",
            f"/repos/{REPOSITORY}/issues/{parent}/comments",
            auth=(username, USER_PASSWORD),
            json_body={"body": body},
        )
        result = worker("resolve", "crash", target_comment_id=str(decoy["id"]))
        rejected.append(
            result.get("rejected") is True
            and result["plan"]["operations"][0]["status"] == "outcome_unknown"
        )
    assertions["explicit_resolution_rejects_wrong_parent_author_and_content"] = all(rejected)
    accepted_comment = next(
        value
        for value in comments()
        if value["body"] == "crash comment" and value["user"]["login"] == "comment-author"
    )
    before_resolution = events()
    comment_count = len(comments())
    resolved = worker("resolve", "crash", target_comment_id=str(accepted_comment["id"]))
    restarted = worker("resume", "crash")
    assertions["explicit_resolution_then_restart_confirms_without_post"] = (
        resolved["plan"]["status"] == restarted["plan"]["status"] == "confirmed"
        and restarted["plan"]["operations"][0]["target_global_id"] == str(accepted_comment["id"])
        and events() == before_resolution
        and len(comments()) == comment_count
    )
    details["explicit_resolution"] = {
        "selection": "operator-selected target comment ID, supplied only after blocked recovery",
        "rejected_decoys": rejected,
        "target_comment_id": str(accepted_comment["id"]),
        "final_plan_status": restarted["plan"]["status"],
    }
    assertions["control_repository_unchanged"] = (
        api(TARGET_URL, "GET", "/repos/access-lab/control-private") == control
    )
    scan = """from pathlib import Path
root=Path('/state/application-plans')
secrets=[p.read_bytes().strip() for p in (root/'secrets').iterdir()]
files=list(root.glob('*.sqlite*'))+[root/'remote-events.jsonl']
clean = all(secret not in path.read_bytes() for path in files for secret in secrets)
raise SystemExit(0 if clean else 1)
"""
    assertions["credentials_absent_from_state_and_report"] = (
        docker_service_exec("gitferry-b", ["python", "-c", scan], check=False).returncode == 0
    )
    if set(assertions) != ASSERTIONS or not all(assertions.values()):
        raise ProbeFailure(f"Application plan assertions failed: {assertions}")
    return {
        "status": "passed",
        "assertions": assertions,
        "interruptions": details,
        "environment": (
            "target-only Linux application container with local HTTPS proxy to pinned Gitea"
        ),
        "limits": [
            "Bounded initial provisioning and metadata plan, not the complete import wizard",
            "Existing independent manifest roundtrip probes remain separate evidence",
            "No general exactly-once guarantee; interrupted creates remain blocked",
        ],
    }
