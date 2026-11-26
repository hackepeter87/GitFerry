"""Typed public records and errors for GitFerry's local control state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ObjectType = Literal[
    "issue",
    "comment",
    "organization",
    "team",
    "repository",
    "label",
    "release",
    "attachment",
    "release_asset",
]
OperationStatus = Literal[
    "prepared",
    "blocked_parent",
    "executing",
    "outcome_unknown",
    "confirmed",
    "conflict",
    "failed",
]


class StateStoreError(RuntimeError):
    """Base error for durable control-state failures."""


class UnsupportedSchemaVersion(StateStoreError):
    """Raised when the database was written by a newer GitFerry version."""


class BindingConflict(StateStoreError):
    """Raised when an immutable identity binding would be redirected."""


class MappingConflict(StateStoreError):
    """Raised when an immutable object mapping would be redirected."""


class OperationConflict(StateStoreError):
    """Raised when an operation id is reused for a different intent."""


class OperationBlocked(StateStoreError):
    """Raised when an operation needs reconciliation instead of another write."""


class ExecutorLeaseConflict(StateStoreError):
    """Raised when another local writer owns the durable executor lease."""


@dataclass(frozen=True)
class InstanceRegistration:
    instance_key: str
    display_name: str
    base_url: str
    installation_marker: str


@dataclass(frozen=True)
class RealmRegistration:
    realm_key: str
    display_name: str
    external_marker: str


@dataclass(frozen=True)
class IdentityBindingRecord:
    binding_id: str
    source_realm_key: str
    source_login: str
    source_external_id: str
    source_ou_path: str
    target_instance_key: str
    target_account_id: int
    target_login: str
    mapping_version: int


@dataclass(frozen=True)
class OriginKey:
    origin_instance_key: str
    origin_repository_scope: str
    object_type: ObjectType
    origin_global_id: str
    issue_index: int | None = None
    parent_origin_id: str | None = None
    origin_created_at: str | None = None


@dataclass(frozen=True)
class OriginRecord:
    id: str
    key: OriginKey


@dataclass(frozen=True)
class ObjectMappingRecord:
    origin_id: str
    target_instance_key: str
    target_repository_scope: str
    target_object_type: ObjectType
    target_global_id: str
    target_issue_index: int | None
    target_native_created_at: str | None


@dataclass(frozen=True)
class BaselineRecord:
    baseline_id: str
    transfer_round_id: str
    origin_id: str
    ruleset_name: str
    ruleset_version: int
    baseline: dict[str, Any]
    desired: dict[str, Any]
    observed: dict[str, Any]
    observation_revision: int
    origin_created_at: str | None
    target_native_created_at: str | None


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    origin_id: str
    target_instance_key: str
    target_repository_scope: str
    operation_type: str
    mapping_version: int
    baseline_id: str | None
    intent: dict[str, Any]
    intent_hash: str
    status: OperationStatus
    executor_id: str | None
    target_global_id: str | None
    target_issue_index: int | None
    error_reason: str | None


@dataclass(frozen=True)
class TargetConfirmation:
    target_global_id: str
    target_issue_index: int | None
    target_native_created_at: str | None = None
    observed_state: dict[str, Any] | None = None
