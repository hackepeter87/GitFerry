"""Side-local Manifest v2 issue-comment roundtrip and restart worker."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from gitferry.comment_operations import execute_comment_operation, prepare_comment_operation
from gitferry.gitea_comments import GiteaCommentClient, verify_comment_facts
from gitferry.operations import (
    OperationBlocked,
    OperationExecutor,
    OutcomeUnknownError,
    TargetConfirmation,
)
from gitferry.state_store import OriginKey, StateStore
from gitferry.transport import load_transport, write_transport
from gitferry.transport_state import (
    admit_transport,
    confirm_import_observation,
    get_import_observation,
    record_export,
)

INSTANCE_A = "gitea-a-portable"
INSTANCE_B = "gitea-b-portable"
REPOSITORY = "access-lab/selected-private"
DATABASE = Path("/state/gitferry.sqlite3")
FORWARD_OUT = Path("/state/outbox/comments-forward")
FORWARD_IN = Path("/state/inbox/comments-forward")
RETURN_OUT = Path("/state/outbox/comments-return")
RETURN_IN = Path(os.environ.get("GITFERRY_COMMENT_RETURN_PATH", "/state/inbox/comments-return"))
ROUND = "round-comments-v2"
SYNC_DIRECTORY = Path("/state/probe-sync")
AUTHOR_A = {
    "realm_key": "synthetic-directory-a",
    "login": "comment-author",
    "external_id": "SYNTHETIC-A-COMMENT-1",
    "ou_path": "OU=People,DC=a,DC=invalid",
}
AUTHOR_B = {
    "realm_key": "synthetic-directory-b",
    "login": "comment-author-b",
    "external_id": "SYNTHETIC-B-COMMENT-1",
    "ou_path": "OU=People,DC=b,DC=invalid",
}


def marker(name: str) -> str:
    return f"{name}-{os.environ['GITFERRY_IT_INSTANCE_NONCE']}"


def write_remote_acceptance_marker(name: str, value: dict[str, Any]) -> None:
    """Persist test-only evidence after Gitea returned an accepted comment."""

    SYNC_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = SYNC_DIRECTORY / f"{name}.json"
    temporary = destination.with_suffix(f".json.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    directory = os.open(SYNC_DIRECTORY, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_remote_acceptance_marker(name: str) -> dict[str, Any]:
    path = SYNC_DIRECTORY / f"{name}.json"
    if not path.is_file():
        raise RuntimeError(f"remote acceptance synchronization marker is missing: {name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("remote acceptance synchronization marker is invalid")
    return value


def request(
    method: str, path: str, body: dict[str, Any] | None = None, *, user: str = "issue-author"
) -> Any:
    response = httpx.request(
        method,
        os.environ["GITFERRY_GITEA_URL"] + "/api/v1" + path,
        auth=(user, os.environ["GITFERRY_IT_USER_PASSWORD"]),
        json=body,
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gitea {method} {path}: {response.status_code} {response.text}")
    return response.json() if response.content else None


def state_store() -> StateStore:
    state = StateStore(DATABASE)
    state.register_instance(INSTANCE_A, "Gitea A", "http://source:3000", marker("source"))
    state.register_instance(INSTANCE_B, "Gitea B", "http://target:3000", marker("target"))
    side = os.environ["GITFERRY_SIDE"]
    local_instance = INSTANCE_A if side == "A" else INSTANCE_B
    state.register_realm("synthetic-directory-a", "Synthetic directory A", "directory-a")
    state.register_realm("synthetic-directory-b", "Synthetic directory B", "directory-b")
    for author in (AUTHOR_A, AUTHOR_B):
        user = request("GET", f"/users/{author['login']}")
        state.confirm_identity_binding(
            binding_id=f"binding-{author['realm_key']}-to-{local_instance}",
            source_realm_key=author["realm_key"],
            source_login=author["login"],
            source_external_id=author["external_id"],
            source_ou_path=author["ou_path"],
            target_instance_key=local_instance,
            target_account_id=int(user["id"]),
            target_login=author["login"],
            mapping_version=1,
        )
    return state


def client(login: str) -> GiteaCommentClient:
    return GiteaCommentClient(
        os.environ["GITFERRY_GITEA_URL"],
        username=login,
        password=os.environ["GITFERRY_IT_USER_PASSWORD"],
    )


def issue_values(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": value["title"],
        "body": value["body"],
        "state": value["state"],
        "labels": sorted(label["name"] for label in value["labels"]),
    }


def issue_replica(instance: str, mapping: Any) -> dict[str, Any]:
    return {
        "instance_key": instance,
        "repository": REPOSITORY,
        "global_id": mapping.target_global_id,
        "issue_index": str(mapping.target_issue_index),
        "native_created_at": mapping.target_native_created_at,
    }


def comment_replica(instance: str, mapping: Any) -> dict[str, Any]:
    return {
        "instance_key": instance,
        "repository": REPOSITORY,
        "global_id": mapping.target_global_id,
        "parent_issue_index": str(mapping.target_issue_index),
        "native_created_at": mapping.target_native_created_at,
    }


def issue_item(
    origin: Any,
    mapping: Any,
    values: dict[str, Any],
    *,
    baseline: dict[str, Any] | None,
    confirmed: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "origin": {
            "instance_key": origin.key.origin_instance_key,
            "repository": REPOSITORY,
            "object_type": "issue",
            "global_id": origin.key.origin_global_id,
            "issue_index": str(origin.key.issue_index),
            "created_at": origin.key.origin_created_at,
        },
        "replicas": [issue_replica(mapping.target_instance_key, mapping)],
        "comparison": {
            "ruleset": "gitea-issue-selected-fields",
            "ruleset_version": 1,
            "original_baseline": baseline,
            "confirmed_import": confirmed,
            "current": values,
            "baseline_origin": {
                "instance_key": origin.key.origin_instance_key,
                "transfer_round_id": ROUND,
            },
        },
    }


def comment_item(
    origin: Any,
    mapping: Any,
    parent: Any,
    author: dict[str, str],
    body: str,
    *,
    baseline: dict[str, str] | None,
    confirmed: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        "origin": {
            "instance_key": origin.key.origin_instance_key,
            "repository": REPOSITORY,
            "object_type": "issue_comment",
            "global_id": origin.key.origin_global_id,
            "parent_issue_origin": {
                "instance_key": parent.key.origin_instance_key,
                "repository": REPOSITORY,
                "object_type": "issue",
                "global_id": parent.key.origin_global_id,
            },
            "created_at": origin.key.origin_created_at,
        },
        "replicas": [comment_replica(mapping.target_instance_key, mapping)],
        "author": author,
        "comparison": {
            "ruleset": "gitea-issue-comment-body",
            "ruleset_version": 1,
            "original_baseline": baseline,
            "confirmed_import": confirmed,
            "current": {"body": body},
            "baseline_origin": {
                "instance_key": origin.key.origin_instance_key,
                "transfer_round_id": ROUND,
            },
        },
    }


def header(manifest_id: str, sender: str, recipient: str, mode: str) -> dict[str, Any]:
    return {
        "format": "gitferry.transport",
        "schema_version": 2,
        "manifest_id": manifest_id,
        "transfer_round_id": ROUND,
        "producer_version": "0.1.0.dev0",
        "sender": {
            "instance_key": sender,
            "installation_marker": marker("source" if sender == INSTANCE_A else "target"),
        },
        "recipient": {
            "instance_key": recipient,
            "installation_marker": marker("source" if recipient == INSTANCE_A else "target"),
            "context": f"isolated GitFerry {recipient} state",
        },
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "deletion_semantics": "absence-does-not-delete",
        "selected_repositories": [
            {
                "repository": REPOSITORY,
                "object_types": ["issue", "issue_comment"],
                "fields": ["title", "body", "state", "labels", "comment.body"],
                "capabilities": [
                    "issue-create",
                    "issue-update",
                    "issue-comment-create",
                    "issue-comment-update",
                ],
                "limitations": [
                    "normal-issue-comments-only",
                    "native-created-at-not-restored",
                    "comment-update-preflight-is-not-atomic-with-patch",
                ],
            }
        ],
        "identity_claims": [],
        "payloads": [],
    }


def register_local(
    state: StateStore, instance: str, issue: dict[str, Any], comments: list[dict[str, Any]]
) -> tuple[Any, list[Any]]:
    parent = state.register_origin(
        OriginKey(
            instance,
            REPOSITORY,
            "issue",
            str(issue["id"]),
            issue_index=int(issue["number"]),
            origin_created_at=issue["created_at"],
        )
    )
    state.add_object_mapping(
        parent.id,
        instance,
        REPOSITORY,
        str(issue["id"]),
        target_issue_index=int(issue["number"]),
        target_native_created_at=issue["created_at"],
    )
    origins = []
    for value in comments:
        origin = state.register_origin(
            OriginKey(
                instance,
                REPOSITORY,
                "comment",
                str(value["id"]),
                parent_origin_id=parent.id,
                origin_created_at=value["created_at"],
            )
        )
        state.add_object_mapping(
            origin.id,
            instance,
            REPOSITORY,
            str(value["id"]),
            target_issue_index=int(issue["number"]),
            target_native_created_at=value["created_at"],
        )
        origins.append(origin)
    return parent, origins


def seed_forward(state: StateStore) -> dict[str, Any]:
    for name in ("hard-crash", "lost-response"):
        (SYNC_DIRECTORY / f"{name}.json").unlink(missing_ok=True)
    issue = request(
        "POST",
        f"/repos/{REPOSITORY}/issues",
        {"title": "Manifest v2 comment parent", "body": "A issue for comment roundtrip"},
    )
    bodies = [
        "Existing A comment — Fähre 🚢\n\n**Markdown**",
        "Crash-window comment",
        "Lost-response comment",
    ]
    comments = [
        request(
            "POST",
            f"/repos/{REPOSITORY}/issues/{issue['number']}/comments",
            {"body": body},
            user="comment-author",
        )
        for body in bodies
    ]
    parent, origins = register_local(state, INSTANCE_A, issue, comments)
    state.create_transfer_round(ROUND, INSTANCE_A, INSTANCE_B, REPOSITORY)
    iv = issue_values(issue)
    state.create_baseline(
        baseline_id="comments-a-issue",
        transfer_round_id=ROUND,
        origin_id=parent.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline=iv,
        desired=iv,
        observed=iv,
        origin_created_at=issue["created_at"],
        target_native_created_at=issue["created_at"],
    )
    for index, (origin, value) in enumerate(zip(origins, comments)):
        body = {"body": value["body"]}
        state.create_baseline(
            baseline_id=f"comments-a-{index}",
            transfer_round_id=ROUND,
            origin_id=origin.id,
            ruleset_name="gitea-issue-comment-body",
            ruleset_version=1,
            baseline=body,
            desired=body,
            observed=body,
            origin_created_at=value["created_at"],
            target_native_created_at=value["created_at"],
        )
    document = header("comments-v2-forward-1", INSTANCE_A, INSTANCE_B, "forward")
    document["objects"] = [
        issue_item(
            parent, state.get_object_mapping(parent.id, INSTANCE_A), iv, baseline=iv, confirmed=None
        )
    ] + [
        comment_item(
            origin,
            state.get_object_mapping(origin.id, INSTANCE_A),
            parent,
            AUTHOR_A,
            value["body"],
            baseline={"body": value["body"]},
            confirmed=None,
        )
        for origin, value in zip(origins, comments)
    ]
    artifact = write_transport(FORWARD_OUT, document)
    report = record_export(state, artifact, local_instance_key=INSTANCE_A)
    return {
        "manifest_id": report.manifest_id,
        "digest_sha256": report.digest_sha256,
        "origin_ids": list(report.origin_ids),
        "issue": issue,
        "comments": comments,
    }


def create_issue(values: dict[str, Any]) -> TargetConfirmation:
    issue = request(
        "POST",
        f"/repos/{REPOSITORY}/issues",
        {"title": values["title"], "body": values["body"], "closed": values["state"] == "closed"},
    )
    return TargetConfirmation(str(issue["id"]), int(issue["number"]), issue["created_at"])


def import_forward(state: StateStore) -> dict[str, Any]:
    artifact = load_transport(FORWARD_IN)
    report = admit_transport(state, artifact, local_instance_key=INSTANCE_B)
    issue_item_value, comment_value = artifact.document["objects"][:2]
    issue_origin, comment_origin = report.origin_ids[:2]
    state.prepare_operation(
        operation_id=f"comments-forward-issue-{issue_origin}",
        origin_id=issue_origin,
        target_instance_key=INSTANCE_B,
        target_repository_scope=REPOSITORY,
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent=issue_item_value["comparison"]["current"],
    )
    with OperationExecutor(state, f"comments-forward-issue-{os.getpid()}") as executor:
        issue_operation = executor.execute(f"comments-forward-issue-{issue_origin}", create_issue)
    comment_operation = prepare_comment_operation(
        state,
        origin_id=comment_origin,
        target_instance_key=INSTANCE_B,
        target_repository_scope=REPOSITORY,
        operation_type="create_comment",
        body=comment_value["comparison"]["current"]["body"],
        author=comment_value["author"],
    )
    with (
        client("comment-author") as comments,
        OperationExecutor(state, f"comments-forward-comment-{os.getpid()}") as executor,
    ):
        result = execute_comment_operation(
            state, executor, comments, comment_operation.operation_id
        )
        target_comment = comments.verified_comment(
            REPOSITORY, int(result.target_issue_index), str(result.target_global_id)
        )
    target_issue = request(
        "GET", f"/repos/{REPOSITORY}/issues/{issue_operation.target_issue_index}"
    )
    confirm_import_observation(
        state,
        forward_manifest_id=artifact.document["manifest_id"],
        origin_identifier=issue_origin,
        observed=issue_values(target_issue),
        target_native_created_at=target_issue["created_at"],
    )
    confirm_import_observation(
        state,
        forward_manifest_id=artifact.document["manifest_id"],
        origin_identifier=comment_origin,
        observed={"body": target_comment["body"]},
        target_native_created_at=target_comment["created_at"],
    )
    return {
        "admission": report.status,
        "issue": target_issue,
        "comment": target_comment,
        "origin_ids": list(report.origin_ids),
    }


def unknown_origin(state: StateStore, index: int) -> tuple[str, dict[str, Any]]:
    artifact = load_transport(FORWARD_IN)
    report = admit_transport(state, artifact, local_instance_key=INSTANCE_B)
    return report.origin_ids[index], artifact.document["objects"][index]


def run_unknown(state: StateStore, index: int, *, hard_exit: bool) -> dict[str, Any]:
    origin, item = unknown_origin(state, index)
    operation = prepare_comment_operation(
        state,
        origin_id=origin,
        target_instance_key=INSTANCE_B,
        target_repository_scope=REPOSITORY,
        operation_type="create_comment",
        body=item["comparison"]["current"]["body"],
        author=item["author"],
    )
    comments = client("comment-author")
    marker_name = "hard-crash" if hard_exit else "lost-response"

    class RecordedResponse:
        authenticated_login = comments.authenticated_login

        def verified_comment(self, *args: Any) -> dict[str, Any]:
            return comments.verified_comment(*args)

        def update_comment(self, *args: Any) -> dict[str, Any]:
            return comments.update_comment(*args)

        def create_comment(self, repository: str, issue_index: int, body: str) -> dict[str, Any]:
            created = comments.create_comment(repository, issue_index, body)
            write_remote_acceptance_marker(
                marker_name,
                {
                    "remote_accepted": True,
                    "repository": repository,
                    "parent_issue_index": issue_index,
                    "comment_global_id": str(created["id"]),
                    "author": created["user"]["login"],
                    "body": created["body"],
                },
            )
            return created

    if hard_exit:
        with OperationExecutor(
            state, "comment-hard-crash", after_remote_hook=lambda: os._exit(91)
        ) as executor:
            execute_comment_operation(state, executor, RecordedResponse(), operation.operation_id)
        raise AssertionError("unreachable")

    class LostResponse(RecordedResponse):
        def create_comment(self, repository: str, issue_index: int, body: str) -> dict[str, Any]:
            super().create_comment(repository, issue_index, body)
            raise OutcomeUnknownError("synthetic lost HTTP response after accepted comment POST")

    try:
        with OperationExecutor(state, f"comment-lost-{os.getpid()}") as executor:
            execute_comment_operation(state, executor, LostResponse(), operation.operation_id)
    except OperationBlocked:
        pass
    comments.close()
    return {
        "operation_id": operation.operation_id,
        "status": state.get_operation(operation.operation_id).status,
    }


def recover_unknown(state: StateStore, index: int) -> dict[str, Any]:
    origin, item = unknown_origin(state, index)
    operation = prepare_comment_operation(
        state,
        origin_id=origin,
        target_instance_key=INSTANCE_B,
        target_repository_scope=REPOSITORY,
        operation_type="create_comment",
        body=item["comparison"]["current"]["body"],
        author=item["author"],
    )
    parent_origin_id = state.get_origin(origin).key.parent_origin_id
    if parent_origin_id is None:
        raise RuntimeError("unknown probe comment has no parent")
    parent_issue_index = state.get_object_mapping(parent_origin_id, INSTANCE_B).target_issue_index
    marker_name = "hard-crash" if index == 2 else "lost-response"
    synchronization = read_remote_acceptance_marker(marker_name)
    if (
        synchronization.get("repository") != REPOSITORY
        or synchronization.get("parent_issue_index") != parent_issue_index
    ):
        raise RuntimeError("remote acceptance marker points at another repository or parent")
    with client("comment-author") as evidence_client:
        accepted = evidence_client.verified_comment(
            REPOSITORY,
            int(parent_issue_index),
            str(synchronization["comment_global_id"]),
        )
        verify_comment_facts(
            accepted,
            issue_index=int(parent_issue_index),
            expected_body=str(item["comparison"]["current"]["body"]),
            expected_author_login="comment-author",
        )
    before = len(
        request(
            "GET",
            f"/repos/{REPOSITORY}/issues/{parent_issue_index}/comments",
            user="comment-author",
        )
    )
    with (
        client("comment-author") as comments,
        OperationExecutor(state, f"comment-recover-{os.getpid()}") as executor,
    ):
        try:
            execute_comment_operation(state, executor, comments, operation.operation_id)
        except OperationBlocked:
            pass
    after = len(
        request(
            "GET",
            f"/repos/{REPOSITORY}/issues/{parent_issue_index}/comments",
            user="comment-author",
        )
    )
    record = state.get_operation(operation.operation_id)
    return {
        "operation_id": operation.operation_id,
        "status": record.status,
        "target_global_id": record.target_global_id,
        "count_before": before,
        "count_after": after,
        "synchronization_evidence": {
            **synchronization,
            "live_comment_verified_after_restart": True,
            "target_id_not_supplied_to_gitferry_recovery": True,
        },
    }


def modify_return(state: StateStore, manifest_id: str = "comments-v2-return-1") -> dict[str, Any]:
    forward = load_transport(FORWARD_IN)
    admitted = admit_transport(state, forward, local_instance_key=INSTANCE_B)
    issue_origin_id, main_origin_id = admitted.origin_ids[:2]
    issue_origin = state.get_origin(issue_origin_id)
    issue_mapping = state.get_object_mapping(issue_origin_id, INSTANCE_B)
    main_mapping = state.get_object_mapping(main_origin_id, INSTANCE_B)
    changed_body = "Changed on B — Unicode retained 🚢\n\n*updated Markdown*"
    with client("comment-author") as comments:
        comments.update_comment(REPOSITORY, main_mapping.target_global_id, changed_body)
    new_same_parent = request(
        "POST",
        f"/repos/{REPOSITORY}/issues/{issue_mapping.target_issue_index}/comments",
        {"body": "New B comment on A-origin issue"},
        user="comment-author-b",
    )
    same_origin = state.register_origin(
        OriginKey(
            INSTANCE_B,
            REPOSITORY,
            "comment",
            str(new_same_parent["id"]),
            parent_origin_id=issue_origin_id,
            origin_created_at=new_same_parent["created_at"],
        )
    )
    state.add_object_mapping(
        same_origin.id,
        INSTANCE_B,
        REPOSITORY,
        str(new_same_parent["id"]),
        target_issue_index=int(issue_mapping.target_issue_index),
        target_native_created_at=new_same_parent["created_at"],
    )
    new_issue = request(
        "POST",
        f"/repos/{REPOSITORY}/issues",
        {"title": "New B issue with child", "body": "B-origin issue"},
    )
    new_comment = request(
        "POST",
        f"/repos/{REPOSITORY}/issues/{new_issue['number']}/comments",
        {"body": "New B child before parent in manifest"},
        user="comment-author-b",
    )
    new_parent, new_children = register_local(state, INSTANCE_B, new_issue, [new_comment])
    existing_forward = forward.document["objects"][1]
    objects = [
        comment_item(
            new_children[0],
            state.get_object_mapping(new_children[0].id, INSTANCE_B),
            new_parent,
            AUTHOR_B,
            new_comment["body"],
            baseline=None,
            confirmed=None,
        ),
        {
            **existing_forward,
            "replicas": [*existing_forward["replicas"], comment_replica(INSTANCE_B, main_mapping)],
            "comparison": {
                **existing_forward["comparison"],
                "confirmed_import": get_import_observation(
                    state,
                    forward_manifest_id=forward.document["manifest_id"],
                    origin_identifier=main_origin_id,
                ),
                "current": {"body": changed_body},
            },
        },
        comment_item(
            same_origin,
            state.get_object_mapping(same_origin.id, INSTANCE_B),
            issue_origin,
            AUTHOR_B,
            new_same_parent["body"],
            baseline=None,
            confirmed=None,
        ),
        issue_item(
            new_parent,
            state.get_object_mapping(new_parent.id, INSTANCE_B),
            issue_values(new_issue),
            baseline=None,
            confirmed=None,
        ),
    ]
    document = header(manifest_id, INSTANCE_B, INSTANCE_A, "return")
    document["forward_reference"] = {
        "manifest_id": forward.document["manifest_id"],
        "digest_sha256": forward.digest_sha256,
    }
    document["objects"] = objects
    output = (
        RETURN_OUT
        if manifest_id == "comments-v2-return-1"
        else Path("/state/outbox/comments-repackaged")
    )
    artifact = write_transport(output, document)
    report = record_export(state, artifact, local_instance_key=INSTANCE_B)
    return {
        "manifest_id": report.manifest_id,
        "digest_sha256": report.digest_sha256,
        "origin_ids": list(report.origin_ids),
        "changed_body": changed_body,
    }


def repackage_return(state: StateStore) -> dict[str, Any]:
    original = load_transport(RETURN_OUT)
    document = {
        **original.document,
        "manifest_id": "comments-v2-return-repackaged",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact = write_transport(Path("/state/outbox/comments-repackaged"), document)
    report = record_export(state, artifact, local_instance_key=INSTANCE_B)
    return {
        "manifest_id": report.manifest_id,
        "digest_sha256": report.digest_sha256,
        "origin_ids": list(report.origin_ids),
    }


def preflight_return(state: StateStore, artifact: Any, report: Any) -> list[str]:
    drift = []
    for item, origin_id in zip(artifact.document["objects"], report.origin_ids):
        if (
            item["origin"]["object_type"] != "issue_comment"
            or item["origin"]["instance_key"] != INSTANCE_A
        ):
            continue
        mapping = state.get_object_mapping(origin_id, INSTANCE_A)
        with client(item["author"]["login"]) as comments:
            current = comments.verified_comment(
                REPOSITORY, int(mapping.target_issue_index), mapping.target_global_id
            )
        operation_id_prefix = f"update_comment-{origin_id[-16:]}-"
        confirmed = False
        with state.transaction() as connection:
            confirmed = (
                connection.execute(
                    "SELECT 1 FROM operations WHERE operation_id LIKE ? AND status = 'confirmed'",
                    (operation_id_prefix + "%",),
                ).fetchone()
                is not None
            )
        expected = (
            item["comparison"]["current"] if confirmed else item["comparison"]["original_baseline"]
        )
        if current["body"] != expected["body"]:
            drift.append(origin_id)
    return drift


def apply_return(state: StateStore) -> tuple[int, dict[str, Any]]:
    artifact = load_transport(RETURN_IN)
    report = admit_transport(state, artifact, local_instance_key=INSTANCE_A)
    drift = preflight_return(state, artifact, report)
    if drift:
        return 5, {"status": "blocked", "drift_origins": drift, "admission": report.status}
    pairs = list(zip(artifact.document["objects"], report.origin_ids))
    outcomes: list[dict[str, Any]] = []
    for item, origin_id in pairs:
        if item["origin"]["object_type"] != "issue":
            continue
        values = item["comparison"]["current"]
        state.prepare_operation(
            operation_id=f"comments-return-issue-{origin_id}",
            origin_id=origin_id,
            target_instance_key=INSTANCE_A,
            target_repository_scope=REPOSITORY,
            operation_type="create_issue",
            mapping_version=1,
            baseline_id=None,
            intent=values,
        )
        with OperationExecutor(state, f"comments-return-issue-{os.getpid()}") as executor:
            result = executor.execute(f"comments-return-issue-{origin_id}", create_issue)
        outcomes.append(
            {
                "origin_id": origin_id,
                "type": "issue",
                "status": result.status,
                "target_global_id": result.target_global_id,
                "target_issue_index": result.target_issue_index,
            }
        )
    for item, origin_id in pairs:
        if item["origin"]["object_type"] != "issue_comment":
            continue
        existing = item["origin"]["instance_key"] == INSTANCE_A
        operation = prepare_comment_operation(
            state,
            origin_id=origin_id,
            target_instance_key=INSTANCE_A,
            target_repository_scope=REPOSITORY,
            operation_type="update_comment" if existing else "create_comment",
            body=item["comparison"]["current"]["body"],
            expected_body=item["comparison"]["original_baseline"]["body"] if existing else None,
            author=item["author"],
            baseline_id="comments-a-0" if existing else None,
        )
        with (
            client(item["author"]["login"]) as comments,
            OperationExecutor(state, f"comments-return-{os.getpid()}-{len(outcomes)}") as executor,
        ):
            result = execute_comment_operation(state, executor, comments, operation.operation_id)
            target = comments.verified_comment(
                REPOSITORY, int(result.target_issue_index), str(result.target_global_id)
            )
        outcomes.append(
            {
                "origin_id": origin_id,
                "type": "comment",
                "status": result.status,
                "target_global_id": result.target_global_id,
                "parent_issue_index": result.target_issue_index,
                "body": target["body"],
                "author": target["user"]["login"],
                "native_created_at": target["created_at"],
                "origin_created_at": item["origin"]["created_at"],
            }
        )
    return 0, {"status": "passed", "admission": report.status, "outcomes": outcomes}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "seed",
            "import",
            "crash",
            "recover-crash",
            "lose",
            "recover-lost",
            "return",
            "repackage",
            "apply",
        ),
    )
    args = parser.parse_args()
    state = state_store()
    if args.mode == "seed":
        code, value = 0, seed_forward(state)
    elif args.mode == "import":
        code, value = 0, import_forward(state)
    elif args.mode == "crash":
        code, value = 0, run_unknown(state, 2, hard_exit=True)
    elif args.mode == "recover-crash":
        code, value = 0, recover_unknown(state, 2)
    elif args.mode == "lose":
        code, value = 0, run_unknown(state, 3, hard_exit=False)
    elif args.mode == "recover-lost":
        code, value = 0, recover_unknown(state, 3)
    elif args.mode == "return":
        code, value = 0, modify_return(state)
    elif args.mode == "repackage":
        code, value = 0, repackage_return(state)
    else:
        code, value = apply_return(state)
    print(json.dumps(value, sort_keys=True), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
