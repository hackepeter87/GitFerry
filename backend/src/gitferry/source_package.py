"""Build the existing portable contracts from a selected native Gitea snapshot."""

from __future__ import annotations

import hashlib
from typing import Any

from gitferry import __version__
from gitferry.state_codec import origin_id
from gitferry.state_models import ObjectType
from gitferry.state_store import MappingConflict, StateStore


def _origin(
    state: StateStore,
    source: str,
    scope: str,
    kind: ObjectType,
    value: dict[str, Any],
    *,
    parent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = state.find_origin_for_replica(source, scope, kind, value["global_id"])
    parent_identifier = (
        origin_id(
            parent["instance_key"],
            parent["repository"],
            "comment" if parent["object_type"] == "issue_comment" else parent["object_type"],
            parent["global_id"],
        )
        if parent
        else None
    )
    if existing:
        key = existing.key
        if kind in ("organization", "repository", "team") and key.origin_repository_scope != scope:
            raise MappingConflict(
                "cross-scope structure copies are unsupported by application-package v1"
            )
        if (
            key.origin_instance_key == source
            and key.origin_created_at is not None
            and key.origin_created_at != value.get("created_at")
        ):
            raise MappingConflict("native source creation time conflicts with its immutable origin")
        if key.parent_origin_id != parent_identifier:
            raise MappingConflict("captured comment has another immutable parent")
        result: dict[str, Any] = {
            "instance_key": key.origin_instance_key,
            "repository": key.origin_repository_scope,
            "object_type": "issue_comment" if kind == "comment" else kind,
            "global_id": key.origin_global_id,
        }
        if key.issue_index is not None:
            result["issue_index"] = str(key.issue_index)
        if key.origin_created_at is not None:
            result["created_at"] = key.origin_created_at
    else:
        result = {
            "instance_key": source,
            "repository": scope,
            "object_type": "issue_comment" if kind == "comment" else kind,
            "global_id": value["global_id"],
        }
        if kind == "issue":
            result["issue_index"] = str(value["issue_index"])
        if "created_at" in value:
            result["created_at"] = value["created_at"]
    if parent and kind == "comment":
        result["parent_issue_origin"] = {
            key: parent[key] for key in ("instance_key", "repository", "object_type", "global_id")
        }
    return result


def build_source_documents(
    state: StateStore,
    snapshot: dict[str, Any],
    *,
    export_id: str,
    created_at: str,
    source: str,
    recipient: str,
    include_metadata: bool,
    include_labels: bool = False,
    include_attachments: bool = False,
    include_releases: bool = False,
    include_release_assets: bool = False,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    sender = state.get_instance(source)
    receiver = state.get_instance(recipient)
    realm = (
        "gitea-"
        + hashlib.sha256((source + ":" + sender.installation_marker).encode()).hexdigest()[:32]
    )
    identities: dict[str, dict[str, Any]] = {}

    def author(value: dict[str, str]) -> dict[str, str]:
        selector = {
            "realm_key": realm,
            "login": value["login"],
            "external_id": value["global_id"],
            # Existing v1/v2 selectors require a non-empty namespace path. The
            # root of a gitea-local realm is synthetic, not an LDAP/AD OU claim.
            "ou_path": "/",
        }
        claim = {
            **selector,
            "relationship": {
                "instance_key": source,
                "account_id": value["global_id"],
                "login": value["login"],
                "status": "observed-not-approved",
            },
        }
        previous = identities.setdefault(value["global_id"], claim)
        if previous != claim:
            raise MappingConflict("source account changed while capturing author relationships")
        return selector

    package: dict[str, Any] = {
        "format": "gitferry.application-package",
        "schema_version": 1,
        "package_id": export_id,
        "transfer_round_id": export_id,
        "producer_version": __version__,
        "created_at": created_at,
        "mode": "first-import",
        "sender": {"instance_key": source, "installation_marker": sender.installation_marker},
        "recipient": {
            "instance_key": recipient,
            "installation_marker": receiver.installation_marker,
        },
        "repositories": [],
        "realms": [],
        "metadata": None,
        "inventory_complete": False,
        "limitations": sorted(
            set(
                snapshot["limitations"]
                + [
                    "Native updated_at is used for capture drift checks, "
                    "not transported as a restorable field."
                ]
            )
        ),
        "deletion_semantics": "absence-does-not-delete",
    }
    metadata: dict[str, Any] = {
        "format": "gitferry.transport",
        "schema_version": 2,
        "manifest_id": "metadata-" + export_id,
        "transfer_round_id": export_id,
        "producer_version": __version__,
        "sender": package["sender"],
        "recipient": {**package["recipient"], "context": "Locally selected first-import recipient"},
        "mode": "forward",
        "created_at": created_at,
        "deletion_semantics": "absence-does-not-delete",
        "selected_repositories": [],
        "identity_claims": [],
        "objects": [],
        "payloads": [],
    }
    issue_authors = []
    if include_labels or include_attachments or include_releases or include_release_assets:
        package["content"] = []
    if include_labels:
        package["label_assignments"] = []
    for repository in snapshot["repositories"]:
        scope = repository["repository"]
        organization = repository["organization"]
        org_origin = _origin(state, source, organization["name"], "organization", organization)
        repo_origin = _origin(state, source, scope, "repository", repository)
        selected = {
            "repository": scope,
            "origin": {key: repo_origin[key] for key in ("instance_key", "global_id")},
            "organization": {
                "name": organization["name"],
                "origin": {key: org_origin[key] for key in ("instance_key", "global_id")},
            },
            "teams": [],
            "bundle": None,
        }
        for team in repository["teams"]:
            team_origin = _origin(state, source, organization["name"], "team", team)
            selected["teams"].append(
                {
                    "origin": {key: team_origin[key] for key in ("instance_key", "global_id")},
                    "name": team["name"],
                    "permission": team["permission"],
                    "members": [author(member) for member in team["members"]],
                }
            )
        package["repositories"].append(selected)
        label_origins_by_name: dict[str, dict[str, Any]] = {}
        if include_labels:
            for label in repository["labels"]:
                label_origin = _origin(state, source, scope, "label", label)
                if label["name"] in label_origins_by_name:
                    raise MappingConflict("source label names are ambiguous within a repository")
                label_origins_by_name[label["name"]] = label_origin
                package["content"].append(
                    {
                        "kind": "label",
                        "origin": {
                            key: label_origin[key]
                            for key in ("instance_key", "repository", "object_type", "global_id")
                        },
                        "repository": scope,
                        "source_native_id": label["global_id"],
                        "values": {key: label[key] for key in ("name", "color", "description")},
                    }
                )
        if not include_metadata:
            issues = []
        else:
            issues = repository["issues"]
        metadata["selected_repositories"].append(
            {
                "repository": scope,
                "object_types": ["issue", "issue_comment"],
                "fields": ["title", "body", "state", "labels", "comment.body"],
                "capabilities": ["issue-create", "issue-comment-create"],
                "limitations": ["native-created-at-not-restored", "normal-issue-comments-only"],
            }
        )
        issue_origins: dict[str, dict[str, Any]] = {}
        comment_origins: dict[str, dict[str, Any]] = {}
        for issue in issues:
            issue_origin = _origin(state, source, scope, "issue", issue)
            issue_origins[issue["global_id"]] = issue_origin
            values = {key: issue[key] for key in ("title", "body", "state", "labels")}
            issue_authors.append(
                {
                    "origin": {
                        key: issue_origin[key]
                        for key in ("instance_key", "repository", "object_type", "global_id")
                    },
                    "author": author(issue["author"]),
                }
            )
            metadata["objects"].append(_item(issue_origin, source, scope, issue, values, export_id))
            if include_labels and issue["labels"]:
                try:
                    assigned = [label_origins_by_name[name] for name in issue["labels"]]
                except KeyError:
                    raise MappingConflict(
                        "issue label assignment references an uncaptured label definition"
                    ) from None
                package["label_assignments"].append(
                    {
                        "repository": scope,
                        "issue_origin": {
                            key: issue_origin[key]
                            for key in ("instance_key", "repository", "object_type", "global_id")
                        },
                        "label_origins": [
                            {
                                key: label_origin[key]
                                for key in (
                                    "instance_key",
                                    "repository",
                                    "object_type",
                                    "global_id",
                                )
                            }
                            for label_origin in assigned
                        ],
                    }
                )
            for comment in issue["comments"]:
                comment_origin = _origin(
                    state, source, scope, "comment", comment, parent=issue_origin
                )
                comment_origins[comment["global_id"]] = comment_origin
                item = _item(
                    comment_origin,
                    source,
                    scope,
                    comment,
                    {"body": comment["body"]},
                    export_id,
                    issue_index=issue["issue_index"],
                )
                item["author"] = author(comment["author"])
                metadata["objects"].append(item)
                if include_attachments:
                    for asset in comment["attachments"]:
                        _append_asset(
                            package,
                            state,
                            source,
                            scope,
                            "attachment",
                            asset,
                            comment_origin,
                        )
            if include_attachments:
                for asset in issue["attachments"]:
                    _append_asset(
                        package,
                        state,
                        source,
                        scope,
                        "attachment",
                        asset,
                        issue_origin,
                    )
        if include_releases:
            for release in repository["releases"]:
                release_origin = _origin(state, source, scope, "release", release)
                package["content"].append(
                    {
                        "kind": "release",
                        "origin": {
                            key: release_origin[key]
                            for key in ("instance_key", "repository", "object_type", "global_id")
                        },
                        "repository": scope,
                        "source_native_id": release["global_id"],
                        "author": author(release["author"]),
                        "values": {
                            key: release[key]
                            for key in (
                                "tag_name",
                                "target_commitish",
                                "name",
                                "body",
                                "draft",
                                "prerelease",
                            )
                        },
                        "source_created_at": release["created_at"],
                        "source_published_at": release["published_at"],
                    }
                )
                if include_release_assets:
                    for asset in release["assets"]:
                        _append_asset(
                            package,
                            state,
                            source,
                            scope,
                            "release_asset",
                            asset,
                            release_origin,
                        )
    if identities:
        package["realms"] = [
            {
                "realm_key": realm,
                "kind": "gitea-local",
                "description": (
                    "Observed native Gitea accounts; no external directory identity "
                    "or local binding approval"
                ),
            }
        ]
    if include_metadata:
        metadata["identity_claims"] = list(identities.values())
        package["metadata"] = {
            "directory": "metadata",
            "manifest_id": metadata["manifest_id"],
            "digest_sha256": "0" * 64,
            "issue_authors": issue_authors,
        }
    return package, metadata if include_metadata else None


def _append_asset(
    package: dict[str, Any],
    state: StateStore,
    source: str,
    scope: str,
    kind: ObjectType,
    value: dict[str, Any],
    parent: dict[str, Any],
) -> None:
    asset_origin = _origin(state, source, scope, kind, value, parent=parent)
    package["content"].append(
        {
            "kind": kind,
            "origin": {
                key: asset_origin[key]
                for key in ("instance_key", "repository", "object_type", "global_id")
            },
            "repository": scope,
            "source_native_id": value["global_id"],
            "parent_origin": {
                key: parent[key]
                for key in ("instance_key", "repository", "object_type", "global_id")
            },
            "name": value["name"],
            "media_type": "application/octet-stream",
            "source_created_at": value["created_at"],
            "file": {
                "path": "assets/" + value["global_id"],
                "size": value["size"],
                "sha256": "0" * 64,
            },
        }
    )


def _item(
    origin: dict[str, Any],
    source: str,
    scope: str,
    native: dict[str, Any],
    values: dict[str, Any],
    round_key: str,
    *,
    issue_index: int | None = None,
) -> dict[str, Any]:
    comment = origin["object_type"] == "issue_comment"
    return {
        "origin": origin,
        "replicas": [
            {
                "instance_key": source,
                "repository": scope,
                "global_id": native["global_id"],
                "parent_issue_index" if comment else "issue_index": str(
                    issue_index if comment else native["issue_index"]
                ),
                "native_created_at": native["created_at"],
            }
        ],
        "comparison": {
            "ruleset": "gitea-issue-comment-body" if comment else "gitea-issue-selected-fields",
            "ruleset_version": 1,
            "original_baseline": values,
            "confirmed_import": None,
            "current": values,
            "baseline_origin": {"instance_key": source, "transfer_round_id": round_key},
        },
    }
