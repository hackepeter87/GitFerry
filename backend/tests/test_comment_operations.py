from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gitferry.comment_operations import (
    execute_comment_operation,
    prepare_comment_operation,
    resolve_unknown_comment_operation,
)
from gitferry.gitea_comments import CommentTargetConflict
from gitferry.operations import OperationBlocked, OperationExecutor, OutcomeUnknownError
from gitferry.state_store import OriginKey, StateStore

AUTHOR = {
    "realm_key": "realm-a",
    "login": "comment-author-a",
    "external_id": "SYNTHETIC-A-2001",
    "ou_path": "OU=People,DC=a,DC=invalid",
}


class FakeComments:
    authenticated_login = "comment-author-b"

    def __init__(self) -> None:
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.posts = 0
        self.patches = 0
        self.fail_after_create = False

    def _value(self, identifier: int, issue: int, body: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "body": body,
            "issue_url": f"http://b.invalid/api/v1/repos/org/repo/issues/{issue}",
            "created_at": "2026-11-20T10:00:00Z",
            "user": {"id": 52, "login": self.authenticated_login},
        }

    def verified_comment(
        self, repository: str, issue_index: int, comment_global_id: str
    ) -> dict[str, Any]:
        assert repository == "org/repo"
        matches = [
            value
            for value in self.comments.get(issue_index, [])
            if str(value["id"]) == comment_global_id
        ]
        if len(matches) != 1:
            raise CommentTargetConflict("not on expected issue")
        return matches[0]

    def create_comment(self, repository: str, issue_index: int, body: str) -> dict[str, Any]:
        self.posts += 1
        value = self._value(100 + self.posts, issue_index, body)
        self.comments.setdefault(issue_index, []).append(value)
        if self.fail_after_create:
            raise OutcomeUnknownError("response lost after accepted POST")
        return value

    def update_comment(self, repository: str, comment_global_id: str, body: str) -> dict[str, Any]:
        self.patches += 1
        for values in self.comments.values():
            for value in values:
                if str(value["id"]) == comment_global_id:
                    value["body"] = body
                    return value
        raise AssertionError("unknown target")


def configured(tmp_path: Path, *, with_parent_mapping: bool = True) -> tuple[StateStore, str, str]:
    state = StateStore(tmp_path / "comments.sqlite3")
    state.register_instance("instance-a", "A", "http://a.invalid", "install-a")
    state.register_instance("instance-b", "B", "http://b.invalid", "install-b")
    state.register_realm("realm-a", "Directory A", "directory-a")
    state.confirm_identity_binding(
        binding_id="binding-comment-author-a-to-b",
        source_realm_key="realm-a",
        source_login="comment-author-a",
        source_external_id="SYNTHETIC-A-2001",
        source_ou_path="OU=People,DC=a,DC=invalid",
        target_instance_key="instance-b",
        target_account_id=52,
        target_login="comment-author-b",
        mapping_version=1,
    )
    parent = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    comment = state.register_origin(
        OriginKey(
            "instance-a",
            "org/repo",
            "comment",
            "31",
            parent_origin_id=parent.id,
            origin_created_at="2026-11-20T09:00:00Z",
        )
    )
    if with_parent_mapping:
        state.add_object_mapping(parent.id, "instance-b", "org/repo", "71", target_issue_index=8)
    return state, parent.id, comment.id


def test_missing_author_mapping_never_falls_back_to_importer(tmp_path: Path) -> None:
    state, _, comment = configured(tmp_path)
    unknown = {**AUTHOR, "external_id": "SYNTHETIC-UNMAPPED"}

    with pytest.raises(OperationBlocked, match="no exact locally confirmed"):
        prepare_comment_operation(
            state,
            origin_id=comment,
            target_instance_key="instance-b",
            target_repository_scope="org/repo",
            operation_type="create_comment",
            body="No fallback",
            author=unknown,
        )


def test_parent_block_survives_until_confirmed_mapping_and_no_early_post(tmp_path: Path) -> None:
    state, parent, comment = configured(tmp_path, with_parent_mapping=False)
    client = FakeComments()
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_comment",
        body="Child after parent",
        author=AUTHOR,
    )
    assert operation.status == "blocked_parent"
    with OperationExecutor(state, "executor-blocked") as executor:
        with pytest.raises(OperationBlocked):
            execute_comment_operation(state, executor, client, operation.operation_id)
    assert client.posts == 0

    state.add_object_mapping(parent, "instance-b", "org/repo", "71", target_issue_index=8)
    reopened = StateStore(state.path)
    resumed = prepare_comment_operation(
        reopened,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_comment",
        body="Child after parent",
        author=AUTHOR,
    )
    with OperationExecutor(reopened, "executor-resumed") as executor:
        result = execute_comment_operation(reopened, executor, client, resumed.operation_id)
    assert result.status == "confirmed"
    assert client.posts == 1


def test_confirmed_comment_create_restarts_without_duplicate(tmp_path: Path) -> None:
    state, _, comment = configured(tmp_path)
    client = FakeComments()
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_comment",
        body="Unicode Fähre 🚢\n\n**Markdown**",
        author=AUTHOR,
    )
    with OperationExecutor(state, "first") as executor:
        execute_comment_operation(state, executor, client, operation.operation_id)
    reopened = StateStore(state.path)
    with OperationExecutor(reopened, "restart") as executor:
        execute_comment_operation(reopened, executor, client, operation.operation_id)
    assert client.posts == 1


