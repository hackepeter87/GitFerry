from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from pathlib import Path

import pytest
from gitferry.application_package import PackageError
from gitferry.content_inventory import InventoryObject, parse_inventory
from gitferry.package_native import (
    match_native_inventory,
    native_contents,
    validate_native_descriptors,
)
from test_content_inventory import _document


def _descriptor(*, size: int = 4, oid: str | None = None) -> dict:
    oid = oid or "a" * 64
    return {"oid": oid, "size": size, "path": f"lfs/{oid}", "sha256": oid}


def _package(*, lfs: list[dict] | None = None, wiki: dict | None = None) -> dict:
    return {
        "schema_version": 3,
        "inventory": {"include_lfs": lfs is not None, "include_wiki": wiki is not None},
        "repositories": [
            {
                "repository": "acme/one",
                "lfs": lfs,
                "wiki": wiki,
            }
        ],
    }


def _wiki(
    *,
    state: str = "populated",
    refs: dict[str, str] | None = None,
    bundle: dict | None = None,
) -> dict:
    refs = refs if refs is not None else {"refs/heads/main": "b" * 40}
    if bundle is None and state == "populated":
        bundle = {"path": "wikis/repo.bundle", "size": 4, "sha256": "c" * 64}
    return {"state": state, "refs": refs, "bundle": bundle}


def test_native_descriptors_require_canonical_lfs_paths_and_sha() -> None:
    valid = _package(lfs=[_descriptor()], wiki=None)
    validate_native_descriptors(valid)
    wrong_path = deepcopy(valid)
    wrong_path["repositories"][0]["lfs"][0]["path"] = "lfs/not-canonical"
    with pytest.raises(PackageError):
        validate_native_descriptors(wrong_path)
    wrong_sha = deepcopy(valid)
    wrong_sha["repositories"][0]["lfs"][0]["sha256"] = "d" * 64
    with pytest.raises(PackageError):
        validate_native_descriptors(wrong_sha)


def test_shared_lfs_identity_cannot_have_conflicting_sizes() -> None:
    pointer = _descriptor(size=4)
    value = _package(lfs=[pointer], wiki=None)
    value["repositories"].append({"lfs": [_descriptor(size=5)], "wiki": None})
    with pytest.raises(PackageError, match="conflicting facts"):
        validate_native_descriptors(value)


@pytest.mark.parametrize(
    "wiki",
    [
        _wiki(state="populated", refs={}, bundle=None),
        _wiki(state="empty", refs={"refs/heads/main": "b" * 40}, bundle=None),
        _wiki(refs={"refs/heads/../bad": "b" * 40}),
    ],
)
def test_wiki_state_refs_and_bundle_must_cohere(wiki: dict) -> None:
    with pytest.raises(PackageError):
        validate_native_descriptors(_package(lfs=None, wiki=wiki))


def test_native_contents_rejects_corruption_and_hardlinks(tmp_path: Path) -> None:
    oid = hashlib.sha256(b"data").hexdigest()
    value = _package(lfs=[_descriptor(size=4, oid=oid)], wiki=None)
    payload = tmp_path / "lfs" / oid
    payload.parent.mkdir()
    payload.write_bytes(b"bad!")
    with pytest.raises(PackageError, match="verification"):
        native_contents(tmp_path, value)
    payload.write_bytes(b"data")
    alias = tmp_path / "alias"
    os.link(payload, alias)
    with pytest.raises(PackageError, match="verification"):
        native_contents(tmp_path, value)


@pytest.mark.parametrize("inventory_version", [2, 3])
def test_native_inventory_matching_rejects_other_parent_and_pointer_drift(
    inventory_version: int,
) -> None:
    value = _document(requested_classes=["lfs", "wiki"])
    value["schema_version"] = inventory_version
    value["comparison_version"] = inventory_version
    document = parse_inventory(value)
    repository = document.repositories[0]
    lfs = next(item for item in repository.classes if item.content_class == "lfs")
    lfs.objects = [
        InventoryObject(
            key="a" * 64,
            namespace="lfs-sha256",
            native_id="a" * 64,
            size_bytes=4,
            facts={"oid": "a" * 64, "size": 4},
        )
    ]
    lfs.count = 1
    wiki = next(item for item in repository.classes if item.content_class == "wiki")
    wiki.objects = [
        InventoryObject(
            key="wiki",
            namespace="wiki-repository",
            native_id=repository.native_id,
            facts={"state": "populated", "refs": {"refs/heads/main": "b" * 40}},
        )
    ]
    wiki.count = 1
    selected = _package(lfs=[_descriptor(size=4)], wiki=_wiki())["repositories"][0]
    match_native_inventory(document, selected)
    changed = deepcopy(selected)
    changed["lfs"][0]["size"] = 5
    with pytest.raises(PackageError):
        match_native_inventory(document, changed)
    other_parent = deepcopy(selected)
    other_parent["wiki"]["refs"] = {"refs/heads/other": "b" * 40}
    with pytest.raises(PackageError):
        match_native_inventory(document, other_parent)
