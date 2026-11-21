"""Bounded, journaled provisioning for one selected Gitea repository."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from gitferry.gitea_provisioning import (
    ProvisioningTargetConflict,
    verify_account,
    verify_organization,
    verify_repository,
    verify_team,
    verify_technical_administrator,
)
from gitferry.operations import OperationExecutor
from gitferry.state_codec import canonical_json
from gitferry.state_store import (
    IdentityBindingRecord,
    ObjectMappingRecord,
    OperationBlocked,
    OperationRecord,
    StateStore,
    TargetConfirmation,
)

PermissionRole = Literal["read", "write"]


class ProvisioningClient(Protocol):
    authenticated_login: str

    def create_organization(self, username: str, visibility: str) -> dict[str, Any]: ...

    def find_organization(self, username: str) -> dict[str, Any] | None: ...

    def create_team(self, organization: str, name: str, permission: str) -> dict[str, Any]: ...

    def find_team(self, organization: str, name: str) -> dict[str, Any] | None: ...

    def create_repository(self, organization: str, name: str, private: bool) -> dict[str, Any]: ...

    def find_repository(self, repository: str) -> dict[str, Any] | None: ...

    def assign_team_repository(self, team_id: str, repository: str) -> dict[str, Any]: ...

    def add_team_member(self, team_id: str, username: str) -> dict[str, Any]: ...

    def get_user(self, username: str) -> dict[str, Any]: ...

    def get_authenticated_user(self) -> dict[str, Any]: ...


class BundlePusher(Protocol):
    def push(
        self,
        repository: str,
        *,
        bundle_member: str,
        bundle_sha256: str,
        bundle_size: int,
        expected_repository_id: str,
    ) -> TargetConfirmation: ...


@dataclass(frozen=True)
class ProvisioningMember:
    binding_id: str
    role: PermissionRole


@dataclass(frozen=True)
class ProvisioningTeam:
    origin_id: str
    name: str
    permission: PermissionRole
    members: tuple[ProvisioningMember, ...] = ()


@dataclass(frozen=True)
class BundleIntent:
    member: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ProvisioningPlan:
    target_instance_key: str
    organization_origin_id: str
    organization: str
    repository_origin_id: str
    repository: str
    teams: tuple[ProvisioningTeam, ...]
    bundle: BundleIntent | None = None

    def __post_init__(self) -> None:
        owner, separator, name = self.repository.partition("/")
        if separator != "/" or owner != self.organization or not name or "/" in name:
            raise ValueError("selected repository must be owned by the planned organization")
        if len({team.name for team in self.teams}) != len(self.teams):
            raise ValueError("team names must be unique within a provisioning plan")
        for team in self.teams:
            if team.permission not in ("read", "write"):
                raise ValueError("only read and write teams are supported")
            for member in team.members:
                if member.role != team.permission:
                    raise ValueError("member role must match the receiving team permission")
        if self.bundle is not None:
            if len(self.bundle.sha256) != 64 or any(
                character not in "0123456789abcdef" for character in self.bundle.sha256
            ):
                raise ValueError("bundle SHA-256 must be lowercase hexadecimal")
            if self.bundle.size <= 0:
                raise ValueError("bundle size must be positive")


@dataclass(frozen=True)
class ProvisioningResult:
    operations: tuple[OperationRecord, ...]


@dataclass(frozen=True)
class _PreparedTeam:
    plan: ProvisioningTeam
    create: OperationRecord
    assignment: OperationRecord
    memberships: tuple[tuple[ProvisioningMember, IdentityBindingRecord, OperationRecord], ...]


@dataclass(frozen=True)
class _PreparedProvisioning:
    organization: OperationRecord
    repository: OperationRecord
    teams: tuple[_PreparedTeam, ...]
    bundle: OperationRecord | None

    def dependencies(self) -> tuple[tuple[OperationRecord, tuple[str, ...]], ...]:
        rows: list[tuple[OperationRecord, tuple[str, ...]]] = [
            (self.repository, (self.organization.origin_id,))
        ]
        for team in self.teams:
            rows.append((team.create, (self.organization.origin_id,)))
            rows.append((team.assignment, (team.create.origin_id, self.repository.origin_id)))
            rows.extend(
                (membership, (team.create.origin_id,)) for _, _, membership in team.memberships
            )
        if self.bundle is not None:
            rows.append((self.bundle, (self.repository.origin_id,)))
        return tuple(rows)


def _operation_id(
    *,
    origin_id: str,
    target_instance_key: str,
    repository_scope: str,
    operation_type: str,
    mapping_version: int,
    intent: Mapping[str, Any],
) -> str:
    envelope = {
        "origin_id": origin_id,
        "target_instance_key": target_instance_key,
        "target_repository_scope": repository_scope,
        "operation_type": operation_type,
        "mapping_version": mapping_version,
        "intent": dict(intent),
    }
    digest = hashlib.sha256(canonical_json(envelope).encode()).hexdigest()[:24]
    return f"{operation_type}-{origin_id[-16:]}-{digest}"


def _prepare(
    state: StateStore,
    plan: ProvisioningPlan,
    *,
    origin_id: str,
    operation_type: str,
    repository_scope: str,
    mapping_version: int,
    intent: Mapping[str, Any],
) -> OperationRecord:
    return state.prepare_operation(
        operation_id=_operation_id(
            origin_id=origin_id,
            target_instance_key=plan.target_instance_key,
            repository_scope=repository_scope,
            operation_type=operation_type,
            mapping_version=mapping_version,
            intent=intent,
        ),
        origin_id=origin_id,
        target_instance_key=plan.target_instance_key,
        target_repository_scope=repository_scope,
        operation_type=operation_type,
        mapping_version=mapping_version,
        baseline_id=None,
        intent=intent,
    )


def _binding(state: StateStore, binding_id: str, target_instance_key: str) -> IdentityBindingRecord:
    binding = state.get_identity_binding(binding_id)
    if binding.target_instance_key != target_instance_key:
        raise OperationBlocked("team member binding points at another target instance")
    return binding


def _mapped(
    state: StateStore, origin_id: str, target_instance_key: str, object_type: str
) -> ObjectMappingRecord:
    mapping = state.get_object_mapping(origin_id, target_instance_key)
    if mapping.target_object_type != object_type:
        raise OperationBlocked(f"mapped target is not a {object_type}")
    return mapping


def _preflight_origins(state: StateStore, plan: ProvisioningPlan) -> None:
    expected = (
        (plan.organization_origin_id, "organization", plan.organization),
        (plan.repository_origin_id, "repository", plan.repository),
        *((team.origin_id, "team", plan.organization) for team in plan.teams),
    )
    source_instance_key: str | None = None
    for origin_id, object_type, repository_scope in expected:
        origin = state.get_origin(origin_id)
        if (
            origin.key.object_type != object_type
            or origin.key.origin_repository_scope != repository_scope
        ):
            raise OperationBlocked(f"{object_type} origin does not match the planned source scope")
        if source_instance_key is None:
            source_instance_key = origin.key.origin_instance_key
        elif origin.key.origin_instance_key != source_instance_key:
            raise OperationBlocked("provisioning origins do not share one source instance")


def _local_bindings(state: StateStore, plan: ProvisioningPlan) -> dict[str, IdentityBindingRecord]:
    bindings: dict[str, IdentityBindingRecord] = {}
    for team in plan.teams:
        for member in team.members:
            binding = _binding(state, member.binding_id, plan.target_instance_key)
            bindings[binding.binding_id] = binding
    return bindings


def _verify_remote_bindings(
    client: ProvisioningClient,
    bindings: Mapping[str, IdentityBindingRecord],
    administrator_id: int,
) -> None:
    for binding in bindings.values():
        if binding.target_account_id == administrator_id:
            raise OperationBlocked(
                "functional identity binding points at the technical administrator"
            )
        account = client.get_user(binding.target_login)
        verify_account(account, binding.target_login, binding.target_account_id)


def _prepare_plan(
    state: StateStore,
    plan: ProvisioningPlan,
    bindings: Mapping[str, IdentityBindingRecord],
) -> _PreparedProvisioning:
    organization = _prepare(
        state,
        plan,
        origin_id=plan.organization_origin_id,
        operation_type="create_organization",
        repository_scope=plan.organization,
        mapping_version=1,
        intent={"username": plan.organization, "visibility": "private"},
    )
    repository_name = plan.repository.split("/", 1)[1]
    repository = _prepare(
        state,
        plan,
        origin_id=plan.repository_origin_id,
        operation_type="create_repository",
        repository_scope=plan.repository,
        mapping_version=1,
        intent={"organization": plan.organization, "name": repository_name, "private": True},
    )
    prepared_teams: list[_PreparedTeam] = []
    for team in plan.teams:
        create = _prepare(
            state,
            plan,
            origin_id=team.origin_id,
            operation_type="create_team",
            repository_scope=plan.organization,
            mapping_version=1,
            intent={
                "organization": plan.organization,
                "name": team.name,
                "permission": team.permission,
            },
        )
        assignment = _prepare(
            state,
            plan,
            origin_id=team.origin_id,
            operation_type="assign_team_repository",
            repository_scope=plan.organization,
            mapping_version=1,
            intent={
                "team_origin_id": team.origin_id,
                "repository_origin_id": plan.repository_origin_id,
                "repository": plan.repository,
            },
        )
        memberships: list[tuple[ProvisioningMember, IdentityBindingRecord, OperationRecord]] = []
        for member in team.members:
            binding = bindings[member.binding_id]
            operation = _prepare(
                state,
                plan,
                origin_id=team.origin_id,
                operation_type="add_team_member",
                repository_scope=plan.organization,
                mapping_version=binding.mapping_version,
                intent={
                    "team_origin_id": team.origin_id,
                    "binding_id": binding.binding_id,
                    "target_login": binding.target_login,
                    "target_account_id": binding.target_account_id,
                    "repository": plan.repository,
                    "role": member.role,
                },
            )
            memberships.append((member, binding, operation))
        prepared_teams.append(_PreparedTeam(team, create, assignment, tuple(memberships)))
    bundle = None
    if plan.bundle is not None:
        bundle = _prepare(
            state,
            plan,
            origin_id=plan.repository_origin_id,
            operation_type="push_git_bundle",
            repository_scope=plan.repository,
            mapping_version=1,
            intent={
                "repository": plan.repository,
                "bundle_member": plan.bundle.member,
                "bundle_sha256": plan.bundle.sha256,
                "bundle_size": plan.bundle.size,
            },
        )
    return _PreparedProvisioning(organization, repository, tuple(prepared_teams), bundle)


def _ensure_dependencies(
    state: StateStore,
    operation: OperationRecord,
    parent_origin_ids: tuple[str, ...],
) -> OperationRecord:
    missing: list[str] = []
    for origin_id in parent_origin_ids:
        try:
            state.get_object_mapping(origin_id, operation.target_instance_key)
        except KeyError:
            missing.append(origin_id)
    current = state.get_operation(operation.operation_id)
    if missing:
        if current.status == "prepared":
            current = state.block_operation_on_parent(
                current.operation_id,
                "required parent mappings are not confirmed: " + ", ".join(missing),
            )
        raise OperationBlocked(
            f"operation {current.operation_id} is blocked by an unresolved parent"
        )
    if current.status == "blocked_parent":
        return state.unblock_operation_on_mappings(current.operation_id, parent_origin_ids)
    return current


def _block_unready_dependencies(state: StateStore, prepared: _PreparedProvisioning) -> None:
    for operation, parents in prepared.dependencies():
        current = state.get_operation(operation.operation_id)
        if current.status != "prepared":
            continue
        if any(_mapping_absent(state, parent, operation.target_instance_key) for parent in parents):
            state.block_operation_on_parent(
                operation.operation_id,
                "required parent mapping was not confirmed",
            )


def _mapping_absent(state: StateStore, origin_id: str, target_instance_key: str) -> bool:
    try:
        state.get_object_mapping(origin_id, target_instance_key)
    except KeyError:
        return True
    return False


def _verify_or_reject_target(
    state: StateStore,
    operation: OperationRecord,
    *,
    kind: str,
    name: str,
    lookup: Callable[[], Mapping[str, Any] | None],
    verify: Callable[[Mapping[str, Any]], str],
) -> None:
    current = state.get_operation(operation.operation_id)
    if current.status in ("outcome_unknown", "conflict"):
        raise OperationBlocked(f"{kind} operation is {current.status}; reconciliation is required")
    observed = lookup()
    if current.status == "confirmed":
        expected = _mapped(state, operation.origin_id, operation.target_instance_key, kind)
        reason = f"mapped target {kind} {name} no longer matches its confirmed identity"
        try:
            observed_id = None if observed is None else verify(observed)
        except ProvisioningTargetConflict as exc:
            _record_target_verification_conflict(
                state,
                operation,
                kind=kind,
                name=name,
                expected_id=expected.target_global_id,
                observed_id=str(observed.get("id")) if observed is not None else None,
                reason=reason,
            )
            raise OperationBlocked(reason) from exc
        if observed_id != expected.target_global_id:
            _record_target_verification_conflict(
                state,
                operation,
                kind=kind,
                name=name,
                expected_id=expected.target_global_id,
                observed_id=observed_id,
                reason=reason,
            )
            raise OperationBlocked(reason)
        return
    if observed is None:
        return
    observed_id = observed.get("id")
    reason = f"target {kind} {name} already exists but is not bound to this origin"
    state.mark_operation_conflict(
        operation.operation_id,
        conflict_type="target_name_collision",
        reason=reason,
        evidence={
            "kind": kind,
            "name": name,
            "observed_target_id": str(observed_id) if observed_id is not None else None,
        },
    )
    raise OperationBlocked(reason)


def _record_target_verification_conflict(
    state: StateStore,
    operation: OperationRecord,
    *,
    kind: str,
    name: str,
    expected_id: str,
    observed_id: str | None,
    reason: str,
) -> None:
    evidence = {
        "kind": kind,
        "name": name,
        "expected_target_id": expected_id,
        "observed_target_id": observed_id,
    }
    fingerprint = hashlib.sha256(canonical_json(evidence).encode()).hexdigest()[:24]
    state.record_conflict(
        f"mapped-target-{operation.operation_id}-{fingerprint}",
        operation_id=operation.operation_id,
        conflict_type="mapped_target_mismatch",
        reason=reason,
        evidence=evidence,
    )


def prepare_provisioning_operations(
    state: StateStore, plan: ProvisioningPlan
) -> tuple[tuple[OperationRecord, ...], dict[str, list[str]]]:
    """Expose the existing journal intents and dependencies to the application planner."""
    _preflight_origins(state, plan)
    prepared = _prepare_plan(state, plan, _local_bindings(state, plan))
    dependencies = prepared.dependencies()
    operations = (prepared.organization,) + tuple(operation for operation, _ in dependencies)
    creators = {
        operation.origin_id: operation.operation_id
        for operation in operations
        if operation.operation_type in ("create_organization", "create_repository", "create_team")
    }
    return operations, {
        operation.operation_id: [creators[parent] for parent in parents]
        for operation, parents in dependencies
    }


def preflight_provisioning_operations(
    state: StateStore, client: ProvisioningClient, plan: ProvisioningPlan
) -> None:
    """Check every selected structure before any write; recheck at the write boundary too."""
    _preflight_origins(state, plan)
    bindings = _local_bindings(state, plan)
    prepared = _prepare_plan(state, plan, bindings)
    administrator_id = verify_technical_administrator(
        client.get_authenticated_user(), client.authenticated_login
    )
    _verify_remote_bindings(client, bindings, administrator_id)
    organization = client.find_organization(plan.organization)
    try:
        _verify_or_reject_target(
            state,
            prepared.organization,
            kind="organization",
            name=plan.organization,
            lookup=lambda: organization,
            verify=lambda value: verify_organization(value, plan.organization),
        )
        _verify_or_reject_target(
            state,
            prepared.repository,
            kind="repository",
            name=plan.repository,
            lookup=lambda: client.find_repository(plan.repository),
            verify=lambda value: verify_repository(value, plan.repository, True),
        )
        for team in prepared.teams:

            def lookup_team(current: ProvisioningTeam = team.plan) -> Mapping[str, Any] | None:
                return (
                    client.find_team(plan.organization, current.name)
                    if organization is not None
                    else None
                )

            def verify_current_team(
                value: Mapping[str, Any], current: ProvisioningTeam = team.plan
            ) -> str:
                return verify_team(value, current.name, current.permission)

            _verify_or_reject_target(
                state,
                team.create,
                kind="team",
                name=f"{plan.organization}/{team.plan.name}",
                lookup=lookup_team,
                verify=verify_current_team,
            )
        for operation, _ in prepared.dependencies():
            if state.get_operation(operation.operation_id).status in (
                "outcome_unknown",
                "conflict",
            ):
                raise OperationBlocked("selected operation requires reconciliation")
    except OperationBlocked:
        _block_unready_dependencies(state, prepared)
        raise


def provision_selected_repository(
    state: StateStore,
    executor: OperationExecutor,
    client: ProvisioningClient,
    plan: ProvisioningPlan,
    *,
    bundle_pusher: BundlePusher | None = None,
) -> ProvisioningResult:
    """Apply one explicit selected-repository plan through the durable journal."""

    if client.authenticated_login == "":
        raise OperationBlocked("a separate technical provisioning administrator is required")
    if plan.bundle is not None and bundle_pusher is None:
        raise OperationBlocked("bundle content was planned but no bundle pusher was provided")
    preflight_provisioning_operations(state, client, plan)
    _preflight_origins(state, plan)
    bindings = _local_bindings(state, plan)
    prepared = _prepare_plan(state, plan, bindings)

    # Every possible write is durable before the first target read. This makes a
    # restart able to distinguish not-yet-sent work from an uncertain write.
    administrator_id = verify_technical_administrator(
        client.get_authenticated_user(), client.authenticated_login
    )
    _verify_remote_bindings(client, bindings, administrator_id)
    completed: list[OperationRecord] = []

    try:
        _verify_or_reject_target(
            state,
            prepared.organization,
            kind="organization",
            name=plan.organization,
            lookup=lambda: client.find_organization(plan.organization),
            verify=lambda value: verify_organization(value, plan.organization),
        )

        def create_organization(_: dict[str, Any]) -> TargetConfirmation:
            value = client.create_organization(plan.organization, "private")
            return TargetConfirmation(verify_organization(value, plan.organization), None)

        completed.append(executor.execute(prepared.organization.operation_id, create_organization))

        _ensure_dependencies(state, prepared.repository, (prepared.organization.origin_id,))
        _verify_or_reject_target(
            state,
            prepared.repository,
            kind="repository",
            name=plan.repository,
            lookup=lambda: client.find_repository(plan.repository),
            verify=lambda value: verify_repository(value, plan.repository, True),
        )
        repository_name = plan.repository.split("/", 1)[1]

        def create_repository(_: dict[str, Any]) -> TargetConfirmation:
            value = client.create_repository(plan.organization, repository_name, True)
            return TargetConfirmation(verify_repository(value, plan.repository, True), None)

        completed.append(executor.execute(prepared.repository.operation_id, create_repository))
        repository_mapping = _mapped(
            state, plan.repository_origin_id, plan.target_instance_key, "repository"
        )

        for team in prepared.teams:
            _ensure_dependencies(state, team.create, (prepared.organization.origin_id,))

            def find_team(current: ProvisioningTeam = team.plan) -> Mapping[str, Any] | None:
                return client.find_team(plan.organization, current.name)

            def verify_current_team(
                value: Mapping[str, Any], current: ProvisioningTeam = team.plan
            ) -> str:
                return verify_team(value, current.name, current.permission)

            _verify_or_reject_target(
                state,
                team.create,
                kind="team",
                name=f"{plan.organization}/{team.plan.name}",
                lookup=find_team,
                verify=verify_current_team,
            )

            def create_team(
                _: dict[str, Any], current: ProvisioningTeam = team.plan
            ) -> TargetConfirmation:
                value = client.create_team(plan.organization, current.name, current.permission)
                return TargetConfirmation(
                    verify_team(value, current.name, current.permission), None
                )

            completed.append(executor.execute(team.create.operation_id, create_team))
            team_mapping = _mapped(state, team.plan.origin_id, plan.target_instance_key, "team")
            _ensure_dependencies(
                state,
                team.assignment,
                (team.create.origin_id, prepared.repository.origin_id),
            )

            def assign_team_repository(
                _: dict[str, Any], current_mapping: ObjectMappingRecord = team_mapping
            ) -> TargetConfirmation:
                value = client.assign_team_repository(
                    current_mapping.target_global_id, plan.repository
                )
                verify_repository(value, plan.repository, True)
                return TargetConfirmation(current_mapping.target_global_id, None)

            completed.append(executor.execute(team.assignment.operation_id, assign_team_repository))

            for _, binding, membership in team.memberships:
                _ensure_dependencies(state, membership, (team.create.origin_id,))

                def add_team_member(
                    _: dict[str, Any],
                    current_binding: IdentityBindingRecord = binding,
                    current_mapping: ObjectMappingRecord = team_mapping,
                ) -> TargetConfirmation:
                    added = client.add_team_member(
                        current_mapping.target_global_id, current_binding.target_login
                    )
                    verify_account(
                        added,
                        current_binding.target_login,
                        current_binding.target_account_id,
                    )
                    return TargetConfirmation(current_mapping.target_global_id, None)

                completed.append(executor.execute(membership.operation_id, add_team_member))

        bundle_plan = plan.bundle
        if bundle_plan is not None and prepared.bundle is not None and bundle_pusher is not None:
            _ensure_dependencies(state, prepared.bundle, (prepared.repository.origin_id,))

            def push_bundle(_: dict[str, Any]) -> TargetConfirmation:
                return bundle_pusher.push(
                    plan.repository,
                    bundle_member=bundle_plan.member,
                    bundle_sha256=bundle_plan.sha256,
                    bundle_size=bundle_plan.size,
                    expected_repository_id=repository_mapping.target_global_id,
                )

            completed.append(executor.execute(prepared.bundle.operation_id, push_bundle))
    except OperationBlocked:
        _block_unready_dependencies(state, prepared)
        raise

    return ProvisioningResult(tuple(completed))
