"""Journaled label, release, and asset operations with explicit parent mappings."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from gitferry.application_package import _digest, _file
from gitferry.comment_operations import resolve_author_binding
from gitferry.gitea_metadata import (
    GiteaMetadataClient,
    MetadataTargetConflict,
    ParentKind,
    verify_asset,
    verify_label,
    verify_release,
)
from gitferry.operations import OperationExecutor
from gitferry.state_codec import canonical_json
from gitferry.state_store import OperationBlocked, OperationRecord, StateStore, TargetConfirmation

METADATA_OPERATION_TYPES = frozenset(
    {
        "create_label",
        "update_label",
        "assign_issue_labels",
        "create_release",
        "update_release",
        "upload_attachment",
        "upload_release_asset",
    }
)


def label_values(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != {"name", "color", "description"}:
        raise ValueError("label fields must be name, color and description")
    result = {key: value[key] for key in ("name", "color", "description")}
    if (
        any(not isinstance(item, str) for item in result.values())
        or not result["name"]
        or len(result["name"]) > 255
        or len(result["description"]) > 1024
        or len(result["color"]) != 6
        or any(character not in "0123456789abcdefABCDEF" for character in result["color"])
    ):
        raise ValueError("label fields are invalid")
    result["color"] = result["color"].lower()
    return result


def release_values(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"tag_name", "target_commitish", "name", "body", "draft", "prerelease"}
    if set(value) != expected:
        raise ValueError("release fields are incomplete")
    result = dict(value)
    if (
        any(
            not isinstance(result[key], str)
            for key in ("tag_name", "target_commitish", "name", "body")
        )
        or not result["tag_name"]
        or not result["target_commitish"]
        or type(result["draft"]) is not bool
        or type(result["prerelease"]) is not bool
    ):
        raise ValueError("release fields are invalid")
    return result


def asset_values(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"name", "size", "sha256", "media_type", "member", "parent_origin_id"}:
        raise ValueError("asset fields are incomplete")
    result = dict(value)
    if (
        not isinstance(result["name"], str)
        or not result["name"]
        or "/" in result["name"]
        or "\\" in result["name"]
        or type(result["size"]) is not int
        or result["size"] < 0
        or not isinstance(result["sha256"], str)
        or len(result["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in result["sha256"])
        or not isinstance(result["media_type"], str)
        or not result["media_type"]
        or not isinstance(result["member"], str)
        or not isinstance(result["parent_origin_id"], str)
    ):
        raise ValueError("asset fields are invalid")
    return result


def _operation_id(
    operation_type: str,
    origin_id: str,
    target_instance_key: str,
    intent: Mapping[str, Any],
    baseline_id: str | None,
) -> str:
    identity: dict[str, Any] = {
        "origin": origin_id,
        "target": target_instance_key,
        "kind": operation_type,
    }
    if operation_type.startswith("update_") or operation_type == "assign_issue_labels":
        identity.update(intent=intent, baseline_id=baseline_id)
    return operation_type + "-" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()


def prepare_metadata_operation(
    state: StateStore,
    *,
    origin_id: str,
    target_instance_key: str,
    target_repository_scope: str,
    operation_type: str,
    values: Mapping[str, Any],
    baseline_id: str | None = None,
    author: Mapping[str, Any] | None = None,
) -> OperationRecord:
    if operation_type not in METADATA_OPERATION_TYPES:
        raise ValueError("unsupported metadata operation")
    origin = state.get_origin(origin_id)
    allowed = {
        "create_label": "label",
        "update_label": "label",
        "assign_issue_labels": "issue",
        "create_release": "release",
        "update_release": "release",
        "upload_attachment": "attachment",
        "upload_release_asset": "release_asset",
    }
    if origin.key.object_type != allowed[operation_type]:
        raise OperationBlocked("metadata operation and origin type differ")
    intent: dict[str, Any]
    mapping_version = 1
    if operation_type in ("create_label", "update_label"):
        intent = {"values": label_values(values)}
        ruleset = "gitea-label-fields"
    elif operation_type in ("create_release", "update_release"):
        intent = {"values": release_values(values)}
        ruleset = "gitea-release-fields"
        if author is None:
            raise OperationBlocked("release operation requires an explicit author binding")
        binding = resolve_author_binding(state, author, target_instance_key)
        intent["author_binding_id"] = binding.binding_id
        mapping_version = binding.mapping_version
    elif operation_type == "assign_issue_labels":
        origins = values.get("label_origin_ids")
        if (
            set(values) != {"label_origin_ids"}
            or not isinstance(origins, list)
            or any(
                not isinstance(item, str) or state.get_origin(item).key.object_type != "label"
                for item in origins
            )
        ):
            raise ValueError("issue label assignment needs explicit label origins")
        if len(set(origins)) != len(origins):
            raise ValueError("issue label origins must be unique")
        intent = {"label_origin_ids": sorted(origins)}
        ruleset = "gitea-issue-label-origins"
    else:
        intent = asset_values(values)
        parent = state.get_origin(intent["parent_origin_id"])
        expected_parents = (
            {"release"} if operation_type == "upload_release_asset" else {"issue", "comment"}
        )
        if (
            parent.key.object_type not in expected_parents
            or origin.key.parent_origin_id != parent.id
        ):
            raise OperationBlocked("asset parent origin is missing or contradictory")
        ruleset = ""
    if operation_type.startswith("update_") or operation_type == "assign_issue_labels":
        if baseline_id is None:
            raise OperationBlocked("metadata changes require an immutable baseline")
        baseline = state.get_baseline(baseline_id)
        if (
            baseline.origin_id != origin_id
            or baseline.ruleset_name != ruleset
            or baseline.ruleset_version != 1
        ):
            raise OperationBlocked("metadata baseline origin or ruleset differs")
        intent["expected"] = baseline.baseline
    return state.prepare_operation(
        operation_id=_operation_id(
            operation_type, origin_id, target_instance_key, intent, baseline_id
        ),
        origin_id=origin_id,
        target_instance_key=target_instance_key,
        target_repository_scope=target_repository_scope,
        operation_type=operation_type,
        mapping_version=mapping_version,
        baseline_id=baseline_id,
        intent=intent,
    )


class MetadataApplication:
    def __init__(
        self,
        state: StateStore,
        client: GiteaMetadataClient,
        package_root: Path,
        *,
        max_bytes: int,
    ) -> None:
        self.state = state
        self.client = client
        self.package_root = package_root
        self.max_bytes = max_bytes

    def _mapping(self, origin_id: str, target: str) -> Any:
        try:
            return self.state.get_object_mapping(origin_id, target)
        except KeyError:
            raise OperationBlocked("required parent or target mapping is missing") from None

    def _labels(self, operation: OperationRecord) -> dict[str, dict[str, Any]]:
        values = self.client.list_labels(operation.target_repository_scope)
        result: dict[str, dict[str, Any]] = {}
        for item in values:
            name = item.get("name")
            if not isinstance(name, str) or name in result:
                raise OperationBlocked("target label names are ambiguous")
            result[name] = item
        return result

    def preflight(
        self,
        operation_id: str,
        *,
        planned_repository: bool = False,
        planned_parent_origins: frozenset[str] = frozenset(),
    ) -> None:
        operation = self.state.get_operation(operation_id)
        if operation.status in ("outcome_unknown", "executing", "conflict", "failed"):
            raise OperationBlocked("metadata operation requires reconciliation")
        if operation.operation_type in ("create_label", "update_label"):
            if planned_repository and operation.operation_type == "create_label":
                return
            labels = self._labels(operation)
            if operation.operation_type == "create_label" and operation.status != "confirmed":
                if operation.intent["values"]["name"] in labels:
                    raise OperationBlocked("target label name already exists without this mapping")
                return
            mapping = self._mapping(operation.origin_id, operation.target_instance_key)
            matches = [
                item for item in labels.values() if str(item.get("id")) == mapping.target_global_id
            ]
            expected = (
                operation.intent["values"]
                if operation.status == "confirmed"
                else operation.intent["expected"]
            )
            if len(matches) != 1:
                raise OperationBlocked("mapped target label is missing")
            verify_label(matches[0], expected)
            return
        if operation.operation_type in ("create_release", "update_release"):
            binding = self.state.get_identity_binding(operation.intent["author_binding_id"])
            if (
                binding.target_instance_key != operation.target_instance_key
                or binding.target_login != self.client.authenticated_login
            ):
                raise OperationBlocked("release write client differs from the explicit author")
            if planned_repository and operation.operation_type == "create_release":
                return
            releases = self.client.list_releases(operation.target_repository_scope)
            if operation.operation_type == "create_release" and operation.status != "confirmed":
                wanted = operation.intent["values"]
                if any(
                    item.get("tag_name") == wanted["tag_name"] or item.get("name") == wanted["name"]
                    for item in releases
                ):
                    raise OperationBlocked(
                        "target release tag or name already exists without this mapping"
                    )
                return
            mapping = self._mapping(operation.origin_id, operation.target_instance_key)
            value = self.client.get_release(
                operation.target_repository_scope, mapping.target_global_id
            )
            expected = (
                operation.intent["values"]
                if operation.status == "confirmed"
                else operation.intent["expected"]
            )
            verify_release(
                value,
                expected,
                author_login=binding.target_login,
                author_id=binding.target_account_id,
            )
            return
        if operation.operation_type == "assign_issue_labels":
            issue = self._mapping(operation.origin_id, operation.target_instance_key)
            if issue.target_issue_index is None:
                raise OperationBlocked("mapped issue has no repository index")
            for origin_id in operation.intent["label_origin_ids"]:
                mapping = self._mapping(origin_id, operation.target_instance_key)
                if mapping.target_repository_scope != operation.target_repository_scope:
                    raise OperationBlocked("mapped label belongs to another repository")
            return
        intent = operation.intent
        path = _file(self.package_root, intent["member"])
        if _digest(path, limit=intent["size"]) != (intent["size"], intent["sha256"]):
            raise OperationBlocked("asset payload differs from the reviewed digest")
        try:
            parent = self._mapping(intent["parent_origin_id"], operation.target_instance_key)
        except OperationBlocked:
            if intent["parent_origin_id"] in planned_parent_origins:
                return
            raise
        parent_type = self.state.get_origin(intent["parent_origin_id"]).key.object_type
        if parent_type not in ("issue", "comment", "release"):
            raise OperationBlocked("asset parent mapping has an unsupported type")
        parent_kind: ParentKind = (
            "release"
            if operation.operation_type == "upload_release_asset"
            else cast(ParentKind, parent_type)
        )
        parent_id = parent.target_global_id
        if parent_kind == "issue":
            if parent.target_issue_index is None:
                raise OperationBlocked("mapped issue has no repository index")
            parent_id = str(parent.target_issue_index)
        assets = self.client.list_assets(operation.target_repository_scope, parent_kind, parent_id)
        if operation.status == "confirmed":
            mapping = self._mapping(operation.origin_id, operation.target_instance_key)
            matches = [item for item in assets if str(item.get("id")) == mapping.target_global_id]
            if len(matches) != 1:
                raise OperationBlocked("mapped target asset is missing from its parent")
            verify_asset(matches[0], name=intent["name"], size=intent["size"])
        elif any(item.get("name") == intent["name"] for item in assets):
            raise OperationBlocked("target parent already has an unmapped asset with this name")

    def apply(self, operation_id: str, executor: OperationExecutor) -> OperationRecord:
        operation = self.state.get_operation(operation_id)
        if operation.status == "confirmed":
            self.preflight(operation_id)
            return operation
        self.preflight(operation_id)

        def write(_: dict[str, Any]) -> TargetConfirmation:
            scope = operation.target_repository_scope
            kind = operation.operation_type
            if kind == "create_label":
                value = self.client.create_label(scope, operation.intent["values"])
                identifier = verify_label(value, operation.intent["values"])
                return TargetConfirmation(
                    identifier, None, observed_state=operation.intent["values"]
                )
            if kind == "update_label":
                mapping = self._mapping(operation.origin_id, operation.target_instance_key)
                value = self.client.update_label(
                    scope, mapping.target_global_id, operation.intent["values"]
                )
                identifier = verify_label(value, operation.intent["values"])
                return TargetConfirmation(
                    identifier, None, observed_state=operation.intent["values"]
                )
            if kind == "assign_issue_labels":
                issue = self._mapping(operation.origin_id, operation.target_instance_key)
                label_ids = [
                    int(self._mapping(origin, operation.target_instance_key).target_global_id)
                    for origin in operation.intent["label_origin_ids"]
                ]
                if issue.target_issue_index is None:
                    raise OperationBlocked("mapped issue has no repository index")
                values = self.client.replace_issue_labels(
                    scope, issue.target_issue_index, label_ids
                )
                if sorted(str(item.get("id")) for item in values) != sorted(map(str, label_ids)):
                    raise OperationBlocked("target issue label assignment differs")
                return TargetConfirmation(
                    issue.target_global_id,
                    issue.target_issue_index,
                    observed_state={"label_ids": sorted(label_ids)},
                )
            if kind == "create_release":
                binding = self.state.get_identity_binding(operation.intent["author_binding_id"])
                value = self.client.create_release(scope, operation.intent["values"])
                identifier = verify_release(
                    value,
                    operation.intent["values"],
                    author_login=binding.target_login,
                    author_id=binding.target_account_id,
                )
                created_at = value.get("created_at")
                return TargetConfirmation(
                    identifier,
                    None,
                    created_at if isinstance(created_at, str) else None,
                    operation.intent["values"],
                )
            if kind == "update_release":
                binding = self.state.get_identity_binding(operation.intent["author_binding_id"])
                mapping = self._mapping(operation.origin_id, operation.target_instance_key)
                value = self.client.update_release(
                    scope, mapping.target_global_id, operation.intent["values"]
                )
                identifier = verify_release(
                    value,
                    operation.intent["values"],
                    author_login=binding.target_login,
                    author_id=binding.target_account_id,
                )
                return TargetConfirmation(
                    identifier, None, mapping.target_native_created_at, operation.intent["values"]
                )
            intent = operation.intent
            parent = self._mapping(intent["parent_origin_id"], operation.target_instance_key)
            parent_type = self.state.get_origin(intent["parent_origin_id"]).key.object_type
            if parent_type not in ("issue", "comment", "release"):
                raise OperationBlocked("asset parent mapping has an unsupported type")
            parent_kind: ParentKind = (
                "release" if kind == "upload_release_asset" else cast(ParentKind, parent_type)
            )
            parent_id = parent.target_global_id
            if parent_kind == "issue":
                if parent.target_issue_index is None:
                    raise OperationBlocked("mapped issue has no repository index")
                parent_id = str(parent.target_issue_index)
            value = self.client.upload_asset(
                scope,
                parent_kind,
                parent_id,
                intent["name"],
                _file(self.package_root, intent["member"]),
                intent["media_type"],
            )
            identifier = verify_asset(value, name=intent["name"], size=intent["size"])
            with tempfile.TemporaryDirectory(dir=self.package_root.parent) as temporary:
                observed = Path(temporary) / "asset"
                size, digest, _media_type = self.client.download_asset(
                    value, observed, max_bytes=self.max_bytes
                )
            if (size, digest) != (intent["size"], intent["sha256"]):
                raise MetadataTargetConflict(
                    "uploaded asset bytes differ from the reviewed payload"
                )
            created_at = value.get("created_at")
            return TargetConfirmation(
                identifier,
                None,
                created_at if isinstance(created_at, str) else None,
                {"name": intent["name"], "size": size, "sha256": digest},
            )

        return executor.execute(operation_id, write)


__all__ = ["METADATA_OPERATION_TYPES", "MetadataApplication", "prepare_metadata_operation"]
