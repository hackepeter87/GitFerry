"""Build local plans from explicit selections, never from imported execution authority."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gitferry.comment_operations import prepare_comment_operation, resolve_author_binding
from gitferry.gitea_provisioning import _repository_segments
from gitferry.issue_operations import prepare_issue_operation
from gitferry.provisioning import (
    BundleIntent,
    ProvisioningMember,
    ProvisioningPlan,
    ProvisioningTeam,
    prepare_provisioning_operations,
)
from gitferry.state_store import OperationBlocked, OperationRecord, StateStore
from gitferry.transfer_plans import PlanConflict, PlanService

Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=256)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class MemberInput(_Input):
    binding_id: Text
    role: Literal["read", "write"]


class TeamInput(_Input):
    origin_id: Text
    name: Text
    permission: Literal["read", "write"]
    members: list[MemberInput] = Field(default_factory=list, max_length=10000)


class BundleInput(_Input):
    member: Text
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size: Annotated[int, Field(gt=0)]


class RepositoryInput(_Input):
    organization_origin_id: Text
    organization: Text
    repository_origin_id: Text
    repository: Text
    teams: list[TeamInput] = Field(default_factory=list, max_length=1000)
    bundle: BundleInput | None = None

    def provisioning(self, target: str) -> ProvisioningPlan:
        return ProvisioningPlan(
            target,
            self.organization_origin_id,
            self.organization,
            self.repository_origin_id,
            self.repository,
            tuple(
                ProvisioningTeam(
                    team.origin_id,
                    team.name,
                    team.permission,
                    tuple(
                        ProvisioningMember(member.binding_id, member.role)
                        for member in team.members
                    ),
                )
                for team in self.teams
            ),
            BundleIntent(**self.bundle.model_dump()) if self.bundle else None,
        )


class AuthorInput(_Input):
    realm_key: Text
    login: Text
    external_id: Text
    ou_path: str


class MetadataInput(_Input):
    origin_id: Text
    repository: Text
    author: AuthorInput
    values: dict[str, Any]
    baseline_id: Text | None = None


class PlanRequest(_Input):
    version: Literal[1]
    target_instance_key: Reference
    round_key: Text
    package_ref: Reference | None = None
    package_digest: Optional[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = None  # noqa: UP045
    source_limitations: list[Text] = Field(default_factory=list, max_length=1000)
    repositories: list[RepositoryInput] = Field(default_factory=list, max_length=1000)
    metadata: list[MetadataInput] = Field(default_factory=list, max_length=10000)
    author_credential_refs: dict[str, Reference] = Field(default_factory=dict)
    unsupported_items: list[Text] = Field(default_factory=list, max_length=1000)


def parse_plan_request(value: Mapping[str, Any]) -> PlanRequest:
    try:
        parsed = PlanRequest.model_validate(value)
    except ValidationError:
        raise PlanConflict(
            "invalid plan selection; unknown fields and embedded credentials are rejected"
        ) from None
    if len({item.repository for item in parsed.repositories}) != len(parsed.repositories):
        raise PlanConflict("selected repository scopes must be unique")
    if len({item.origin_id for item in parsed.metadata}) != len(parsed.metadata):
        raise PlanConflict("selected metadata origins must be unique")
    for repository in [item.repository for item in parsed.repositories] + [
        item.repository for item in parsed.metadata
    ]:
        _repository_segments(repository)
    if any(item.bundle for item in parsed.repositories) and parsed.package_ref is None:
        raise PlanConflict("bundle selections require an owned local package reference")
    if parsed.package_digest is not None and parsed.package_ref is None:
        raise PlanConflict("package digest requires an owned package reference")
    return parsed


def _metadata_operation(
    state: StateStore, request: PlanRequest, item: MetadataInput
) -> OperationRecord:
    origin = state.get_origin(item.origin_id)
    if origin.key.object_type not in ("issue", "comment"):
        raise PlanConflict("only issues and normal issue comments are supported metadata")
    author = item.author.model_dump()
    binding = resolve_author_binding(state, author, request.target_instance_key)
    if binding.binding_id not in request.author_credential_refs:
        raise OperationBlocked(
            "selected author requires a local credential reference; no fallback author is permitted"
        )
    kind = origin.key.object_type
    try:
        state.get_object_mapping(origin.id, request.target_instance_key)
        mapped = True
    except KeyError:
        mapped = False
    with state.read_connection() as connection:
        creates = list(
            connection.execute(
                "SELECT operation_id,intent_json,status FROM operations WHERE origin_id=? "
                "AND target_instance_key=? AND operation_type=?",
                (origin.id, request.target_instance_key, f"create_{kind}"),
            )
        )
    if mapped and item.baseline_id is None:
        # Exact initial-import replay uses its confirmed create, not a new origin
        # or a fabricated update baseline. Changed values require an explicit round.
        matching = []
        for row in creates:
            operation = state.get_operation(row["operation_id"])
            expected = (
                operation.intent.get("values")
                if kind == "issue"
                else {"body": operation.intent.get("body")}
            )
            if (
                expected == item.values
                and operation.intent.get("author_binding_id") == binding.binding_id
                and operation.target_repository_scope == item.repository
            ):
                matching.append(operation)
        if len(matching) == 1 and matching[0].status == "confirmed":
            return matching[0]
        raise OperationBlocked("existing metadata requires an explicit baseline for a new change")
    if any(
        row["status"] in ("outcome_unknown", "executing", "conflict", "failed") for row in creates
    ):
        raise OperationBlocked("an earlier metadata create requires reconciliation")
    operation_type = f"update_{kind}" if mapped else f"create_{kind}"
    if kind == "issue":
        return prepare_issue_operation(
            state,
            origin_id=origin.id,
            target_instance_key=request.target_instance_key,
            target_repository_scope=item.repository,
            operation_type=operation_type,
            author=author,
            baseline_id=item.baseline_id,
            values=item.values,
        )
    if set(item.values) != {"body"} or not isinstance(item.values["body"], str):
        raise PlanConflict("normal comments support only text body content")
    expected_body = None
    if mapped:
        if item.baseline_id is None:
            raise OperationBlocked("comment change requires an immutable baseline")
        baseline = state.get_baseline(item.baseline_id)
        if (
            baseline.origin_id != origin.id
            or baseline.ruleset_name != "gitea-issue-comment-body"
            or baseline.ruleset_version != 1
        ):
            raise PlanConflict("comment baseline origin or comparison rules do not match")
        expected_body = baseline.baseline.get("body")
        if not isinstance(expected_body, str):
            raise PlanConflict("comment baseline must contain the original body")
    return prepare_comment_operation(
        state,
        origin_id=origin.id,
        target_instance_key=request.target_instance_key,
        target_repository_scope=item.repository,
        operation_type=operation_type,
        author=author,
        baseline_id=item.baseline_id,
        body=item.values["body"],
        expected_body=expected_body,
    )


def prepare_transfer_plan(
    state: StateStore,
    value: Mapping[str, Any],
    *,
    expected_profile_revision: int | None = None,
    content_inventory: dict[str, Any] | None = None,
    native_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request = parse_plan_request(value)
    with state.read_connection() as connection:
        profile = connection.execute(
            "SELECT revision FROM connection_profiles WHERE instance_key=?",
            (request.target_instance_key,),
        ).fetchone()
        evidence = connection.execute(
            "SELECT approved_identity FROM connection_checks WHERE instance_key=?",
            (request.target_instance_key,),
        ).fetchone()
    if profile is None or evidence is None or not evidence["approved_identity"]:
        raise PlanConflict("test and locally approve the target connection first")
    if expected_profile_revision is not None and profile["revision"] != expected_profile_revision:
        raise PlanConflict("target profile changed since package selection review")
    digest = hashlib.sha256(evidence["approved_identity"].encode()).hexdigest()
    operations: dict[str, OperationRecord] = {}
    dependencies: dict[str, list[str]] = {}
    for item in sorted(request.repositories, key=lambda selected: selected.repository):
        prepared, graph = prepare_provisioning_operations(
            state, item.provisioning(request.target_instance_key)
        )
        operations.update({operation.operation_id: operation for operation in prepared})
        dependencies.update(graph)
    if native_payloads is not None:
        from gitferry.native_operations import prepare_native_operations

        by_scope = {entry["repository"]: entry for entry in native_payloads}
        if len(by_scope) != len(native_payloads) or set(by_scope) != {
            item.repository for item in request.repositories
        }:
            raise PlanConflict("native payload selection differs from the selected repositories")
        for item in request.repositories:
            native_prepared, graph = prepare_native_operations(
                state, item.provisioning(request.target_instance_key), by_scope[item.repository]
            )
            operations.update({operation.operation_id: operation for operation in native_prepared})
            dependencies.update(graph)
    for metadata in sorted(request.metadata, key=lambda selected: selected.origin_id):
        operation = _metadata_operation(state, request, metadata)
        operations[operation.operation_id] = operation
    creators = {
        operation.origin_id: operation.operation_id
        for operation in operations.values()
        if operation.operation_type
        in ("create_organization", "create_repository", "create_team", "create_issue")
    }
    repositories = {
        operation.target_repository_scope: operation.operation_id
        for operation in operations.values()
        if operation.operation_type == "create_repository"
    }
    if native_payloads is not None:
        from gitferry.native_operations import NATIVE_OPERATION_TYPES

        for operation in operations.values():
            if operation.operation_type in NATIVE_OPERATION_TYPES:
                parent = repositories.get(operation.target_repository_scope)
                if parent:
                    dependencies.setdefault(operation.operation_id, []).append(parent)
            if operation.operation_type == "push_git_bundle":
                dependencies.setdefault(operation.operation_id, []).extend(
                    other.operation_id
                    for other in operations.values()
                    if other.operation_type == "upload_lfs_object"
                    and other.target_repository_scope == operation.target_repository_scope
                )
    for operation in operations.values():
        parents = dependencies.setdefault(operation.operation_id, [])
        if operation.operation_type in (
            "create_issue",
            "update_issue",
            "create_comment",
            "update_comment",
        ):
            parent_repository = repositories.get(operation.target_repository_scope)
            if parent_repository:
                parents.append(parent_repository)
        if operation.operation_type in ("create_comment", "update_comment"):
            parent_origin = str(operation.intent["parent_origin_id"])
            parent_operation = creators.get(parent_origin)
            if parent_operation:
                if (
                    operations[parent_operation].target_repository_scope
                    != operation.target_repository_scope
                ):
                    raise PlanConflict(
                        "comment and selected parent point at different repositories"
                    )
                parents.append(parent_operation)
            else:
                try:
                    parent_mapping = state.get_object_mapping(
                        parent_origin, request.target_instance_key
                    )
                    if (
                        parent_mapping.target_repository_scope != operation.target_repository_scope
                        or parent_mapping.target_issue_index is None
                    ):
                        raise PlanConflict("comment parent mapping points at another repository")
                except KeyError:
                    raise PlanConflict(
                        "comment parent is neither selected nor locally mapped"
                    ) from None
    inventory = {
        "request": request.model_dump(mode="json"),
        "included_operations": sorted(
            {operation.operation_type for operation in operations.values()}
        ),
        "inventory_complete": False,
        "content_inventory": content_inventory,
        "limitations": [
            "Selected explicit inputs only; not a complete remote inventory",
            "Native historical creation timestamps are not restored",
            "No general exactly-once guarantee or atomic comment PATCH precondition",
            *request.source_limitations,
        ],
    }
    native_bytes = 0
    if native_payloads is not None:
        inventory["native_payloads"] = native_payloads
        native_bytes = sum(
            sum(p["size"] for p in item.get("lfs") or [])
            + (item["wiki"]["bundle"]["size"] if item.get("wiki") and item["wiki"]["bundle"] else 0)
            for item in native_payloads
        )
    required_bytes = (
        3 * native_bytes
        + 64 * 1024 * 1024
        + 3 * sum(item.bundle.size for item in request.repositories if item.bundle)
    )
    return PlanService(state).create(
        target_instance_key=request.target_instance_key,
        profile_revision=profile["revision"],
        evidence_digest=digest,
        round_key=request.round_key,
        inventory=inventory,
        operation_ids=list(operations),
        dependencies=dependencies,
        required_bytes=required_bytes,
    )
