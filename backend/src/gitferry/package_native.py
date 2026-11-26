"""Version-three package payload facts; no execution grants or hidden downloads."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .content_inventory import InventoryDocument
from .git_refs import valid_ref
from .lfs_payloads import LFSPayloadError, LFSPointer, verify_lfs_object

_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def validate_native_descriptors(document: dict[str, Any]) -> None:
    from .application_package import PackageError, package_path

    if document["schema_version"] != 3:
        return
    files: dict[str, tuple[int, str]] = {}
    for repository in document["repositories"]:
        pointers = repository["lfs"]
        if (pointers is not None) != document["inventory"]["include_lfs"]:
            raise PackageError("LFS payload selection differs from inventory selection")
        seen: set[str] = set()
        for pointer in pointers or []:
            member = pointer["path"]
            package_path(member)
            if (
                pointer["oid"] in seen
                or pointer["sha256"] != pointer["oid"]
                or member != "lfs/" + pointer["oid"]
            ):
                raise PackageError("LFS payload identity or canonical path differs")
            seen.add(pointer["oid"])
            facts = pointer["size"], pointer["sha256"]
            if files.setdefault(member, facts) != facts:
                raise PackageError("shared LFS identity has conflicting facts")
        wiki = repository["wiki"]
        if (wiki is not None) != document["inventory"]["include_wiki"]:
            raise PackageError("wiki payload selection differs from inventory selection")
        if wiki is None:
            continue
        refs = wiki["refs"]
        if any(not valid_ref(ref) or not _SHA.fullmatch(sha) for ref, sha in refs.items()):
            raise PackageError("wiki ref identity is invalid")
        populated = wiki["state"] == "populated"
        if populated != bool(refs) or populated != (wiki["bundle"] is not None):
            raise PackageError("wiki state, refs and payload disagree")
        if wiki["bundle"] is not None:
            bundle = wiki["bundle"]
            member = bundle["path"]
            package_path(member)
            if not member.startswith("wikis/") or member in files:
                raise PackageError("wiki payload needs a unique path below wikis")
            files[member] = bundle["size"], bundle["sha256"]


def native_contents(directory: Path, document: dict[str, Any]) -> dict[str, tuple[int, str]]:
    from .application_package import PackageError, _digest, _file

    files: dict[str, tuple[int, str]] = {}
    if document["schema_version"] != 3:
        return files
    for repository in document["repositories"]:
        for value in repository["lfs"] or []:
            pointer = LFSPointer(value["oid"], value["size"])
            path = _file(directory, value["path"])
            # The shared verifier also rejects hardlinks and checks exact size/digest.
            try:
                verify_lfs_object(path, pointer, max_bytes=max(1, pointer.size))
            except LFSPayloadError:
                raise PackageError("LFS payload verification failed") from None
            files[value["path"]] = pointer.size, pointer.oid
        wiki = repository["wiki"]
        if wiki is not None and wiki["bundle"] is not None:
            bundle = wiki["bundle"]
            path = _file(directory, bundle["path"])
            if path.stat().st_nlink != 1:
                raise PackageError("wiki payload hardlinks are forbidden")
            facts = _digest(path, limit=bundle["size"])
            if facts != (bundle["size"], bundle["sha256"]):
                raise PackageError("wiki payload size or digest differs")
            files[bundle["path"]] = facts
    return files


def match_native_inventory(document: InventoryDocument, selected: dict[str, Any]) -> None:
    from .application_package import PackageError

    if document.schema_version < 2:
        raise PackageError("native payloads require a native-capable inventory version")
    repository = next(r for r in document.repositories if r.repository == selected["repository"])
    classes = {c.content_class: c for c in repository.classes}
    if selected["lfs"] is not None:
        expected = {p["oid"]: p["size"] for p in selected["lfs"]}
        observed = {}
        for item in classes["lfs"].objects:
            if (
                item.namespace != "lfs-sha256"
                or item.key != item.native_id
                or item.facts != {"oid": item.key, "size": item.size_bytes}
            ):
                raise PackageError("LFS inventory identity differs")
            observed[item.key] = item.size_bytes
        if expected != observed or classes["lfs"].status != "observed":
            raise PackageError("packaged LFS objects differ from reviewed inventory")
    if selected["wiki"] is not None:
        observation = classes["wiki"]
        items = observation.objects
        if (
            observation.status != "observed"
            or len(items) != 1
            or items[0].namespace != "wiki-repository"
            or items[0].key != "wiki"
            or items[0].native_id != repository.native_id
            or items[0].facts != {k: selected["wiki"][k] for k in ("state", "refs")}
        ):
            raise PackageError("packaged wiki refs differ from reviewed inventory")
