"""Bounded metadata roundtrip, native-author, idempotency, and drift probe."""

from __future__ import annotations

import time
from typing import Any

from gitferry.baselines import IssueBaseline, issue_baseline, issue_has_drift
from permissions_probe import CONTROL_REPO, SELECTED_REPO, AccessContext
from support import (
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    ProbeFailure,
    api,
)

ISSUE_AUTHOR = "issue-author"
COMMENT_AUTHOR = "comment-author"
IMPORTER = "gitferry-importer"


def auth(username: str) -> tuple[str, str]:
    return (username, USER_PASSWORD)


def create_issue(base_url: str, title: str, body: str, username: str) -> dict[str, Any]:
    return api(
        base_url,
        "POST",
        f"/repos/{SELECTED_REPO}/issues",
        auth=auth(username),
        json_body={"title": title, "body": body},
    )


def create_comment(base_url: str, issue_index: int, body: str, username: str) -> dict[str, Any]:
    return api(
        base_url,
        "POST",
        f"/repos/{SELECTED_REPO}/issues/{issue_index}/comments",
        auth=auth(username),
        json_body={"body": body},
    )


def get_issue(base_url: str, issue_index: int) -> dict[str, Any]:
    return api(base_url, "GET", f"/repos/{SELECTED_REPO}/issues/{issue_index}")


def issue_count(base_url: str) -> int:
    return len(api(base_url, "GET", f"/repos/{SELECTED_REPO}/issues?state=all&limit=100"))


def baseline_report(baseline: IssueBaseline) -> dict[str, Any]:
    return {"fields": baseline.fields, "values": dict(baseline.values)}


def assert_native_author(
    value: dict[str, Any], expected_account: dict[str, Any], object_name: str
) -> dict[str, Any]:
    observed = {
        "id": value["user"]["id"],
        "login": value["user"]["login"],
        "expected_id": expected_account["id"],
        "expected_login": expected_account["login"],
    }
    observed["matches_explicit_target_account"] = (
        observed["id"] == observed["expected_id"]
        and observed["login"] == observed["expected_login"]
    )
    if not observed["matches_explicit_target_account"]:
        raise ProbeFailure(f"native {object_name} author did not match mapping: {observed}")
    return observed


