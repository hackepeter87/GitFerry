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


def verify_release_target(
    client: GiteaMetadataClient,
    repository: str,
    expected: Mapping[str, Any],
    *,
    allow_missing_tag: bool = False,
) -> None:
    desired = client.resolve_git_target(repository, str(expected["target_commitish"]))
    actual = client.tag_target(repository, str(expected["tag_name"]))
    if actual is None and allow_missing_tag:
        return
    if actual != desired:
        raise MetadataTargetConflict("release Git tag points at another target")


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
            if operation_type != "assign_issue_labels":
                raise OperationBlocked("metadata changes require an immutable baseline")
            try:
                state.get_object_mapping(origin_id, target_instance_key)
            except KeyError:
                intent["expected"] = {"label_origin_ids": []}
            else:
                intent["expected"] = {"label_origin_ids": []}
                operation_id = _operation_id(
                    operation_type, origin_id, target_instance_key, intent, baseline_id
                )
                try:
                    existing = state.get_operation(operation_id)
                except KeyError:
                    raise OperationBlocked(
                        "existing issue label changes require an immutable baseline"
                    ) from None
                if (
                    existing.status != "confirmed"
                    or existing.target_repository_scope != target_repository_scope
                ):
                    raise OperationBlocked(
                        "existing issue label changes require an immutable baseline"
                    )
                return existing
        else:
            baseline = state.get_baseline(baseline_id)
            if (
                baseline.origin_id != origin_id
                or baseline.ruleset_name != ruleset
                or baseline.ruleset_version != 1
            ):
                raise OperationBlocked("metadata baseline origin or ruleset differs")
            if operation_type == "assign_issue_labels":
                expected_origins = baseline.baseline.get("label_origin_ids")
                if (
                    set(baseline.baseline) != {"label_origin_ids"}
                    or not isinstance(expected_origins, list)
                    or any(
                        not isinstance(item, str)
                        or state.get_origin(item).key.object_type != "label"
                        for item in expected_origins
                    )
                    or len(set(expected_origins)) != len(expected_origins)
                ):
                    raise OperationBlocked("issue label baseline is invalid")
                intent["expected"] = {"label_origin_ids": sorted(expected_origins)}
            else:
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
        planned_parent_scopes: Mapping[str, str] | None = None,
        planned_label_scopes: Mapping[str, str] | None = None,
    ) -> None:
        planned_parent_scopes = planned_parent_scopes or {}
        planned_label_scopes = planned_label_scopes or {}
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
            try:
                verify_label(matches[0], expected)
            except MetadataTargetConflict as exc:
                raise OperationBlocked("mapped target label differs from the baseline") from exc
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
                try:
                    verify_release_target(
                        self.client,
                        operation.target_repository_scope,
                        wanted,
                        allow_missing_tag=True,
                    )
                except MetadataTargetConflict as exc:
                    raise OperationBlocked(
                        "release tag target cannot be represented safely"
                    ) from exc
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
            if operation.operation_type == "update_release" and operation.status != "confirmed":
                wanted = operation.intent["values"]
                if any(wanted[key] != expected[key] for key in ("tag_name", "target_commitish")):
                    raise OperationBlocked("release tag target changes are not supported")
            try:
                verify_release(
                    value,
                    expected,
                    author_login=binding.target_login,
                    author_id=binding.target_account_id,
                )
                verify_release_target(self.client, operation.target_repository_scope, expected)
            except MetadataTargetConflict as exc:
                raise OperationBlocked("mapped target release differs from the baseline") from exc
            return
        if operation.operation_type == "assign_issue_labels":
            try:
                issue = self._mapping(operation.origin_id, operation.target_instance_key)
            except OperationBlocked:
                if (
                    planned_parent_scopes.get(operation.origin_id)
                    != operation.target_repository_scope
                ):
                    raise
                missing = []
                for origin_id in operation.intent["label_origin_ids"]:
                    if planned_label_scopes.get(origin_id) == operation.target_repository_scope:
                        continue
                    try:
                        self._mapping(origin_id, operation.target_instance_key)
                    except OperationBlocked:
                        missing.append(origin_id)
                if missing:
                    raise OperationBlocked(
                        "planned issue labels have no target mapping or create"
                    ) from None
                return
            if (
                issue.target_repository_scope != operation.target_repository_scope
                or issue.target_issue_index is None
            ):
                raise OperationBlocked("mapped issue has no repository index")
            desired_ids: list[int] = []
            for origin_id in operation.intent["label_origin_ids"]:
                try:
                    mapping = self._mapping(origin_id, operation.target_instance_key)
                except OperationBlocked:
                    if (
                        operation.status != "confirmed"
                        and planned_label_scopes.get(origin_id) == operation.target_repository_scope
                    ):
                        continue
                    raise
                if mapping.target_repository_scope != operation.target_repository_scope:
                    raise OperationBlocked("mapped label belongs to another repository")
                desired_ids.append(int(mapping.target_global_id))
            expected_ids: list[int] = []
            for origin_id in operation.intent["expected"]["label_origin_ids"]:
                mapping = self._mapping(origin_id, operation.target_instance_key)
                if mapping.target_repository_scope != operation.target_repository_scope:
                    raise OperationBlocked("baseline label belongs to another repository")
                expected_ids.append(int(mapping.target_global_id))
            current = self.client.list_issue_labels(
                operation.target_repository_scope, issue.target_issue_index
            )
            try:
                current_ids = sorted(int(item["id"]) for item in current)
            except (KeyError, TypeError, ValueError):
                raise OperationBlocked("target issue label inventory is invalid") from None
            wanted = sorted(desired_ids if operation.status == "confirmed" else expected_ids)
            if current_ids != wanted:
                raise OperationBlocked("mapped target issue labels differ from the baseline")
            return
        intent = operation.intent
        path = _file(self.package_root, intent["member"])
        if _digest(path, limit=intent["size"]) != (intent["size"], intent["sha256"]):
            raise OperationBlocked("asset payload differs from the reviewed digest")
        try:
            parent = self._mapping(intent["parent_origin_id"], operation.target_instance_key)
        except OperationBlocked:
            if (
                planned_parent_scopes.get(intent["parent_origin_id"])
                == operation.target_repository_scope
            ):
                return
            raise
        if parent.target_repository_scope != operation.target_repository_scope:
            raise OperationBlocked("asset parent mapping belongs to another repository")
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
            try:
                verify_asset(matches[0], name=intent["name"], size=intent["size"])
            except MetadataTargetConflict as exc:
                raise OperationBlocked(
                    f"mapped target {operation.operation_type} metadata differs"
                ) from exc
            try:
                with tempfile.TemporaryDirectory() as temporary:
                    observed = Path(temporary) / "asset"
                    size, digest, _media_type = self.client.download_asset(
                        matches[0], observed, max_bytes=self.max_bytes
                    )
                if (size, digest) != (intent["size"], intent["sha256"]):
                    raise MetadataTargetConflict(
                        "mapped target asset bytes differ from the reviewed payload"
                    )
            except MetadataTargetConflict as exc:
                raise OperationBlocked(
                    f"mapped target {operation.operation_type} bytes differ"
                ) from exc
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
                verify_release_target(self.client, scope, operation.intent["values"])
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
                verify_release_target(self.client, scope, operation.intent["values"])
                return TargetConfirmation(
                    identifier, None, mapping.target_native_created_at, operation.intent["values"]
                )
            intent = operation.intent
            parent = self._mapping(intent["parent_origin_id"], operation.target_instance_key)
            if parent.target_repository_scope != scope:
                raise OperationBlocked("asset parent mapping belongs to another repository")
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
