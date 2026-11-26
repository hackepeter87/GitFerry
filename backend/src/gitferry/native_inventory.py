"""Read-only, bounded native payload facts for content-inventory version two."""

from __future__ import annotations

from collections.abc import Callable

from .content_inventory import ClassObservation, InventoryDocument, InventoryObject, parse_inventory
from .gitea_bundles import GiteaBundleError, GiteaBundlePusher
from .gitea_lfs import GiteaLFSClient, GiteaLFSError
from .gitea_wiki import GiteaWikiClient, GiteaWikiError
from .lfs_inventory import inventory_lfs_pointers
from .lfs_payloads import LFSPayloadError


def observe_native_payloads(
    document: InventoryDocument,
    git: GiteaBundlePusher,
    lfs_client: Callable[[str], GiteaLFSClient],
    wiki: GiteaWikiClient,
    *,
    max_bytes: int,
) -> InventoryDocument:
    """Augment only explicitly selected native classes; never mutate saved baselines.

    V1 wiki page counts remain v1 facts. V2 represents a wiki repository and its
    exact refs; LFS objects are SHA-256 identities discovered across all reachable
    historical blobs. A protocol failure remains unknown, never an empty set.
    """
    result = document.model_copy(deep=True)
    result.schema_version = 2
    result.comparison_version = 2
    for repository in result.repositories:
        replacements: dict[str, ClassObservation] = {}
        if "lfs" in repository.requested_classes:
            try:
                with git.source_mirror(
                    repository.repository, repository.native_id, max_bytes=max_bytes
                ) as mirror:
                    pointers = inventory_lfs_pointers(mirror) if mirror is not None else ()
                    if sum(p.size for p in pointers) > max_bytes:
                        raise LFSPayloadError("selected LFS bytes exceed the inventory bound")
                    with lfs_client(repository.repository) as client:
                        for pointer in pointers:
                            client.inspect(pointer)
                    replacements["lfs"] = ClassObservation(
                        content_class="lfs",
                        status="observed",
                        count=len(pointers),
                        objects=[
                            InventoryObject(
                                key=pointer.oid,
                                namespace="lfs-sha256",
                                native_id=pointer.oid,
                                size_bytes=pointer.size,
                                facts={"oid": pointer.oid, "size": pointer.size},
                            )
                            for pointer in pointers
                        ],
                        limitations=[
                            "Standard v1 pointers across selected reachable history; no extensions",
                            "Availability checked here; export verifies every object's bytes",
                            "Only approved same-origin repository basic LFS actions are allowed",
                        ],
                    )
            except (GiteaBundleError, GiteaLFSError, LFSPayloadError):
                replacements["lfs"] = _blocked(
                    "lfs",
                    "Selected LFS history or objects are unavailable, unsupported or oversized",
                )
        if "wiki" in repository.requested_classes:
            try:
                # This checks native parent ID, enabled native wiki and no external wiki.
                wiki._wiki_parent(repository.repository, int(repository.native_id))
                with git.source_mirror(
                    repository.repository, repository.native_id, max_bytes=max_bytes, wiki=True
                ) as mirror:
                    refs = git._local_refs(mirror) if mirror is not None else {}
                    if len(refs) > 10000:
                        raise GiteaWikiError("wiki ref count exceeds the supported bound")
                    replacements["wiki"] = ClassObservation(
                        content_class="wiki",
                        status="observed",
                        count=1,
                        objects=[
                            InventoryObject(
                                key="wiki",
                                namespace="wiki-repository",
                                native_id=repository.native_id,
                                facts={"state": "populated" if refs else "empty", "refs": refs},
                            )
                        ],
                        limitations=[
                            "One native wiki repository with exact refs/history; not a page count",
                            "Uninitialized, disabled or external wikis are explicit blockers",
                            "No ref deletion or automatic default-branch conversion",
                        ],
                    )
            except (GiteaBundleError, GiteaWikiError):
                replacements["wiki"] = _blocked(
                    "wiki",
                    "Native wiki is disabled, external, uninitialized, unavailable or oversized; "
                    "absence is not inferred from an API error",
                )
        repository.classes = [replacements.get(c.content_class, c) for c in repository.classes]
    return parse_inventory(result.model_dump(mode="json"))


def _blocked(content_class: str, reason: str) -> ClassObservation:
    return ClassObservation(
        content_class=content_class, status="blocked", count=None, reason=reason
    )
