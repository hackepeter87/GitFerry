"""Portable inventory facts bound to package bytes, never to local approvals."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from gitferry.content_inventory import (
    InventoryDocument,
    InventoryError,
    export_classes,
    inventory_digest,
    inventory_summary,
    parse_inventory,
)
from gitferry.state_codec import canonical_json
from gitferry.transport import ManifestValidationError, _no_duplicate_keys

MAX_INVENTORY_BYTES = 16 * 1024 * 1024


def attach_inventory(
    directory: Path,
    package: dict[str, Any],
    document: InventoryDocument,
    snapshot: dict[str, Any],
    *,
    include_members: bool,
    include_lfs: bool = False,
    include_wiki: bool = False,
    include_labels: bool = False,
    include_attachments: bool = False,
    include_releases: bool = False,
    include_release_assets: bool = False,
) -> None:
    """Write one immutable payload; source replica IDs do not replace permanent origins."""
    raw = (canonical_json(document.model_dump(mode="json")) + "\n").encode("utf-8")
    if len(raw) > MAX_INVENTORY_BYTES:
        raise InventoryError("inventory exceeds the payload limit")
    parent = directory / "inventory"
    parent.mkdir(mode=0o700)
    with (parent / "document.json").open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    package["schema_version"] = 2
    package["inventory"] = {
        "include_members": include_members,
        "path": "inventory/document.json",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_digest": inventory_digest(document),
        "snapshot_id": document.snapshot_id,
    }
    if include_lfs or include_wiki:
        package["schema_version"] = 3
        package["inventory"].update(include_lfs=include_lfs, include_wiki=include_wiki)
    if include_labels or include_attachments or include_releases or include_release_assets:
        package["schema_version"] = 4
        package["inventory"].update(
            include_lfs=include_lfs,
            include_wiki=include_wiki,
            include_labels=include_labels,
            include_attachments=include_attachments,
            include_releases=include_releases,
            include_release_assets=include_release_assets,
        )
        package.setdefault("content", [])
    native = {item["repository"]: item for item in snapshot["repositories"]}
    for selected in package["repositories"]:
        captured = native[selected["repository"]]
        selected["source_native_id"] = captured["global_id"]
        selected["organization"]["source_native_id"] = captured["organization"]["global_id"]
        teams = {item["name"]: item["global_id"] for item in captured["teams"]}
        for team in selected["teams"]:
            team["source_native_id"] = teams[team["name"]]


def read_inventory(directory: Path, package: dict[str, Any]) -> InventoryDocument | None:
    from gitferry.application_package import PackageError, _file, _read_bounded

    # v1 predates the portable inventory. v5 is the deliberately narrow Git
    # return contract and carries its comparison facts directly in package.json.
    if package["schema_version"] in (1, 5):
        return None
    reference = package["inventory"]
    raw = _read_bounded(_file(directory, reference["path"]), MAX_INVENTORY_BYTES)
    if (len(raw), hashlib.sha256(raw).hexdigest()) != (reference["size"], reference["sha256"]):
        raise PackageError("inventory payload size or byte digest differs")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
        if not isinstance(value, dict):
            raise ValueError("object required")
        document = parse_inventory(value)
    except (ValueError, TypeError, RecursionError, ManifestValidationError):
        raise PackageError("invalid bounded inventory payload") from None
    if (
        document.snapshot_id != reference["snapshot_id"]
        or inventory_digest(document) != reference["content_digest"]
        or document.source_instance_key != package["sender"]["instance_key"]
        or document.source_installation_marker != package["sender"]["installation_marker"]
    ):
        raise PackageError("inventory identity differs from its package")
    if not inventory_summary(document)["transfer_ready"]:
        raise PackageError("partial or unsupported inventory cannot authorize this package")
    return document


def validate_inventory_contents(
    directory: Path,
    package: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> InventoryDocument | None:
    """Reconcile observed native copies with payloads without inventing new origins."""
    from gitferry.application_package import PackageError

    document = read_inventory(directory, package)
    if document is None:
        return None
    repositories = {item.repository: item for item in document.repositories}
    if set(repositories) != {item["repository"] for item in package["repositories"]}:
        raise PackageError("inventory repository selection differs from package")
    metadata = manifest is not None
    claims = (
        {
            canonical_json(
                {k: claim[k] for k in ("realm_key", "login", "external_id", "ou_path")}
            ): claim.get("relationship")
            for claim in manifest["identity_claims"]
        }
        if manifest
        else {}
    )
    authors = (
        {
            canonical_json(item["origin"]): item["author"]
            for item in package["metadata"]["issue_authors"]
        }
        if metadata
        else {}
    )
    all_items = manifest["objects"] if manifest else []
    for selected in package["repositories"]:
        observed = repositories[selected["repository"]]
        classes = {item.content_class: item for item in observed.classes}
        # Membership presence cannot decide whether an empty membership selection was requested.
        members = package["inventory"]["include_members"]
        if sorted(observed.requested_classes) != export_classes(
            metadata=metadata,
            members=members,
            lfs=package["inventory"].get("include_lfs", False),
            wiki=package["inventory"].get("include_wiki", False),
            labels=package["inventory"].get("include_labels", False),
            attachments=package["inventory"].get("include_attachments", False),
            releases=package["inventory"].get("include_releases", False),
            release_assets=package["inventory"].get("include_release_assets", False),
        ):
            raise PackageError("inventory classes differ from bounded package capabilities")
        if package["schema_version"] in (3, 4):
            from gitferry.package_native import match_native_inventory

            match_native_inventory(document, selected)
        if package["schema_version"] == 4:
            from gitferry.package_metadata import content_by_repository, match_metadata_inventory

            match_metadata_inventory(
                observed, content_by_repository(package, selected["repository"])
            )
        if bool(expected_git_refs(document, observed.repository)) != (
            selected["bundle"] is not None
        ):
            raise PackageError("Git payload presence differs from inventory refs")
        if observed.native_id != selected["source_native_id"]:
            raise PackageError("inventory source repository copy differs")
        organization = selected["organization"]
        orgs = classes["organizations"].objects
        if (
            len(orgs) != 1
            or orgs[0].native_id != organization["source_native_id"]
            or orgs[0].facts.get("name") != organization["name"]
        ):
            raise PackageError("inventory source organization differs")
        teams = {
            item.native_id: item
            for item in classes["teams"].objects
            if item.facts.get("permission") not in {"admin", "owner"}
            and item.facts.get("units_map", {}).get("repo.code") not in {"admin", "owner"}
        }
        if set(teams) != {item["source_native_id"] for item in selected["teams"]}:
            raise PackageError("inventory teams differ from package")
        for team in selected["teams"]:
            source = teams[team["source_native_id"]]
            if (
                source.facts.get("name") != team["name"]
                or source.facts.get("units_map", {}).get("repo.code") != team["permission"]
            ):
                raise PackageError("inventory team rights differ")
            if team["members"] and "identities" not in observed.requested_classes:
                raise PackageError("package membership is outside inventory scope")
            expected_members = {
                (m["global_id"], m["login"]) for m in source.facts.get("members", [])
            }
            if {(m["external_id"], m["login"]) for m in team["members"]} != (
                expected_members if members else set()
            ):
                raise PackageError("package membership set differs from selected inventory")
            for member in team["members"]:
                if (member["external_id"], member["login"]) not in expected_members:
                    raise PackageError("package member differs from observed source account")
        if not metadata:
            continue
        items = [item for item in all_items if item["origin"]["repository"] == observed.repository]
        by_kind: dict[str, dict[str, tuple[dict[str, Any], dict[str, Any]]]] = {
            "issue": {},
            "issue_comment": {},
        }
        issue_origins: dict[str, str] = {}
        for item in items:
            origin = item["origin"]
            kind = origin["object_type"]
            replicas = [
                r
                for r in item["replicas"]
                if r["instance_key"] == document.source_instance_key
                and r["repository"] == observed.repository
            ]
            if len(replicas) != 1 or replicas[0]["global_id"] in by_kind[kind]:
                raise PackageError("metadata lacks a unique observed source copy")
            replica = replicas[0]
            by_kind[kind][replica["global_id"]] = (item, replica)
            if kind == "issue":
                issue_origins[replica["global_id"]] = canonical_json(
                    {
                        k: origin[k]
                        for k in ("instance_key", "repository", "object_type", "global_id")
                    }
                )
        for kind, name in (("issue", "issues"), ("issue_comment", "comments")):
            native = {item.native_id: item for item in classes[name].objects}
            if set(native) != set(by_kind[kind]):
                raise PackageError("metadata object set differs from inventory")
            for native_id, source in native.items():
                if native_id is None:
                    raise PackageError("metadata native ID is required")
                item, replica = by_kind[kind][native_id]
                values = item["comparison"]["current"]
                fields = ("title", "body", "state") if kind == "issue" else ("body",)
                if any(values.get(key) != source.facts.get(key) for key in fields) or replica[
                    "native_created_at"
                ] != source.facts.get("created_at"):
                    raise PackageError("metadata content differs from inventory")
                if kind == "issue":
                    selector = authors[issue_origins[native_id]]
                    if replica["issue_index"] != source.issue_index:
                        raise PackageError("issue relation differs from inventory")
                    if values.get("labels"):
                        selected_labels = {
                            item["values"]["name"]
                            for item in package.get("content", [])
                            if item["kind"] == "label" and item["repository"] == observed.repository
                        }
                        if set(values["labels"]) - selected_labels:
                            raise PackageError("issue labels lack portable label definitions")
                else:
                    selector = item["author"]
                    if (
                        source.parent is None
                        or source.parent["content_class"] != "issues"
                        or issue_origins.get(source.parent["key"])
                        != canonical_json(item["origin"]["parent_issue_origin"])
                    ):
                        raise PackageError("comment parent differs from inventory")
                    parent = next(
                        p for p in classes["issues"].objects if p.key == source.parent["key"]
                    )
                    if replica["parent_issue_index"] != parent.issue_index:
                        raise PackageError("comment parent index differs from inventory")
                claim = claims.get(canonical_json(selector))
                if (
                    not source.author
                    or not claim
                    or claim.get("instance_key") != document.source_instance_key
                    or (claim.get("account_id"), claim.get("login"))
                    != (source.author["account_id"], source.author["login"])
                ):
                    raise PackageError("metadata author differs from observed source account")
    return document


def inventory_report(directory: Path, package: dict[str, Any]) -> dict[str, Any] | None:
    """Small portable coverage report; identities and local execution grants stay separate."""
    document = read_inventory(directory, package)
    if document is None:
        return None
    return {
        "membership_records_included": package["inventory"]["include_members"],
        "snapshot_id": document.snapshot_id,
        "content_digest": inventory_digest(document),
        "source_instance_key": document.source_instance_key,
        "comparison_version": document.comparison_version,
        "summary": inventory_summary(document),
        "repositories": [
            {
                "repository": repo.repository,
                "native_id": repo.native_id,
                "git_size_estimate_bytes": repo.git_size_estimate_bytes,
                "size_assumptions": repo.size_assumptions,
                "classes": [
                    {
                        "content_class": item.content_class,
                        "selected": item.content_class in repo.requested_classes,
                        "status": item.status,
                        "count": item.count,
                        "reason": item.reason,
                        "limitations": item.limitations,
                    }
                    for item in repo.classes
                ],
            }
            for repo in document.repositories
        ],
    }


def expected_git_refs(document: InventoryDocument, repository: str) -> dict[str, str]:
    from gitferry.application_package import PackageError

    selected = next((r for r in document.repositories if r.repository == repository), None)
    if selected is None:
        raise PackageError("repository is outside inventory scope")
    refs = next(c for c in selected.classes if c.content_class == "git_refs")
    expected: dict[str, str] = {}
    for item in refs.objects:
        ref, sha = item.facts.get("ref"), item.facts.get("sha")
        if not isinstance(ref, str) or not isinstance(sha, str) or ref != item.key:
            raise PackageError("inventory Git ref identity is malformed")
        expected[ref] = sha
    return expected
