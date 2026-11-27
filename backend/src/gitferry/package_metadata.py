"""Portable label, release, and asset descriptors for application-package v4."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from gitferry.application_package import PackageError, _digest, _file, package_path
from gitferry.content_inventory import RepositoryInventory
from gitferry.metadata_operations import asset_values, label_values, release_values
from gitferry.state_codec import canonical_json

CONTENT_KINDS = frozenset({"label", "release", "attachment", "release_asset"})


def _origin(value: Mapping[str, Any]) -> tuple[str, str, str, str]:
    required = {"instance_key", "repository", "object_type", "global_id"}
    if set(value) != required or value.get("object_type") not in CONTENT_KINDS:
        raise PackageError("portable content origin is invalid")
    parts = tuple(value[key] for key in ("instance_key", "repository", "object_type", "global_id"))
    if any(not isinstance(item, str) or not item for item in parts):
        raise PackageError("portable content origin is invalid")
    return cast(tuple[str, str, str, str], parts)


def validate_metadata_descriptors(document: dict[str, Any]) -> None:
    if document["schema_version"] != 4:
        return
    flags = document["inventory"]
    content = document["content"]
    origins: set[tuple[str, str, str, str]] = set()
    source_copies: set[tuple[str, str, str]] = set()
    members: dict[str, tuple[int, str]] = {}
    selected_kinds = {
        kind
        for kind, flag in (
            ("label", "include_labels"),
            ("attachment", "include_attachments"),
            ("release", "include_releases"),
            ("release_asset", "include_release_assets"),
        )
        if flags[flag]
    }
    if {item["kind"] for item in content} - selected_kinds:
        raise PackageError("portable content differs from the selected inventory classes")
    for item in content:
        kind = item["kind"]
        origin = _origin(item["origin"])
        if origin[2] != kind or item["repository"] != origin[1] or origin in origins:
            raise PackageError("portable content has duplicate or contradictory origins")
        origins.add(origin)
        source = (item["repository"], kind, item["source_native_id"])
        if source in source_copies:
            raise PackageError("portable content has duplicate source copies")
        source_copies.add(source)
        if kind == "label":
            label_values(item["values"])
        elif kind == "release":
            release_values(item["values"])
        else:
            parent = item["parent_origin"]
            if (
                not isinstance(parent, Mapping)
                or set(parent) != {"instance_key", "repository", "object_type", "global_id"}
                or parent["instance_key"] != origin[0]
                or parent["repository"] != item["repository"]
                or parent["object_type"]
                not in ({"release"} if kind == "release_asset" else {"issue", "issue_comment"})
            ):
                raise PackageError("portable asset parent is invalid")
            file = item["file"]
            member = str(package_path(file["path"]))
            if not member.startswith("assets/"):
                raise PackageError("asset payload must use the assets directory")
            facts = (file["size"], file["sha256"])
            if member in members and members[member] != facts:
                raise PackageError("one asset member has conflicting payload facts")
            members[member] = facts
            asset_values(
                {
                    "name": item["name"],
                    "size": file["size"],
                    "sha256": file["sha256"],
                    "media_type": item["media_type"],
                    "member": member,
                    "parent_origin_id": canonical_json(parent),
                }
            )


def metadata_contents(directory: Path, document: dict[str, Any]) -> dict[str, tuple[int, str]]:
    if document["schema_version"] != 4:
        return {}
    files: dict[str, tuple[int, str]] = {}
    for item in document["content"]:
        if item["kind"] not in ("attachment", "release_asset"):
            continue
        descriptor = item["file"]
        member = descriptor["path"]
        facts = _digest(_file(directory, member), limit=descriptor["size"])
        if facts != (descriptor["size"], descriptor["sha256"]):
            raise PackageError("asset payload size or digest mismatch")
        if member in files and files[member] != facts:
            raise PackageError("asset payload member has conflicting facts")
        files[member] = facts
    return files


def content_by_repository(document: dict[str, Any], repository: str) -> list[dict[str, Any]]:
    if document["schema_version"] != 4:
        return []
    return [item for item in document["content"] if item["repository"] == repository]


def match_metadata_inventory(
    repository: RepositoryInventory, content: list[dict[str, Any]]
) -> None:
    """Require an exact source-copy set for each selected v4 metadata class."""
    classes = {item.content_class: item for item in repository.classes}
    mapping = {
        "label": "labels",
        "release": "releases",
        "attachment": "attachments",
        "release_asset": "release_assets",
    }
    for kind, class_name in mapping.items():
        if class_name not in repository.requested_classes:
            if any(item["kind"] == kind for item in content):
                raise PackageError("package contains an unselected metadata class")
            continue
        native = {item.native_id: item for item in classes[class_name].objects}
        packaged = {item["source_native_id"]: item for item in content if item["kind"] == kind}
        if None in native or set(native) != set(packaged):
            raise PackageError("portable metadata set differs from the reviewed inventory")
        for identifier, observed in native.items():
            selected = packaged[identifier]
            if kind == "label" and label_values(selected["values"]) != label_values(observed.facts):
                raise PackageError("label fields differ from the reviewed inventory")
            if kind == "release":
                values = selected["values"]
                if any(values.get(key) != observed.facts.get(key) for key in values):
                    raise PackageError("release fields differ from the reviewed inventory")
                if selected["source_created_at"] != observed.facts.get("created_at"):
                    raise PackageError("release creation time differs from the reviewed inventory")
            if kind in ("attachment", "release_asset"):
                parent = selected["parent_origin"]
                origin = selected["origin"]
                parent_class = {
                    "issue": "issues",
                    "issue_comment": "comments",
                    "release": "releases",
                }.get(parent.get("object_type"))
                if (
                    selected["name"] != observed.facts.get("name")
                    or selected["source_created_at"] != observed.facts.get("created_at")
                    or selected["file"]["size"] != observed.size_bytes
                    or observed.parent is None
                    or parent.get("instance_key") != origin.get("instance_key")
                    or parent.get("repository") != selected["repository"]
                    or parent_class != observed.parent["content_class"]
                    or parent.get("global_id") != observed.parent["key"]
                ):
                    raise PackageError("asset payload or parent differs from reviewed inventory")


__all__ = [
    "CONTENT_KINDS",
    "content_by_repository",
    "metadata_contents",
    "match_metadata_inventory",
    "validate_metadata_descriptors",
]