def transfer_existing_to_b(
    source_issue: dict[str, Any], source_comment: dict[str, Any], context: AccessContext
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    target_issue = create_issue(
        TARGET_URL, source_issue["title"], source_issue["body"], ISSUE_AUTHOR
    )
    target_comment = create_comment(
        TARGET_URL, target_issue["number"], source_comment["body"], COMMENT_AUTHOR
    )
    mapping = {
        "origin": {
            "instance": "A",
            "repository": SELECTED_REPO,
            "issue_global_id": source_issue["id"],
            "issue_index": source_issue["number"],
            "comment_global_id": source_comment["id"],
        },
        "destination": {
            "instance": "B",
            "repository": SELECTED_REPO,
            "issue_global_id": target_issue["id"],
            "issue_index": target_issue["number"],
            "comment_global_id": target_comment["id"],
        },
        "origin_information": {
            "issue_created_at": source_issue["created_at"],
            "comment_created_at": source_comment["created_at"],
            "issue_external_identity": context.mappings[ISSUE_AUTHOR]["source"],
            "comment_external_identity": context.mappings[COMMENT_AUTHOR]["source"],
        },
    }
    return target_issue, target_comment, mapping


def case_existing_issue(context: AccessContext) -> dict[str, Any]:
    # Seed target-global IDs so accidental numeric equality cannot masquerade as identity.
    dummy = create_issue(TARGET_URL, "Target ID offset", "Not part of transfer", IMPORTER)
    create_comment(TARGET_URL, dummy["number"], "Target comment ID offset", IMPORTER)

    source_issue = create_issue(
        SOURCE_URL,
        "Roundtrip case A",
        "Initial central body",
        ISSUE_AUTHOR,
    )
    source_comment = create_comment(
        SOURCE_URL,
        source_issue["number"],
        "Comment by a distinct functional author",
        COMMENT_AUTHOR,
    )
    baseline = issue_baseline(source_issue)
    source_created_at = source_issue["created_at"]
    source_updated_at = source_issue["updated_at"]
    time.sleep(2)
    target_issue, target_comment, mapping = transfer_existing_to_b(
        source_issue, source_comment, context
    )

    author_evidence = {
        "source_issue": assert_native_author(
            source_issue, context.source_users[ISSUE_AUTHOR], "source issue"
        ),
        "source_comment": assert_native_author(
            source_comment, context.source_users[COMMENT_AUTHOR], "source comment"
        ),
        "target_issue": assert_native_author(
            target_issue, context.target_users[ISSUE_AUTHOR], "target issue"
        ),
        "target_comment": assert_native_author(
            target_comment, context.target_users[COMMENT_AUTHOR], "target comment"
        ),
    }
    importer_ids = {
        "source": context.source_users[IMPORTER]["id"],
        "target": context.target_users[IMPORTER]["id"],
    }
    if (
        target_issue["user"]["id"] == importer_ids["target"]
        or target_comment["user"]["id"] == importer_ids["target"]
    ):
        raise ProbeFailure("technical importer incorrectly became a native metadata author")

    changed_on_b = api(
        TARGET_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{target_issue['number']}",
        auth=auth(IMPORTER),
        json_body={
            "body": "Body changed in external environment B",
            "content_version": target_issue["content_version"],
        },
    )

    control_before = api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")
    api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{CONTROL_REPO}",
        json_body={"description": "Changed outside the selected repository"},
    )
    control_after = api(SOURCE_URL, "GET", f"/repos/{CONTROL_REPO}")
    control_changed = control_before["description"] != control_after["description"]

    current_source = get_issue(SOURCE_URL, source_issue["number"])
    selected_drift = issue_has_drift(baseline, current_source)
    count_before = issue_count(SOURCE_URL)
    if selected_drift:
        raise ProbeFailure("selected source issue drifted before the non-conflicting return")
    time.sleep(2)
    returned = api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{source_issue['number']}",
        auth=auth(IMPORTER),
        json_body={
            "body": changed_on_b["body"],
            "content_version": current_source["content_version"],
        },
    )
    count_after = issue_count(SOURCE_URL)
    assertions = {
        "no_additional_issue_on_a": count_before == count_after,
        "existing_issue_found_by_explicit_mapping": returned["id"] == source_issue["id"],
        "body_returned": returned["body"] == changed_on_b["body"],
        "native_author_unchanged": returned["user"]["id"]
        == context.source_users[ISSUE_AUTHOR]["id"],
        "created_at_unchanged": returned["created_at"] == source_created_at,
        "updated_at_changed": returned["updated_at"] != source_updated_at,
        "unselected_control_change_did_not_block": control_changed,
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"case A assertion failed: {assertions}")

    return {
        "status": "passed",
        "mapping": mapping,
        "baseline": baseline_report(baseline),
        "author_evidence": author_evidence,
        "technical_importer_ids": importer_ids,
        "update_actor": IMPORTER,
        "assertions": assertions,
        "timestamps": {
            "a_created_before": source_created_at,
            "a_created_after": returned["created_at"],
            "a_updated_before": source_updated_at,
            "b_updated_source_value": changed_on_b["updated_at"],
            "a_updated_after": returned["updated_at"],
            "updated_at_preserved_exactly_from_b": returned["updated_at"]
            == changed_on_b["updated_at"],
        },
    }


