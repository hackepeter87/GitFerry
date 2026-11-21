"""Manifest v2 comment file roundtrip across isolated GitFerry states."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import httpx
from permissions_probe import CONTROL_REPO, SELECTED_REPO
from support import (
    RESULTS,
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    ProbeFailure,
    api,
    docker_copy,
    docker_service_exec,
)

EXCHANGE = RESULTS / "comment-transport-exchange"


def worker(
    service: str, mode: str, *, return_path: str | None = None, expected: int = 0
) -> dict[str, Any]:
    args = ["python", "/probe/comment_transport_worker.py", mode]
    if return_path is not None:
        args = ["env", f"GITFERRY_COMMENT_RETURN_PATH={return_path}", *args]
    completed = docker_service_exec(service, args, check=False)
    if completed.returncode != expected:
        raise ProbeFailure(
            f"comment worker {service}/{mode}: {completed.returncode}; "
            f"stdout={completed.stdout!r}; stderr={completed.stderr!r}"
        )
    if expected == 91:
        return {"hard_exit": True}
    return json.loads(completed.stdout.strip().splitlines()[-1])


def copy_between(source: str, path: str, host: Path, target: str) -> None:
    shutil.rmtree(host, ignore_errors=True)
    docker_copy(f"{source}:{path}", host)
    docker_service_exec(target, ["mkdir", "-p", "/state/inbox"])
    docker_copy(host, f"{target}:/state/inbox/")


def all_comments(base_url: str) -> list[dict[str, Any]]:
    issues = api(base_url, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100")
    result: list[dict[str, Any]] = []
    for issue in issues:
        result.extend(
            api(
                base_url,
                "GET",
                f"/repos/{SELECTED_REPO}/issues/{issue['number']}/comments?limit=100",
                auth=("comment-author", USER_PASSWORD),
            )
        )
    return result


def openapi_update_contract() -> dict[str, Any]:
    document = httpx.get(f"{SOURCE_URL}/swagger.v1.json", timeout=20).json()
    schemas = document.get("components", {}).get("schemas", document.get("definitions", {}))
    properties = sorted(schemas.get("EditIssueCommentOption", {}).get("properties", {}))
    return {
        "schema": "EditIssueCommentOption",
        "request_fields": properties,
        "atomic_precondition_field_present": any(
            name in properties for name in ("content_version", "etag", "if_match", "updated_at")
        ),
    }


def wait_past_native_timestamp(value: str, timeout_seconds: float = 5) -> None:
    target = datetime.fromisoformat(value.replace("Z", "+00:00"))
    deadline = monotonic() + timeout_seconds
    while datetime.now(UTC) <= target:
        if monotonic() >= deadline:
            raise ProbeFailure(
                f"clock did not advance past native timestamp within {timeout_seconds}s"
            )
        sleep(0.02)


def run_comment_transport_roundtrip_probe() -> dict[str, Any]:
    shutil.rmtree(EXCHANGE, ignore_errors=True)
    EXCHANGE.mkdir(parents=True)
    seed = worker("gitferry-a", "seed")
    forward_host = EXCHANGE / "comments-forward"
    copy_between("gitferry-a", "/state/outbox/comments-forward", forward_host, "gitferry-b")

    target_issues_before = len(
        api(TARGET_URL, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100")
    )
    target_comments_before = len(all_comments(TARGET_URL))
    worker("gitferry-b", "import")
    target_issues_after = len(
        api(TARGET_URL, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100")
    )
    target_comments_after = len(all_comments(TARGET_URL))
    worker("gitferry-b", "import")
    target_comments_replayed = len(all_comments(TARGET_URL))

    crash_before = len(all_comments(TARGET_URL))
    worker("gitferry-b", "crash", expected=91)
    crash_after_remote = len(all_comments(TARGET_URL))
    recovered_crash = worker("gitferry-b", "recover-crash")
    crash_after_restart = len(all_comments(TARGET_URL))

    lost_before = len(all_comments(TARGET_URL))
    lost = worker("gitferry-b", "lose")
    lost_after_remote = len(all_comments(TARGET_URL))
    recovered_lost = worker("gitferry-b", "recover-lost")
    lost_after_restart = len(all_comments(TARGET_URL))

    returned = worker("gitferry-b", "return")
    return_host = EXCHANGE / "comments-return"
    copy_between("gitferry-b", "/state/outbox/comments-return", return_host, "gitferry-a")

    original_comment = api(
        SOURCE_URL,
        "GET",
        f"/repos/{SELECTED_REPO}/issues/{seed['issue']['number']}/comments?limit=100",
        auth=("comment-author", USER_PASSWORD),
    )[0]
    original_created_at = original_comment["created_at"]
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/comments/{original_comment['id']}",
        auth=("comment-author", USER_PASSWORD),
        json_body={"body": "Relevant A comment drift"},
    )
    blocked = worker("gitferry-a", "apply", expected=5)
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/comments/{original_comment['id']}",
        auth=("comment-author", USER_PASSWORD),
        json_body={"body": original_comment["body"]},
    )
    control = api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{CONTROL_REPO}",
        json_body={"description": control["description"] + " comment-v2-control"},
    )
    wait_past_native_timestamp(original_created_at)
    source_issues_before = len(
        api(SOURCE_URL, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100")
    )
    source_comments_before = len(all_comments(SOURCE_URL))
    applied = worker("gitferry-a", "apply")
    source_issues_after = len(
        api(SOURCE_URL, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100")
    )
    source_comments_after = len(all_comments(SOURCE_URL))
    replay = worker("gitferry-a", "apply")
    source_comments_replayed = len(all_comments(SOURCE_URL))

    repackaged = worker("gitferry-b", "repackage")
    repackaged_host = EXCHANGE / "comments-repackaged"
    copy_between("gitferry-b", "/state/outbox/comments-repackaged", repackaged_host, "gitferry-a")
    repackaged_apply = worker("gitferry-a", "apply", return_path="/state/inbox/comments-repackaged")
    source_comments_repackaged = len(all_comments(SOURCE_URL))

    comments = [item for item in applied["outcomes"] if item["type"] == "comment"]
    issues = [item for item in applied["outcomes"] if item["type"] == "issue"]
    existing = next(item for item in comments if item["origin_id"] == returned["origin_ids"][1])
    case_b = next(item for item in comments if item["origin_id"] == returned["origin_ids"][2])
    case_c = next(item for item in comments if item["origin_id"] == returned["origin_ids"][0])
    case_c_issue = issues[0]
    assertions = {
        "forward_issue_created_once": target_issues_after == target_issues_before + 1,
        "forward_comment_created_once": target_comments_after == target_comments_before + 1,
        "forward_restart_no_duplicate": target_comments_replayed == target_comments_after,
        "hard_crash_remote_post_happened": crash_after_remote == crash_before + 1,
        "hard_crash_restart_no_post": crash_after_restart == crash_after_remote,
        "hard_crash_unknown_has_no_target_id": recovered_crash["status"] == "outcome_unknown"
        and recovered_crash["target_global_id"] is None,
        "hard_crash_has_verified_remote_acceptance_sync": recovered_crash[
            "synchronization_evidence"
        ]["remote_accepted"]
        and recovered_crash["synchronization_evidence"]["live_comment_verified_after_restart"]
        and recovered_crash["synchronization_evidence"][
            "target_id_not_supplied_to_gitferry_recovery"
        ]
        and recovered_crash["count_before"] == recovered_crash["count_after"],
        "lost_response_remote_post_happened": lost_after_remote == lost_before + 1,
        "lost_response_restart_no_post": lost_after_restart == lost_after_remote
        and lost["status"] == "outcome_unknown"
        and recovered_lost["status"] == "outcome_unknown",
        "lost_response_has_verified_remote_acceptance_sync": recovered_lost[
            "synchronization_evidence"
        ]["remote_accepted"]
        and recovered_lost["synchronization_evidence"]["live_comment_verified_after_restart"]
        and recovered_lost["synchronization_evidence"][
            "target_id_not_supplied_to_gitferry_recovery"
        ]
        and recovered_lost["count_before"] == recovered_lost["count_after"],
        "comment_drift_blocked_before_any_return_write": blocked["status"] == "blocked"
        and source_issues_before == source_issues_after - 1,
        "case_a_updated_same_comment": existing["target_global_id"] == str(original_comment["id"])
        and existing["body"] == returned["changed_body"],
        "case_a_parent_author_and_created_at_retained": existing["parent_issue_index"]
        == seed["issue"]["number"]
        and existing["author"] == "comment-author"
        and existing["native_created_at"] == original_created_at,
        "case_b_b_origin_on_a_parent": case_b["origin_id"] == returned["origin_ids"][2]
        and case_b["parent_issue_index"] == seed["issue"]["number"]
        and case_b["author"] == "comment-author-b",
        "case_c_comment_before_issue_planned_parent_first": case_c["parent_issue_index"]
        == case_c_issue["target_issue_index"]
        and case_c["author"] == "comment-author-b",
        "return_created_one_issue_two_comments": source_issues_after == source_issues_before + 1
        and source_comments_after == source_comments_before + 2,
        "return_restart_no_duplicate": source_comments_replayed == source_comments_after,
        "new_envelope_no_duplicate": source_comments_repackaged == source_comments_after,
        "repackage_preserved_origins": repackaged["origin_ids"] == returned["origin_ids"],
        "all_repackaged_operations_confirmed": all(
            item["status"] == "confirmed" for item in repackaged_apply["outcomes"]
        ),
        "control_repository_change_preserved": api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")[
            "description"
        ].endswith("comment-v2-control"),
        "original_and_native_times_separate_for_new_comments": case_b["origin_created_at"]
        != case_b["native_created_at"],
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"Manifest v2 comment assertions failed: {assertions}")
    contract = openapi_update_contract()
    return {
        "status": "passed",
        "gitea_version": "1.27.2",
        "manifests": {
            "forward": {"id": seed["manifest_id"], "digest": seed["digest_sha256"]},
            "return": {"id": returned["manifest_id"], "digest": returned["digest_sha256"]},
            "repackaged": {"id": repackaged["manifest_id"], "digest": repackaged["digest_sha256"]},
        },
        "cases": {
            "existing_a_comment_changed_on_b": existing,
            "new_b_comment_on_a_issue": case_b,
            "new_b_issue_and_comment": {"issue": case_c_issue, "comment": case_c},
        },
        "restart": {
            "hard_process_exit": recovered_crash,
            "lost_http_response": recovered_lost,
            "manifest_replay": replay,
        },
        "interruption_transitions": {
            "hard_process_exit": ["executing", "process_exit_91", "outcome_unknown"],
            "lost_http_response": ["executing", "remote_response_discarded", "outcome_unknown"],
            "automatic_retry_policy": "halted_without_second_post",
        },
        "live_api_contract": contract,
        "assertions": assertions,
        "limitations": [
            "Normal issue comments and body changes only; no deletes, moves, reviews, "
            "timelines, reactions, or attachments.",
            "EditIssueCommentOption exposes no atomic content-version/ETag precondition "
            "in the pinned live contract; another writer can race read-before-PATCH.",
            "Original comment timestamps are portable origin facts; newly created native "
            "timestamps are not historical restoration.",
            "SHA-256 is byte integrity, not authenticated provenance, and no general "
            "exactly-once guarantee is claimed.",
        ],
    }