def test_unknown_comment_create_is_not_reposted_and_resolution_checks_parent(
    tmp_path: Path,
) -> None:
    state, _, comment = configured(tmp_path)
    client = FakeComments()
    client.fail_after_create = True
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_comment",
        body="May already exist",
        author=AUTHOR,
    )
    with OperationExecutor(state, "lost") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            execute_comment_operation(state, executor, client, operation.operation_id)
    client.fail_after_create = False
    with OperationExecutor(state, "restart") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            execute_comment_operation(state, executor, client, operation.operation_id)
    assert client.posts == 1

    same_text_wrong_parent = client._value(999, 9, "May already exist")
    client.comments.setdefault(9, []).append(same_text_wrong_parent)
    with pytest.raises(CommentTargetConflict):
        resolve_unknown_comment_operation(
            state, client, operation.operation_id, target_comment_id="999"
        )
    resolved = resolve_unknown_comment_operation(
        state, client, operation.operation_id, target_comment_id="101"
    )
    assert resolved.status == "confirmed"


def test_update_checks_parent_and_baseline_then_reuses_comment_id(tmp_path: Path) -> None:
    state, _, comment = configured(tmp_path)
    state.add_object_mapping(comment, "instance-b", "org/repo", "101", target_issue_index=8)
    client = FakeComments()
    client.comments[8] = [client._value(101, 8, "Before")]
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="update_comment",
        body="After",
        expected_body="Before",
        author=AUTHOR,
    )
    with OperationExecutor(state, "update") as executor:
        result = execute_comment_operation(state, executor, client, operation.operation_id)
    assert result.status == "confirmed"
    assert result.target_global_id == "101"
    assert client.patches == 1

    next_operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="update_comment",
        body="After again",
        expected_body="After",
        author=AUTHOR,
    )
    with OperationExecutor(state, "update-again") as executor:
        repeated = execute_comment_operation(state, executor, client, next_operation.operation_id)
    assert repeated.target_global_id == "101"
    assert client.patches == 2


def test_stale_or_wrong_parent_update_conflicts_without_patch(tmp_path: Path) -> None:
    state, _, comment = configured(tmp_path)
    state.add_object_mapping(comment, "instance-b", "org/repo", "101", target_issue_index=8)
    client = FakeComments()
    client.comments[8] = [client._value(101, 8, "Newer external edit")]
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="update_comment",
        body="Stale desired",
        expected_body="Before",
        author=AUTHOR,
    )
    with OperationExecutor(state, "stale") as executor:
        result = execute_comment_operation(state, executor, client, operation.operation_id)
    assert result.status == "conflict"
    assert client.patches == 0


@pytest.mark.parametrize("wrong_parent", (False, True))
def test_missing_or_differently_parented_target_is_not_recreated(
    tmp_path: Path, wrong_parent: bool
) -> None:
    state, _, comment = configured(tmp_path)
    state.add_object_mapping(comment, "instance-b", "org/repo", "101", target_issue_index=8)
    client = FakeComments()
    if wrong_parent:
        client.comments[9] = [client._value(101, 9, "Before")]
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="update_comment",
        body="After",
        expected_body="Before",
        author=AUTHOR,
    )

    with OperationExecutor(state, f"target-missing-{wrong_parent}") as executor:
        result = execute_comment_operation(state, executor, client, operation.operation_id)

    assert result.status == "conflict"
    assert client.posts == 0
    assert client.patches == 0


def test_identical_text_comments_keep_distinct_origins_and_targets(tmp_path: Path) -> None:
    state, parent, first = configured(tmp_path)
    second = state.register_origin(
        OriginKey("instance-a", "org/repo", "comment", "32", parent_origin_id=parent)
    )
    client = FakeComments()
    operations = [
        prepare_comment_operation(
            state,
            origin_id=origin,
            target_instance_key="instance-b",
            target_repository_scope="org/repo",
            operation_type="create_comment",
            body="Same text",
            author=AUTHOR,
        )
        for origin in (first, second.id)
    ]
    for index, operation in enumerate(operations):
        with OperationExecutor(state, f"executor-{index}") as executor:
            execute_comment_operation(state, executor, client, operation.operation_id)
    assert client.posts == 2
    assert (
        state.get_object_mapping(first, "instance-b").target_global_id
        != state.get_object_mapping(second.id, "instance-b").target_global_id
    )


def test_unknown_resolution_rejects_same_login_with_wrong_native_author(tmp_path: Path) -> None:
    state, _, comment = configured(tmp_path)
    client = FakeComments()
    client.fail_after_create = True
    operation = prepare_comment_operation(
        state,
        origin_id=comment,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_comment",
        body="same content",
        author=AUTHOR,
    )
    with OperationExecutor(state, "lost") as executor, pytest.raises(OperationBlocked):
        execute_comment_operation(state, executor, client, operation.operation_id)
    client.comments[8][0]["user"]["id"] = 999
    with pytest.raises(CommentTargetConflict, match="author ID"):
        resolve_unknown_comment_operation(
            state, client, operation.operation_id, target_comment_id="101"
        )
    assert state.get_operation(operation.operation_id).status == "outcome_unknown"
    assert client.posts == 1