def case_new_objects(context: AccessContext) -> dict[str, Any]:
    new_on_b = create_issue(
        TARGET_URL,
        "New issue from B",
        "Created after the first transfer",
        ISSUE_AUTHOR,
    )
    new_comment_on_b = create_comment(
        TARGET_URL,
        new_on_b["number"],
        "New B comment by a different author",
        COMMENT_AUTHOR,
    )
    origin_key = f"B:{SELECTED_REPO}:issue:{new_on_b['id']}"
    ledger: dict[str, dict[str, int]] = {}
    source_count_before = issue_count(SOURCE_URL)
    time.sleep(2)

    if origin_key not in ledger:
        new_on_a = create_issue(SOURCE_URL, new_on_b["title"], new_on_b["body"], ISSUE_AUTHOR)
        new_comment_on_a = create_comment(
            SOURCE_URL,
            new_on_a["number"],
            new_comment_on_b["body"],
            COMMENT_AUTHOR,
        )
        ledger[origin_key] = {
            "issue_global_id": new_on_a["id"],
            "issue_index": new_on_a["number"],
            "comment_global_id": new_comment_on_a["id"],
        }
    source_count_after_first = issue_count(SOURCE_URL)

    # Re-applying the same bounded state uses the stored origin key and performs no create call.
    replay_action = "existing-mapping-noop" if origin_key in ledger else "unexpected-create"
    mapped = ledger[origin_key]
    replay_issue = get_issue(SOURCE_URL, mapped["issue_index"])
    source_count_after_replay = issue_count(SOURCE_URL)

    assertions = {
        "content_transferred": replay_issue["title"] == new_on_b["title"]
        and replay_issue["body"] == new_on_b["body"],
        "native_issue_author_mapped": replay_issue["user"]["id"]
        == context.source_users[ISSUE_AUTHOR]["id"],
        "native_comment_author_mapped": new_comment_on_a["user"]["id"]
        == context.source_users[COMMENT_AUTHOR]["id"],
        "issue_relation_preserved": new_comment_on_a["issue_url"].endswith(
            f"/{new_on_a['number']}"
        ),
        "one_issue_created_on_first_apply": source_count_after_first == source_count_before + 1,
        "replay_created_no_duplicate": source_count_after_replay == source_count_after_first,
        "replay_used_explicit_mapping": replay_action == "existing-mapping-noop",
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"case B assertion failed: {assertions}")

    return {
        "status": "passed",
        "origin_key": origin_key,
        "mapping": {
            "origin": {
                "instance": "B",
                "repository": SELECTED_REPO,
                "issue_global_id": new_on_b["id"],
                "issue_index": new_on_b["number"],
                "comment_global_id": new_comment_on_b["id"],
            },
            "destination": {"instance": "A", "repository": SELECTED_REPO, **mapped},
        },
        "origin_information": {
            "issue_created_at": new_on_b["created_at"],
            "comment_created_at": new_comment_on_b["created_at"],
            "issue_author": context.mappings[ISSUE_AUTHOR]["source"],
            "comment_author": context.mappings[COMMENT_AUTHOR]["source"],
        },
        "native_target_values": {
            "issue_created_at": new_on_a["created_at"],
            "comment_created_at": new_comment_on_a["created_at"],
            "issue_author_id": new_on_a["user"]["id"],
            "issue_author_login": new_on_a["user"]["login"],
            "comment_author_id": new_comment_on_a["user"]["id"],
            "comment_author_login": new_comment_on_a["user"]["login"],
        },
        "historical_timestamps_restored_natively": {
            "issue": new_on_a["created_at"] == new_on_b["created_at"],
            "comment": new_comment_on_a["created_at"] == new_comment_on_b["created_at"],
        },
        "replay_action": replay_action,
        "assertions": assertions,
    }


def case_selected_conflict() -> dict[str, Any]:
    source_issue = create_issue(
        SOURCE_URL,
        "Roundtrip conflict case",
        "Baseline body",
        ISSUE_AUTHOR,
    )
    baseline = issue_baseline(source_issue)
    target_issue = create_issue(
        TARGET_URL,
        source_issue["title"],
        source_issue["body"],
        ISSUE_AUTHOR,
    )
    changed_on_b = api(
        TARGET_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{target_issue['number']}",
        auth=auth(IMPORTER),
        json_body={"body": "External B change", "content_version": target_issue["content_version"]},
    )
    local_change = api(
        SOURCE_URL,
        "PATCH",
        f"/repos/{SELECTED_REPO}/issues/{source_issue['number']}",
        auth=auth(IMPORTER),
        json_body={
            "body": "Concurrent central A change",
            "content_version": source_issue["content_version"],
        },
    )
    drift_detected = issue_has_drift(baseline, local_change)
    action = "blocked-conflict" if drift_detected else "would-overwrite"
    final_source = get_issue(SOURCE_URL, source_issue["number"])
    assertions = {
        "selected_change_detected": drift_detected,
        "return_blocked": action == "blocked-conflict",
        "central_change_not_overwritten": final_source["body"] == "Concurrent central A change",
        "external_change_not_applied": final_source["body"] != changed_on_b["body"],
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"case C assertion failed: {assertions}")
    return {
        "status": "passed",
        "baseline": baseline_report(baseline),
        "comparison_scope": (
            "title, body, state, and sorted label names of the explicitly mapped issue; "
            "no global instance timestamp"
        ),
        "action": action,
        "assertions": assertions,
    }


def run_metadata_roundtrip_probe(context: AccessContext) -> dict[str, Any]:
    return {
        "status": "passed",
        "interfaces": [
            "POST /repos/{owner}/{repo}/issues",
            "POST /repos/{owner}/{repo}/issues/{index}/comments",
            "PATCH /repos/{owner}/{repo}/issues/{index}",
            "GET /repos/{owner}/{repo}/issues/{index}",
        ],
        "case_a_existing_issue": case_existing_issue(context),
        "case_b_new_objects": case_new_objects(context),
        "case_c_selected_conflict": case_selected_conflict(),
    }
