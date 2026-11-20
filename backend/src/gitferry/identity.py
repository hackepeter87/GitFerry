"""Fail-closed identity resolution for explicit GitFerry mappings.

This module models directory identities and local Gitea accounts separately. A
successful resolution alone neither rewrites historical authors nor grants a
permission; those are deliberate follow-up decisions represented by distinct
records.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ResolutionStatus = Literal["resolved", "unmapped", "ambiguous", "collision"]
PermissionRole = Literal["read", "write"]


class IdentityResolutionError(ValueError):
    """Raised when an unresolved identity is used for a downstream decision."""


@dataclass(frozen=True)
class IdentityKey:
    realm: str
    login: str
    external_id: str
    ou_path: str


@dataclass(frozen=True)
class ExternalIdentity:
    key: IdentityKey
    source_numeric_id: int | None = None
    email: str | None = None


@dataclass(frozen=True)
class LocalAccount:
    realm: str
    login: str
    numeric_id: int
    is_admin: bool = False


@dataclass(frozen=True)
class ExplicitIdentityMapping:
    source: IdentityKey
    target_realm: str
    target_login: str


@dataclass(frozen=True)
class ExistingBinding:
    local_account_id: int
    external_identity: IdentityKey


@dataclass(frozen=True)
class HistoricalAuthorBinding:
    origin: IdentityKey
    native_account_id: int
    native_login: str


@dataclass(frozen=True)
class PermissionAssignment:
    local_account_id: int
    repository: str
    role: PermissionRole


@dataclass(frozen=True)
class IdentityResolution:
    status: ResolutionStatus
    external_identity: ExternalIdentity
    local_account: LocalAccount | None = None
    historical_author: HistoricalAuthorBinding | None = None
    permission_assignment: PermissionAssignment | None = None
    conflicts: tuple[str, ...] = ()


def resolve_identity(
    identity: ExternalIdentity,
    mappings: Sequence[ExplicitIdentityMapping],
    local_accounts: Sequence[LocalAccount],
    existing_bindings: Sequence[ExistingBinding],
) -> IdentityResolution:
    """Resolve one external identity using exact, explicit configuration only."""
    candidates = [item for item in mappings if item.source == identity.key]
    if not candidates:
        return IdentityResolution(status="unmapped", external_identity=identity)
    if len(candidates) != 1:
        return IdentityResolution(
            status="ambiguous",
            external_identity=identity,
            conflicts=("multiple explicit mappings match the external identity",),
        )

    candidate = candidates[0]
    accounts = [
        item
        for item in local_accounts
        if item.realm == candidate.target_realm and item.login == candidate.target_login
    ]
    if not accounts:
        return IdentityResolution(
            status="unmapped",
            external_identity=identity,
            conflicts=("the explicitly mapped local account does not exist",),
        )
    if len(accounts) != 1:
        return IdentityResolution(
            status="ambiguous",
            external_identity=identity,
            conflicts=("multiple local accounts match the explicit target",),
        )

    local_account = accounts[0]
    if local_account.is_admin:
        return IdentityResolution(
            status="collision",
            external_identity=identity,
            conflicts=("mapping to an administrator would expand privileges",),
        )

    foreign_bindings = [
        item
        for item in existing_bindings
        if item.local_account_id == local_account.numeric_id
        and item.external_identity != identity.key
    ]
    if foreign_bindings:
        return IdentityResolution(
            status="collision",
            external_identity=identity,
            conflicts=("the local account is already bound to another external identity",),
        )

    return IdentityResolution(
        status="resolved", external_identity=identity, local_account=local_account
    )


def _require_resolved(resolution: IdentityResolution) -> LocalAccount:
    if resolution.status != "resolved" or resolution.local_account is None:
        raise IdentityResolutionError(
            f"identity resolution is {resolution.status}; no downstream binding is allowed"
        )
    return resolution.local_account


def create_historical_author_binding(
    resolution: IdentityResolution,
) -> HistoricalAuthorBinding:
    account = _require_resolved(resolution)
    return HistoricalAuthorBinding(
        origin=resolution.external_identity.key,
        native_account_id=account.numeric_id,
        native_login=account.login,
    )


def create_permission_assignment(
    resolution: IdentityResolution, repository: str, role: str
) -> PermissionAssignment:
    account = _require_resolved(resolution)
    if role not in ("read", "write"):
        raise ValueError(f"unsupported permission role: {role}")
    validated_role: PermissionRole = "read" if role == "read" else "write"
    owner, separator, name = repository.partition("/")
    if separator != "/" or not owner or not name or "/" in name:
        raise ValueError("repository must have the form owner/name")
    return PermissionAssignment(
        local_account_id=account.numeric_id,
        repository=repository,
        role=validated_role,
    )
