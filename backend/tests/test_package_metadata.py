from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
from gitferry.application_package import PackageError
from gitferry.content_inventory import parse_inventory
from gitferry.package_metadata import (
    match_metadata_inventory,
    metadata_contents,
    validate_metadata_descriptors,
)
from test_content_inventory import _document


def package() -> dict:
    return {
        "schema_version": 4,
        "inventory": {
            "include_labels": True,
            "include_attachments": True,
            "include_releases": True,
            "include_release_assets": True,
        },
        "content": [
            {
                "kind": "label",
                "origin": {
                    "instance_key": "a",
                    "repository": "acme/one",
                    "object_type": "label",
                    "global_id": "11",
                },
                "repository": "acme/one",
                "source_native_id": "11",
                "values": {
                    "name": "priority",
                    "color": "112233",
                    "description": "Synthetic",
                },
            },
            {
                "kind": "release",
                "origin": {
                    "instance_key": "a",
                    "repository": "acme/one",
                    "object_type": "release",
                    "global_id": "21",
                },
                "repository": "acme/one",
                "source_native_id": "21",
                "author": {
                    "realm_key": "realm-a",
                    "login": "author",
                    "external_id": "1",
                    "ou_path": "/",
                },
                "values": {
                    "tag_name": "v1",
                    "target_commitish": "main",
                    "name": "Release",
                    "body": "Notes",
                    "draft": False,
                    "prerelease": False,
                },
                "source_created_at": "2026-01-01T00:00:00Z",
                "source_published_at": "2026-01-01T00:00:00Z",
            },
            {
                "kind": "attachment",
                "origin": {
                    "instance_key": "a",
                    "repository": "acme/one",
                    "object_type": "attachment",
                    "global_id": "31",
                },
                "repository": "acme/one",
                "source_native_id": "31",
                "parent_origin": {
                    "instance_key": "a",
                    "repository": "acme/one",
                    "object_type": "issue",
                    "global_id": "issues-1",
                },
                "name": "asset.txt",
                "media_type": "text/plain",
                "source_created_at": "2026-01-01T00:00:00Z",
                "file": {
                    "path": "assets/31",
                    "size": 5,
                    "sha256": hashlib.sha256(b"asset").hexdigest(),
                },
            },
            {
                "kind": "release_asset",
                "origin": {
                    "instance_key": "a",
                    "repository": "acme/one",
                    "object_type": "release_asset",
                    "global_id": "32",
                },
                "repository": "acme/one",
                "source_native_id": "32",
                "parent_origin": {
                    "instance_key": "a",
                    "repository": "acme/one",
                    "object_type": "release",
                    "global_id": "21",
                },
                "name": "asset.txt",
                "media_type": "text/plain",
                "source_created_at": "2026-01-01T00:00:00Z",
                "file": {
                    "path": "assets/32",
                    "size": 5,
                    "sha256": hashlib.sha256(b"asset").hexdigest(),
                },
            },
        ],
    }


def test_metadata_descriptors_and_payload_bytes_are_bound(tmp_path: Path) -> None:
    value = package()
    validate_metadata_descriptors(value)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/31").write_bytes(b"asset")
    (tmp_path / "assets/32").write_bytes(b"asset")
    assert metadata_contents(tmp_path, value) == {
        "assets/31": (5, hashlib.sha256(b"asset").hexdigest()),
        "assets/32": (5, hashlib.sha256(b"asset").hexdigest()),
    }
    (tmp_path / "assets/31").write_bytes(b"wrong")
    with pytest.raises(PackageError, match="digest"):
        metadata_contents(tmp_path, value)


def test_metadata_payload_rejects_missing_symlink_and_oversized_member(tmp_path: Path) -> None:
    value = package()
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/32").write_bytes(b"asset")
    with pytest.raises(PackageError, match="missing"):
        metadata_contents(tmp_path, value)

    outside = tmp_path / "outside"
    outside.write_bytes(b"asset")
    (tmp_path / "assets/31").symlink_to(outside)
    with pytest.raises(PackageError, match="symbolic"):
        metadata_contents(tmp_path, value)

    (tmp_path / "assets/31").unlink()
    (tmp_path / "assets/31").write_bytes(b"asset!")
    with pytest.raises(PackageError, match="size limit"):
        metadata_contents(tmp_path, value)


@pytest.mark.parametrize("unsafe_path", ["../outside", "/etc/passwd", "assets//asset"])
def test_metadata_descriptor_rejects_unsafe_paths(unsafe_path: str) -> None:
    value = package()
    value["content"][2]["file"]["path"] = unsafe_path
    with pytest.raises(PackageError):
        validate_metadata_descriptors(value)


def test_metadata_descriptor_rejects_origin_and_parent_conflicts() -> None:
    for mutation in ("origin", "parent"):
        value = package()
        if mutation == "origin":
            value["content"][0]["origin"]["object_type"] = "release"
        else:
            value["content"][2]["parent_origin"]["object_type"] = "release"
        with pytest.raises(PackageError):
            validate_metadata_descriptors(value)


def test_metadata_content_matches_reviewed_inventory_by_native_id_and_parent() -> None:
    raw = _document(requested_classes=["labels", "attachments", "releases", "release_assets"])
    document = parse_inventory(raw)
    content = package()["content"]
    match_metadata_inventory(document.repositories[0], content)
    changed = deepcopy(content)
    changed[2]["parent_origin"]["global_id"] = "other"
    with pytest.raises(PackageError, match="parent"):
        match_metadata_inventory(document.repositories[0], changed)
    duplicated = deepcopy(package())
    duplicated["content"].append(deepcopy(duplicated["content"][0]))
    with pytest.raises(PackageError, match="duplicate"):
        validate_metadata_descriptors(duplicated)
