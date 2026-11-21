from __future__ import annotations

import ssl

import httpx
import pytest
from gitferry.content_inventory import CONTENT_CLASSES
from gitferry.source_inventory import InventoryCollectionError, _InventoryReader, collect_inventory
from gitferry.source_snapshot import SourceSnapshotError


def _reader(handler):
    return _InventoryReader(
        "https://source.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        httpx.MockTransport(handler),
    )


def test_paginated_inventory_requires_total_and_finishes_at_exact_total() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        values = [{"id": i} for i in range(1, 51)] if page == "1" else [{"id": 51}]
        return httpx.Response(200, headers={"X-Total-Count": "51"}, json=values)

    reader = _reader(handle)
    try:
        assert [item["id"] for item in reader.page("/repos/acme/one/labels")] == list(range(1, 52))
    finally:
        reader.close()


def test_paginated_inventory_rejects_duplicate_ids_and_missing_total() -> None:
    def duplicate(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"X-Total-Count": "2"}, json=[{"id": 1}, {"id": 1}])

    reader = _reader(duplicate)
    try:
        with pytest.raises(SourceSnapshotError, match="duplicate"):
            reader.page("/repos/acme/one/labels")
    finally:
        reader.close()

    def missing_total(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    reader = _reader(missing_total)
    try:
        with pytest.raises(SourceSnapshotError, match="total"):
            reader.page("/repos/acme/one/labels")
    finally:
        reader.close()


def test_refs_are_bounded_nonpaged_and_do_not_require_total_header() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"url": "ref/heads/main", "ref": "refs/heads/main", "object": {"sha": "a"}}]
        )

    reader = _reader(handle)
    try:
        value = reader.full("/repos/acme/one/git/refs")
        assert value[0]["ref"] == "refs/heads/main"
    finally:
        reader.close()


def test_collect_inventory_exposes_unsupported_lfs_and_excluded_classes() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/version"):
            return httpx.Response(200, json={"version": "1.27.2"})
        if path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "full_name": "acme/one",
                    "owner": {"id": 3, "login": "acme"},
                    "size": 4,
                },
            )
        if path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme", "username": "acme"})
        if path.endswith("/git/refs"):
            return httpx.Response(200, json=[])
        if "/wiki/" in path:
            return httpx.Response(404, json={"message": "no wiki"})
        if (
            path.endswith("/issues")
            or path.endswith("/teams")
            or path.endswith("/labels")
            or path.endswith("/releases")
            or path.endswith("/pulls")
            or path.endswith("/milestones")
            or path.endswith("/collaborators")
        ):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, json=[])
        if path.endswith("/issues/comments"):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, json=[])
        if "/issues/" in path and any(
            path.endswith(suffix) for suffix in ("/assets", "/timeline", "/reactions", "/comments")
        ):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "0"} if path.endswith(("/timeline", "/comments")) else {},
                json=[],
            )
        if "/releases/" in path and path.endswith("/assets"):
            return httpx.Response(200, json=[])
        if "/pulls/" in path and (path.endswith("/reviews") or "/reviews/" in path):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, json=[])
        raise AssertionError(path)

    requested = [name for name in CONTENT_CLASSES if name != "labels"]
    document = collect_inventory(
        "https://source.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        [{"repository": "acme/one", "global_id": "7"}],
        requested,
        source_instance_key="source.synthetic",
        installation_marker="install.synthetic",
        snapshot_id="snapshot-a",
        created_at="2026-11-21T10:00:00Z",
        transport=httpx.MockTransport(handle),
    )
    classes = {item.content_class: item for item in document.repositories[0].classes}
    assert classes["lfs"].status == "not-supported"
    assert classes["lfs"].count is None
    assert classes["labels"].status == "observed" and classes["labels"].count == 0
    assert "labels" not in document.repositories[0].requested_classes
    assert classes["wiki"].status == "blocked" and classes["wiki"].count is None
    assert classes["git_refs"].status == "observed"
    assert set(classes) == set(CONTENT_CLASSES)


def test_collect_inventory_rejects_selected_repository_identity_mismatch() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "1.27.2"})
        return httpx.Response(
            200, json={"id": 8, "full_name": "acme/one", "owner": {"id": 3, "login": "acme"}}
        )

    with pytest.raises(InventoryCollectionError):
        collect_inventory(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            ["git_refs"],
            source_instance_key="source.synthetic",
            installation_marker="install.synthetic",
            snapshot_id="snapshot-a",
            created_at="2026-11-21T10:00:00Z",
            transport=httpx.MockTransport(handle),
        )
