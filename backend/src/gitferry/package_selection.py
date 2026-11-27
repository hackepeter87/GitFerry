"""Pure admission of an inspected application package into a local plan request.

This module deliberately only reads the package and the local control state.  It does not
register imported origins, create bindings, prepare operations, or contact a remote.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gitferry.application_package import PackageArtifact, load_package_metadata
from gitferry.comment_operations import resolve_author_binding
from gitferry.git_return import compare_return_refs
from gitferry.state_codec import canonical_json, origin_id
from gitferry.state_store import OperationBlocked, StateStore

_BLOCKED_STATUSES = ("outcome_unknown", "executing", "conflict", "failed")


def _internal_type(value: str) -> str:
    return "comment" if value == "issue_comment" else value


def _origin(value: Mapping[str, Any]) -> str:
    return origin_id(
        str(value["instance_key"]),
        str(value["repository"]),
        _internal_type(str(value["object_type"])),
        str(value["global_id"]),
    )


def _selector(value: Mapping[str, Any]) -> str:
    # Canonical JSON is stable, safe to expose in a diagnostic, and retains every part of
    # the qualified selector (including an empty OU path).
    return canonical_json(
        {key: value[key] for key in ("realm_key", "login", "external_id", "ou_path")}
    )


def _manifest_objects(artifact: PackageArtifact, include: bool) -> list[dict[str, Any]]:
    if not include or artifact.document.get("metadata") is None:
        return []
    return list(load_package_metadata(artifact).document["objects"])


def _parent_key(item: Mapping[str, Any]) -> dict[str, Any] | None:
    origin = item.get("origin", {})
    parent = origin.get("parent_issue_origin")
    return dict(parent) if isinstance(parent, Mapping) else None


def build_package_selection(
    state: StateStore,
    artifact: PackageArtifact,
    package_ref: str,
    *,
    include_metadata: bool,
    apply_members: bool,
    author_credential_refs: dict[str, str],
) -> dict[str, Any]:
    """Build a bounded :class:`PlanRequest`-shaped selection and prerequisite report."""

    document = artifact.document
    if document.get("schema_version") == 5 and document.get("mode") == "return":
        return _build_return_selection(state, artifact, package_ref)
    target = str(document["recipient"]["instance_key"])
    missing_bindings: list[str] = []
    missing_credentials: list[str] = []
    waiting_parents: list[str] = []
    blocked_operations: list[str] = []
    credential_refs: dict[str, str] = {}
    selected_origins: set[str] = set()
    selected_issue_origins: set[str] = set()
    unsupported: list[str] = []
    repositories: list[dict[str, Any]] = []

    def binding_for(author: Mapping[str, Any], *, credential: bool = True) -> str | None:
        qualified = _selector(author)
        try:
            binding = resolve_author_binding(state, author, target)
        except (KeyError, IndexError, OperationBlocked):
            if qualified not in missing_bindings:
                missing_bindings.append(qualified)
            return None
        if credential:
            credential_ref = author_credential_refs.get(binding.binding_id)
            if credential_ref is None:
                if binding.binding_id not in missing_credentials:
                    missing_credentials.append(binding.binding_id)
            else:
                credential_refs[binding.binding_id] = credential_ref
        return binding.binding_id

    for selected in document["repositories"]:
        org = selected["organization"]
        repo_origin = _origin(
            {
                **selected["origin"],
                "repository": selected["repository"],
                "object_type": "repository",
            }
        )
        org_origin = _origin(
            {**org["origin"], "repository": org["name"], "object_type": "organization"}
        )
        selected_origins.update((org_origin, repo_origin))
        teams: list[dict[str, Any]] = []
        for team in selected.get("teams", []):
            team_origin = _origin(
                {**team["origin"], "repository": org["name"], "object_type": "team"}
            )
            selected_origins.add(team_origin)
            members: list[dict[str, str]] = []
            if apply_members:
                for member in team.get("members", []):
                    binding_id = binding_for(member, credential=False)
                    if binding_id is not None:
                        members.append(
                            {
                                "binding_id": binding_id,
                                "role": "write" if team["permission"] == "write" else "read",
                            }
                        )
            teams.append(
                {
                    "origin_id": team_origin,
                    "name": team["name"],
                    "permission": team["permission"],
                    "members": members,
                }
            )
        bundle = selected.get("bundle")
        repositories.append(
            {
                "organization_origin_id": org_origin,
                "organization": org["name"],
                "repository_origin_id": repo_origin,
                "repository": selected["repository"],
                "teams": teams,
                "bundle": (
                    {"member": bundle["path"], "sha256": bundle["sha256"], "size": bundle["size"]}
                    if bundle
                    else None
                ),
            }
        )

    objects = _manifest_objects(artifact, include_metadata)
    metadata: list[dict[str, Any]] = []
    content: list[dict[str, Any]] = []
    portable_content = artifact.document.get("content", [])
    assignments = artifact.document.get("label_assignments", []) if include_metadata else []
    manifest_issue_origins = {
        _origin(item["origin"])
        for item in objects
        if _internal_type(str(item.get("origin", {}).get("object_type", ""))) == "issue"
    }
    manifest_label_origins = {
        _origin(item["origin"]) for item in portable_content if item["kind"] == "label"
    }
    for assignment in assignments:
        issue_origin = _origin(assignment["issue_origin"])
        label_origins = {_origin(origin) for origin in assignment["label_origins"]}
        if (
            issue_origin not in manifest_issue_origins
            or not label_origins <= manifest_label_origins
        ):
            raise ValueError("label assignment references content outside the selected package")
        if assignment["issue_origin"]["repository"] != assignment["repository"] or any(
            origin["repository"] != assignment["repository"]
            for origin in assignment["label_origins"]
        ):
            raise ValueError("label assignment crosses repository boundaries")
    assigned_issues = {_origin(item["issue_origin"]) for item in assignments}
    if include_metadata and artifact.document.get("metadata") is not None:
        authors = {
            _origin({**entry["origin"], "object_type": "issue"}): entry["author"]
            for entry in artifact.document["metadata"].get("issue_authors", [])
        }
        for item in objects:
            portable_origin = item.get("origin", {})
            kind = _internal_type(str(portable_origin.get("object_type", "")))
            if kind not in ("issue", "comment"):
                raise ValueError("selected metadata object type is unsupported")
            item_origin = _origin(portable_origin)
            selected_origins.add(item_origin)
            if kind == "issue":
                selected_issue_origins.add(item_origin)
            author = authors.get(item_origin) if kind == "issue" else item.get("author")
            if not isinstance(author, Mapping):
                raise ValueError("selected metadata object has no explicit author")
            binding_for(author)
            comparison = item.get("comparison")
            values = comparison.get("current") if isinstance(comparison, Mapping) else None
            if not isinstance(values, Mapping):
                raise ValueError("selected metadata object has no current comparison values")
            selected_values = dict(values)
            if item_origin in assigned_issues:
                selected_values["labels"] = []
            metadata.append(
                {
                    "origin_id": item_origin,
                    "repository": portable_origin["repository"],
                    "author": dict(author),
                    "values": selected_values,
                    "baseline_id": None,
                }
            )
    selected_origins.update(_origin(item["origin"]) for item in portable_content)
    for item in portable_content:
        portable_origin = item["origin"]
        item_origin = _origin(portable_origin)
        content_item: dict[str, Any] = {
            "origin_id": item_origin,
            "repository": item["repository"],
            "kind": item["kind"],
            "baseline_id": None,
        }
        if item["kind"] in ("label", "release"):
            content_item["values"] = dict(item["values"])
        else:
            parent = item["parent_origin"]
            parent_id = _origin(parent)
            content_item["values"] = {
                "name": item["name"],
                "size": item["file"]["size"],
                "sha256": item["file"]["sha256"],
                "media_type": item["media_type"],
                "member": item["file"]["path"],
                "parent_origin_id": parent_id,
            }
            if parent_id not in selected_origins:
                try:
                    mapping = state.get_object_mapping(parent_id, target)
                except KeyError:
                    if parent_id not in waiting_parents:
                        waiting_parents.append(parent_id)
                else:
                    if mapping.target_repository_scope != item["repository"]:
                        if parent_id not in waiting_parents:
                            waiting_parents.append(parent_id)
        if item["kind"] == "release":
            content_item["author"] = dict(item["author"])
            binding_for(item["author"])
        content.append(content_item)

    for assignment in assignments:
        issue_origin = _origin(assignment["issue_origin"])
        assignment_label_origins = [_origin(origin) for origin in assignment["label_origins"]]
        content.append(
            {
                "origin_id": issue_origin,
                "repository": assignment["repository"],
                "kind": "label_assignment",
                "baseline_id": None,
                "values": {"label_origin_ids": assignment_label_origins},
            }
        )

    for item in objects:
        if _internal_type(str(item.get("origin", {}).get("object_type", ""))) != "comment":
            continue
        parent = _parent_key(item)
        if parent is None:
            continue
        parent_id = _origin({**parent, "object_type": "issue"})
        if parent_id in selected_issue_origins:
            continue
        repository = str(parent["repository"])
        try:
            mapping = state.get_object_mapping(parent_id, target)
        except KeyError:
            if parent_id not in waiting_parents:
                waiting_parents.append(parent_id)
        else:
            if mapping.target_repository_scope != repository or mapping.target_issue_index is None:
                if parent_id not in waiting_parents:
                    waiting_parents.append(parent_id)

    if selected_origins:
        with state.read_connection() as connection:
            rows = connection.execute(
                "SELECT operation_id,origin_id,status FROM operations WHERE target_instance_key=?",
                (target,),
            ).fetchall()
        for row in rows:
            if (
                row["origin_id"] in selected_origins
                and row["status"] in _BLOCKED_STATUSES
                and row["operation_id"] not in blocked_operations
            ):
                blocked_operations.append(row["operation_id"])

    if not include_metadata:
        source_limitations = list(document.get("limitations", []))
        source_limitations.append("issue and comment metadata was explicitly excluded")
    else:
        source_limitations = list(document.get("limitations", []))
    if not apply_members:
        source_limitations.append("team members were explicitly excluded")

    request = {
        "version": 1,
        "target_instance_key": target,
        "round_key": document["transfer_round_id"],
        "package_ref": package_ref,
        "package_digest": artifact.digest_sha256,
        "source_limitations": list(dict.fromkeys(source_limitations)),
        "repositories": repositories,
        "metadata": metadata,
        "content": content,
        "author_credential_refs": credential_refs,
        "unsupported_items": unsupported,
    }
    return {
        "request": request,
        "missing_bindings": missing_bindings,
        "missing_credentials": missing_credentials,
        "waiting_parents": waiting_parents,
        "blocked_operations": blocked_operations,
    }


def _build_return_selection(
    state: StateStore, artifact: PackageArtifact, package_ref: str
) -> dict[str, Any]:
    """Build a conservative return request; remote ref reads happen during preflight."""
    document = artifact.document
    target = str(document["recipient"]["instance_key"])
    repositories: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for selected in document["repositories"]:
        origin = selected["origin"]
        repository = selected["repository"]
        origin_identifier = origin_id(
            origin["instance_key"], repository, "repository", origin["global_id"]
        )
        try:
            mapping = state.get_object_mapping(origin_identifier, target)
        except KeyError:
            raise ValueError("return repository has no local target mapping") from None
        if mapping.target_repository_scope != repository:
            raise ValueError("return repository mapping scope conflicts with local state")
        comparison = selected["comparison"]
        plan = compare_return_refs(
            comparison["original_baseline"],
            comparison["confirmed_import"],
            comparison["desired"],
            comparison["original_baseline"],
        )
        conflicts.extend(
            {"ref": item.ref, "code": item.code, "reason": item.reason}
            for item in plan.conflicts
        )
        bundle = selected.get("bundle")
        repository_item = {
            "repository_origin_id": origin_identifier,
            "repository": repository,
            "target_repository_id": mapping.target_global_id,
            "bundle": bundle,
            "comparison": comparison,
            "changes": list(plan.changes),
        }
        repositories.append(repository_item)
    request = {
        "version": 1,
        "mode": "return",
        "target_instance_key": target,
        "round_key": document["transfer_round_id"],
        "package_ref": package_ref,
        "package_digest": artifact.digest_sha256,
        "repositories": repositories,
        "metadata": [],
        "content": [],
        "author_credential_refs": {},
        "unsupported_items": list(document["limitations"]),
        "return_conflicts": conflicts,
        "operation_ids": [],
        "forward_reference": document["forward_reference"],
    }
    return {
        "request": request,
        "missing_bindings": [],
        "missing_credentials": [],
        "waiting_parents": [],
        "blocked_operations": [],
        "return_conflicts": conflicts,
        "remote_preflight_required": True,
    }
