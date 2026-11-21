"""Pure admission of an inspected application package into a local plan request.

This module deliberately only reads the package and the local control state.  It does not
register imported origins, create bindings, prepare operations, or contact a remote.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gitferry.application_package import PackageArtifact, load_package_metadata
from gitferry.comment_operations import resolve_author_binding
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
            metadata.append(
                {
                    "origin_id": item_origin,
                    "repository": portable_origin["repository"],
                    "author": dict(author),
                    "values": dict(values),
                    "baseline_id": None,
                }
            )
            if kind == "issue" and values.get("labels"):
                # Labels are intentionally represented as an unsupported selection; the
                # adapter cannot claim label creation support for package admission.
                if "labels" not in unsupported:
                    unsupported.append("labels")

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
