from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from gitferry.metadata_operations import (
    MetadataApplication,
    prepare_metadata_operation,
    resolve_unknown_metadata_operation,
)
from gitferry.operations import OperationExecutor
from gitferry.state_store import OperationBlocked, OriginKey, StateStore


class MetadataClient:
    def __init__(self) -> None:
        self.labels: list[dict[str, Any]] = []
        self.assets: list[dict[str, Any]] = []
        self.label_posts = 0
        self.label_puts = 0
        self.asset_posts = 0
        self.payload = b""
        self.issue_labels: list[dict[str, Any]] = []

    def list_labels(self, repository: str) -> list[dict[str, Any]]:
        return list(self.labels)

    def create_label(self, repository: str, values: dict[str, Any]) -> dict[str, Any]:
        self.label_posts += 1
        value = {"id": 41, **values}
        self.labels.append(value)
        return value

    def update_label(
        self, repository: str, label_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        for label in self.labels:
            if str(label["id"]) == label_id:
                label.update(values)
                return dict(label)
        raise KeyError(label_id)

    def list_issue_labels(self, repository: str, issue_index: int) -> list[dict[str, Any]]:
        return list(self.issue_labels)

    def replace_issue_labels(
        self, repository: str, issue_index: int, label_ids: list[int]
    ) -> list[dict[str, Any]]:
        self.label_puts += 1
        self.issue_labels = [{"id": identifier} for identifier in label_ids]
        return list(self.issue_labels)

    def list_assets(
        self, repository: str, parent_kind: str, parent_id: str
    ) -> list[dict[str, Any]]:
        return [
            value
            for value in self.assets
            if value.get("_parent_kind") == parent_kind and value.get("_parent_id") == parent_id
        ]

    def upload_asset(
        self,
        repository: str,
        parent_kind: str,
        parent_id: str,
        filename: str,
        path: Path,
        media_type: str,
    ) -> dict[str, Any]:
        self.asset_posts += 1
        self.payload = path.read_bytes()
        value = {
            "id": 71,
            "uuid": "asset-71",
            "name": filename,
            "size": len(self.payload),
            "created_at": "2026-01-01T00:00:00Z",
            "browser_download_url": "https://gitea.invalid/attachments/asset-71",
            "_parent_kind": parent_kind,
            "_parent_id": parent_id,
        }
        self.assets.append(value)
        return value

    def download_asset(
        self, value: dict[str, Any], destination: Path, *, max_bytes: int
    ) -> tuple[int, str, str]:
        destination.write_bytes(self.payload)
        return len(self.payload), hashlib.sha256(self.payload).hexdigest(), "text/plain"


def configured(tmp_path: Path) -> tuple[StateStore, str, str]:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("a", "A", "https://a.invalid", "install-a")
    state.register_instance("b", "B", "https://b.invalid", "install-b")
    issue = state.register_origin(OriginKey("a", "acme/repo", "issue", "11", 3))
    state.add_object_mapping(issue.id, "b", "acme/repo", "51", target_issue_index=8)
    label = state.register_origin(OriginKey("a", "acme/repo", "label", "12"))
    return state, issue.id, label.id


def test_label_create_is_confirmed_once_and_replayed_without_post(tmp_path: Path) -> None:
    state, _, label = configured(tmp_path)
    operation = prepare_metadata_operation(
        state,
        origin_id=label,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="create_label",
        values={"name": "priority", "color": "AABBCC", "description": "Synthetic"},
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with OperationExecutor(state, "first") as executor:
        confirmed = app.apply(operation.operation_id, executor)
    assert confirmed.status == "confirmed"
    assert state.get_object_mapping(label, "b").target_global_id == "41"
    with OperationExecutor(state, "replay") as executor:
        replay = app.apply(operation.operation_id, executor)
    assert replay.status == "confirmed"
    assert client.label_posts == 1
    assert client.labels[0]["color"] == "aabbcc"


def test_unknown_label_create_stays_blocked_without_second_post(tmp_path: Path) -> None:
    state, _, label = configured(tmp_path)
    operation = prepare_metadata_operation(
        state,
        origin_id=label,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="create_label",
        values={"name": "priority", "color": "112233", "description": "Synthetic"},
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="crash"):
        with OperationExecutor(
            state, "crash", after_remote_hook=lambda: (_ for _ in ()).throw(RuntimeError("crash"))
        ) as executor:
            app.apply(operation.operation_id, executor)
    assert client.label_posts == 1
    assert state.get_operation(operation.operation_id).status == "executing"
    with OperationExecutor(state, "restart"):
        pass
    assert state.get_operation(operation.operation_id).status == "outcome_unknown"
    with OperationExecutor(state, "retry") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            app.apply(operation.operation_id, executor)
    assert client.label_posts == 1

    with pytest.raises(OperationBlocked, match="missing or ambiguous"):
        resolve_unknown_metadata_operation(
            state,
            client,  # type: ignore[arg-type]
            operation.operation_id,
            target_global_id="999",
            evidence="synthetic live label verification",
        )
    resolved = resolve_unknown_metadata_operation(
        state,
        client,  # type: ignore[arg-type]
        operation.operation_id,
        target_global_id="41",
        evidence="synthetic live label verification",
    )
    assert resolved.status == "confirmed"
    assert state.get_object_mapping(label, "b").target_global_id == "41"


def test_unknown_asset_resolution_checks_parent_and_bytes(tmp_path: Path) -> None:
    state, issue, _ = configured(tmp_path)
    payload = tmp_path / "payloads" / "asset.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"synthetic asset")
    attachment = state.register_origin(
        OriginKey("a", "acme/repo", "attachment", "18", parent_origin_id=issue)
    )
    operation = prepare_metadata_operation(
        state,
        origin_id=attachment.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="upload_attachment",
        values={
            "name": "asset.txt",
            "size": len(payload.read_bytes()),
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "media_type": "text/plain",
            "member": "payloads/asset.bin",
            "parent_origin_id": issue,
        },
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="crash"):
        with OperationExecutor(
            state,
            "crash",
            after_remote_hook=lambda: (_ for _ in ()).throw(RuntimeError("crash")),
        ) as executor:
            app.apply(operation.operation_id, executor)
    with OperationExecutor(state, "restart"):
        pass
    assert state.get_operation(operation.operation_id).status == "outcome_unknown"

    client.assets[0]["_parent_id"] = "other"
    with pytest.raises(OperationBlocked, match="expected parent"):
        resolve_unknown_metadata_operation(
            state,
            client,  # type: ignore[arg-type]
            operation.operation_id,
            target_global_id="71",
            evidence="synthetic live asset verification",
        )
    client.assets[0]["_parent_id"] = "8"
    client.payload = b"synthetic asseX"
    with pytest.raises(OperationBlocked, match="bytes differ"):
        resolve_unknown_metadata_operation(
            state,
            client,  # type: ignore[arg-type]
            operation.operation_id,
            target_global_id="71",
            evidence="synthetic live asset verification",
        )
    client.payload = payload.read_bytes()
    resolved = resolve_unknown_metadata_operation(
        state,
        client,  # type: ignore[arg-type]
        operation.operation_id,
        target_global_id="71",
        evidence="synthetic live asset verification",
    )
    assert resolved.status == "confirmed"
    assert state.get_object_mapping(attachment.id, "b").target_global_id == "71"


def test_new_issue_label_assignment_uses_mapped_origins_and_replays_without_put(
    tmp_path: Path,
) -> None:
    state, _, label = configured(tmp_path)
    issue = state.register_origin(OriginKey("a", "acme/repo", "issue", "13", 4))
    state.add_object_mapping(label, "b", "acme/repo", "41")
    operation = prepare_metadata_operation(
        state,
        origin_id=issue.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="assign_issue_labels",
        values={"label_origin_ids": [label]},
    )
    state.add_object_mapping(issue.id, "b", "acme/repo", "52", target_issue_index=9)
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with OperationExecutor(state, "assign") as executor:
        confirmed = app.apply(operation.operation_id, executor)
    assert confirmed.status == "confirmed"
    assert client.issue_labels == [{"id": 41}]
    with OperationExecutor(state, "replay-assignment") as executor:
        replay = app.apply(operation.operation_id, executor)
    assert replay.status == "confirmed"
    assert client.label_puts == 1

    repackaged = prepare_metadata_operation(
        state,
        origin_id=issue.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="assign_issue_labels",
        values={"label_origin_ids": [label]},
    )
    assert repackaged.operation_id == operation.operation_id
    assert repackaged.status == "confirmed"

    another = state.register_origin(OriginKey("a", "acme/repo", "label", "14"))
    with pytest.raises(OperationBlocked, match="immutable baseline"):
        prepare_metadata_operation(
            state,
            origin_id=issue.id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            operation_type="assign_issue_labels",
            values={"label_origin_ids": [label, another.id]},
        )

    client.issue_labels = [{"id": 99}]
    with OperationExecutor(state, "drift-assignment") as executor:
        with pytest.raises(OperationBlocked, match="differ from the baseline"):
            app.apply(operation.operation_id, executor)
    assert client.label_puts == 1


def test_new_issue_label_assignment_preflight_accepts_planned_issue_and_label(
    tmp_path: Path,
) -> None:
    state, _, label = configured(tmp_path)
    issue = state.register_origin(OriginKey("a", "acme/repo", "issue", "13", 4))
    operation = prepare_metadata_operation(
        state,
        origin_id=issue.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="assign_issue_labels",
        values={"label_origin_ids": [label]},
    )
    app = MetadataApplication(state, MetadataClient(), tmp_path, max_bytes=1024)  # type: ignore[arg-type]

    app.preflight(
        operation.operation_id,
        planned_parent_scopes={issue.id: "acme/repo"},
        planned_label_scopes={label: "acme/repo"},
    )

    with pytest.raises(OperationBlocked, match="no target mapping or create"):
        app.preflight(
            operation.operation_id,
            planned_parent_scopes={issue.id: "acme/repo"},
        )
    with pytest.raises(OperationBlocked, match="required parent or target mapping"):
        app.preflight(
            operation.operation_id,
            planned_parent_scopes={issue.id: "other/repo"},
            planned_label_scopes={label: "acme/repo"},
        )
    with pytest.raises(OperationBlocked, match="no target mapping or create"):
        app.preflight(
            operation.operation_id,
            planned_parent_scopes={issue.id: "acme/repo"},
            planned_label_scopes={label: "other/repo"},
        )


def test_existing_issue_label_assignment_preflight_accepts_planned_new_label(
    tmp_path: Path,
) -> None:
    state, issue, label = configured(tmp_path)
    state.create_transfer_round("labels-round", "a", "b", "acme/repo")
    state.create_baseline(
        baseline_id="existing-labels",
        transfer_round_id="labels-round",
        origin_id=issue,
        ruleset_name="gitea-issue-label-origins",
        ruleset_version=1,
        baseline={"label_origin_ids": []},
        desired={"label_origin_ids": [label]},
        observed={"label_origin_ids": []},
        origin_created_at=None,
        target_native_created_at=None,
    )
    operation = prepare_metadata_operation(
        state,
        origin_id=issue,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="assign_issue_labels",
        values={"label_origin_ids": [label]},
        baseline_id="existing-labels",
    )
    app = MetadataApplication(state, MetadataClient(), tmp_path, max_bytes=1024)  # type: ignore[arg-type]

    app.preflight(
        operation.operation_id,
        planned_label_scopes={label: "acme/repo"},
    )


def test_same_name_label_collision_is_blocked_without_mapping_or_post(tmp_path: Path) -> None:
    state, _, label = configured(tmp_path)
    client = MetadataClient()
    client.labels.append({"id": 99, "name": "priority", "color": "aabbcc", "description": "Other"})
    operation = prepare_metadata_operation(
        state,
        origin_id=label,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="create_label",
        values={"name": "priority", "color": "AABBCC", "description": "Synthetic"},
    )
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with OperationExecutor(state, "collision") as executor:
        with pytest.raises(OperationBlocked, match="already exists"):
            app.apply(operation.operation_id, executor)
    assert client.label_posts == 0
    with pytest.raises(KeyError):
        state.get_object_mapping(label, "b")


def test_label_update_reuses_mapping_and_stale_baseline_blocks_patch(tmp_path: Path) -> None:
    state, _, label = configured(tmp_path)
    create = prepare_metadata_operation(
        state,
        origin_id=label,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="create_label",
        values={"name": "priority", "color": "AABBCC", "description": "Synthetic"},
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with OperationExecutor(state, "create") as executor:
        app.apply(create.operation_id, executor)
    state.create_transfer_round("label-round", "a", "b", "acme/repo")
    baseline_values = {"name": "priority", "color": "aabbcc", "description": "Synthetic"}
    state.create_baseline(
        baseline_id="label-base",
        transfer_round_id="label-round",
        origin_id=label,
        ruleset_name="gitea-label-fields",
        ruleset_version=1,
        baseline=baseline_values,
        desired=baseline_values,
        observed=baseline_values,
        origin_created_at=None,
        target_native_created_at=None,
    )
    update = prepare_metadata_operation(
        state,
        origin_id=label,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="update_label",
        baseline_id="label-base",
        values={"name": "priority", "color": "112233", "description": "Changed"},
    )
    with OperationExecutor(state, "update") as executor:
        result = app.apply(update.operation_id, executor)
    assert result.status == "confirmed"
    assert result.target_global_id == "41"
    assert state.get_object_mapping(label, "b").target_global_id == "41"
    assert client.labels[0]["description"] == "Changed"

    state.create_transfer_round("label-round-stale", "a", "b", "acme/repo")
    state.create_baseline(
        baseline_id="label-base-stale",
        transfer_round_id="label-round-stale",
        origin_id=label,
        ruleset_name="gitea-label-fields",
        ruleset_version=1,
        baseline={"name": "priority", "color": "112233", "description": "Changed"},
        desired={"name": "priority", "color": "445566", "description": "New"},
        observed={"name": "priority", "color": "112233", "description": "Changed"},
        origin_created_at=None,
        target_native_created_at=None,
    )
    client.labels[0]["description"] = "Foreign change"
    stale = prepare_metadata_operation(
        state,
        origin_id=label,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="update_label",
        baseline_id="label-base-stale",
        values={"name": "priority", "color": "445566", "description": "New"},
    )
    with OperationExecutor(state, "stale") as executor:
        with pytest.raises(OperationBlocked, match="differ"):
            app.apply(stale.operation_id, executor)
    assert client.labels[0]["description"] == "Foreign change"


def test_attachment_upload_verifies_parent_and_remote_bytes(tmp_path: Path) -> None:
    state, issue, _ = configured(tmp_path)
    payload = tmp_path / "payloads" / "asset.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"synthetic asset")
    attachment = state.register_origin(
        OriginKey("a", "acme/repo", "attachment", "17", parent_origin_id=issue)
    )
    operation = prepare_metadata_operation(
        state,
        origin_id=attachment.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="upload_attachment",
        values={
            "name": "asset.txt",
            "size": len(payload.read_bytes()),
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "media_type": "text/plain",
            "member": "payloads/asset.bin",
            "parent_origin_id": issue,
        },
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with OperationExecutor(state, "asset") as executor:
        confirmed = app.apply(operation.operation_id, executor)
    assert confirmed.status == "confirmed"
    assert client.asset_posts == 1
    assert client.payload == b"synthetic asset"
    app.preflight(operation.operation_id)


def test_attachment_rejects_cross_repository_parent_before_upload(tmp_path: Path) -> None:
    state, _, _ = configured(tmp_path)
    parent = state.register_origin(OriginKey("a", "other/repo", "issue", "19", 5))
    state.add_object_mapping(parent.id, "b", "other/repo", "61", target_issue_index=9)
    payload = tmp_path / "payloads" / "cross-repo.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"synthetic asset")
    attachment = state.register_origin(
        OriginKey("a", "acme/repo", "attachment", "20", parent_origin_id=parent.id)
    )
    operation = prepare_metadata_operation(
        state,
        origin_id=attachment.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="upload_attachment",
        values={
            "name": "cross-repo.txt",
            "size": len(payload.read_bytes()),
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "media_type": "text/plain",
            "member": "payloads/cross-repo.bin",
            "parent_origin_id": parent.id,
        },
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]

    with pytest.raises(OperationBlocked, match="another repository"):
        app.preflight(operation.operation_id)
    with OperationExecutor(state, "cross-repo") as executor:
        with pytest.raises(OperationBlocked, match="another repository"):
            app.apply(operation.operation_id, executor)
    assert client.asset_posts == 0


def test_confirmed_asset_rejects_equal_size_remote_digest_drift(tmp_path: Path) -> None:
    state, issue, _ = configured(tmp_path)
    payload = tmp_path / "payloads" / "asset.bin"
    payload.parent.mkdir()
    payload.write_bytes(b"synthetic asset")
    attachment = state.register_origin(
        OriginKey("a", "acme/repo", "attachment", "18", parent_origin_id=issue)
    )
    operation = prepare_metadata_operation(
        state,
        origin_id=attachment.id,
        target_instance_key="b",
        target_repository_scope="acme/repo",
        operation_type="upload_attachment",
        values={
            "name": "asset.txt",
            "size": len(payload.read_bytes()),
            "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
            "media_type": "text/plain",
            "member": "payloads/asset.bin",
            "parent_origin_id": issue,
        },
    )
    client = MetadataClient()
    app = MetadataApplication(state, client, tmp_path, max_bytes=1024)  # type: ignore[arg-type]
    with OperationExecutor(state, "asset") as executor:
        app.apply(operation.operation_id, executor)

    client.payload = b"synthetic asseX"
    with pytest.raises(OperationBlocked, match="bytes differ"):
        app.preflight(operation.operation_id)
    assert client.asset_posts == 1


def test_asset_parent_conflict_is_rejected_before_operation(tmp_path: Path) -> None:
    state, _, _ = configured(tmp_path)
    release = state.register_origin(OriginKey("a", "acme/repo", "release", "21"))
    attachment = state.register_origin(
        OriginKey("a", "acme/repo", "attachment", "22", parent_origin_id=release.id)
    )
    with pytest.raises(OperationBlocked, match="parent"):
        prepare_metadata_operation(
            state,
            origin_id=attachment.id,
            target_instance_key="b",
            target_repository_scope="acme/repo",
            operation_type="upload_attachment",
            values={
                "name": "asset.txt",
                "size": 0,
                "sha256": hashlib.sha256(b"").hexdigest(),
                "media_type": "text/plain",
                "member": "payloads/asset.bin",
                "parent_origin_id": release.id,
            },
        )
