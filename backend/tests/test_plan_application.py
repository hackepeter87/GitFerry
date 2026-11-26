from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from gitferry.connection_probe import approve_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.plan_application import prepare_transfer_plan
from gitferry.state_store import OperationBlocked, OriginKey, StateStore
from gitferry.transfer_plans import PlanConflict


def configured_plan(tmp_path: Path) -> tuple[StateStore, dict[str, Any]]:
    state = StateStore(tmp_path / "plans.sqlite")
    state.register_instance("source", "Source", "https://source.invalid", "install-source")
    profiles = ProfileService(state)
    profiles.create(
        ConnectionProfile("target", "Target", "https://target.invalid", "install-target", "admin")
    )
    identity = json.dumps(
        {
            "account_id": "1",
            "login": "technical",
            "ca_digest": "system-trust",
            "certificate_sha256": "cert",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with state.transaction() as connection:
        connection.execute(
            "INSERT INTO connection_checks VALUES (?,?,?,?,?)",
            ("target", 1, identity, identity, "now"),
        )
    approve_connection(
        profiles,
        "target",
        expected_revision=1,
        evidence_digest=hashlib.sha256(identity.encode()).hexdigest(),
    )
    state.register_realm("source-realm", "Source directory", "synthetic")
    for identifier, login in ((2, "issue-author"), (3, "comment-author")):
        state.confirm_identity_binding(
            binding_id=login,
            source_realm_key="source-realm",
            source_login=login,
            source_external_id=f"external-{identifier}",
            source_ou_path="OU=Source",
            target_instance_key="target",
            target_account_id=identifier,
            target_login=login,
            mapping_version=1,
        )
    issue = state.register_origin(OriginKey("source", "org/repo", "issue", "100", 7))
    comment = state.register_origin(
        OriginKey("source", "org/repo", "comment", "200", parent_origin_id=issue.id)
    )
    request = {
        "version": 1,
        "target_instance_key": "target",
        "round_key": "initial",
        "author_credential_refs": {
            "issue-author": "issue-credential",
            "comment-author": "comment-credential",
        },
        "metadata": [
            {
                "origin_id": comment.id,
                "repository": "org/repo",
                "author": {
                    "realm_key": "source-realm",
                    "login": "comment-author",
                    "external_id": "external-3",
                    "ou_path": "OU=Source",
                },
                "values": {"body": "Fähre 🚢\n**Kommentar**"},
            },
            {
                "origin_id": issue.id,
                "repository": "org/repo",
                "author": {
                    "realm_key": "source-realm",
                    "login": "issue-author",
                    "external_id": "external-2",
                    "ou_path": "OU=Source",
                },
                "values": {"title": "Example", "body": "Issue", "state": "open", "labels": []},
            },
        ],
    }
    return state, request


def test_comment_before_issue_uses_existing_journal_and_stable_plan(tmp_path: Path) -> None:
    state, request = configured_plan(tmp_path)
    plan = prepare_transfer_plan(state, request)
    assert [item["operation_type"] for item in plan["operations"]] == [
        "create_issue",
        "create_comment",
    ]
    issue, comment = plan["operations"]
    assert comment["status"] == "blocked_parent"
    assert plan["document"]["dependencies"][comment["operation_id"]] == [issue["operation_id"]]
    assert prepare_transfer_plan(state, request)["plan_id"] == plan["plan_id"]
    assert plan["document"]["inventory"]["inventory_complete"] is False
    assert "token" not in json.dumps(plan)


def test_missing_author_or_parent_never_produces_executable_plan(tmp_path: Path) -> None:
    state, request = configured_plan(tmp_path)
    missing = deepcopy(request)
    missing["author_credential_refs"].pop("comment-author")
    with pytest.raises(OperationBlocked, match="no fallback"):
        prepare_transfer_plan(state, missing)
    missing = deepcopy(request)
    missing["metadata"] = missing["metadata"][:1]
    with pytest.raises(PlanConflict, match="parent"):
        prepare_transfer_plan(state, missing)
    with state.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM transfer_plans").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value", [("status", "confirmed"), ("token", "DO-NOT-PERSIST"), ("version", 2)]
)
def test_untrusted_execution_fields_are_rejected(tmp_path: Path, field: str, value: Any) -> None:
    state, request = configured_plan(tmp_path)
    request[field] = value
    with pytest.raises(PlanConflict, match="invalid plan selection"):
        prepare_transfer_plan(state, request)
    assert b"DO-NOT-PERSIST" not in state.path.read_bytes()


def test_selected_parent_repository_is_checked_before_plan_publication(tmp_path: Path) -> None:
    state, request = configured_plan(tmp_path)
    request["metadata"][0]["repository"] = "different/repo"
    with pytest.raises(PlanConflict, match="different repositories"):
        prepare_transfer_plan(state, request)
    with state.read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM transfer_plans").fetchone()[0] == 0


def test_label_and_attachment_operations_follow_repository_and_issue_dependencies(
    tmp_path: Path,
) -> None:
    state, request = configured_plan(tmp_path)
    label = state.register_origin(OriginKey("source", "org/repo", "label", "300"))
    issue = state.get_origin(request["metadata"][1]["origin_id"])
    attachment = state.register_origin(
        OriginKey(
            "source",
            "org/repo",
            "attachment",
            "400",
            parent_origin_id=issue.id,
        )
    )
    request["package_ref"] = "package-1"
    request["package_digest"] = "a" * 64
    request["metadata"][1]["values"]["labels"] = ["priority"]
    request["content"] = [
        {
            "origin_id": attachment.id,
            "repository": "org/repo",
            "kind": "attachment",
            "values": {
                "name": "asset.txt",
                "size": 5,
                "sha256": hashlib.sha256(b"asset").hexdigest(),
                "media_type": "text/plain",
                "member": "assets/400",
                "parent_origin_id": issue.id,
            },
        },
        {
            "origin_id": label.id,
            "repository": "org/repo",
            "kind": "label",
            "values": {
                "name": "priority",
                "color": "112233",
                "description": "Synthetic",
            },
        },
    ]
    plan = prepare_transfer_plan(state, request)
    by_type = {item["operation_type"]: item for item in plan["operations"]}
    assert set(by_type) == {
        "create_label",
        "create_issue",
        "create_comment",
        "upload_attachment",
    }
    dependencies = plan["document"]["dependencies"]
    assert (
        by_type["create_label"]["operation_id"]
        in dependencies[by_type["create_issue"]["operation_id"]]
    )
    assert (
        by_type["create_issue"]["operation_id"]
        in dependencies[by_type["upload_attachment"]["operation_id"]]
    )
