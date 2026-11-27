from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from gitferry.gitea_metadata import (
    GiteaMetadataClient,
    MetadataTargetConflict,
    verify_asset,
    verify_label,
    verify_release,
)


def response(request: httpx.Request, status: int, value: object) -> httpx.Response:
    return httpx.Response(status, json=value, request=request)


def test_label_release_and_assignment_routes() -> None:
    requests: list[tuple[str, str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path.endswith("/labels"):
            return response(request, 201, {"id": 4, **body})
        if request.method == "PATCH" and "/labels/" in request.url.path:
            return response(request, 200, {"id": 4, **body})
        if request.method == "PUT" and request.url.path.endswith("/issues/7/labels"):
            return response(request, 200, [{"id": 4, "name": "priority"}])
        if request.method == "POST" and request.url.path.endswith("/releases"):
            return response(request, 201, {"id": 8, **body})
        if request.method == "PATCH" and request.url.path.endswith("/releases/8"):
            return response(
                request,
                200,
                {
                    "id": 8,
                    "tag_name": "v1",
                    "target_commitish": body["target_commitish"],
                    "name": body["name"],
                    "body": body["body"],
                    "draft": body["draft"],
                    "prerelease": body["prerelease"],
                },
            )
        raise AssertionError((request.method, request.url.path))

    client = GiteaMetadataClient(
        "https://gitea.invalid",
        username="admin",
        token="token",  # noqa: S106 - synthetic MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    label = {"name": "priority", "color": "112233", "description": "Synthetic"}
    assert verify_label(client.create_label("acme/repo", label), label) == "4"
    assert verify_label(client.update_label("acme/repo", "4", label), label) == "4"
    assert client.replace_issue_labels("acme/repo", 7, [4])[0]["id"] == 4
    release = {
        "tag_name": "v1",
        "target_commitish": "main",
        "name": "Release",
        "body": "Notes",
        "draft": False,
        "prerelease": False,
    }
    assert verify_release(client.create_release("acme/repo", release), release) == "8"
    updated = {**release, "name": "Updated"}
    assert verify_release(client.update_release("acme/repo", "8", updated), updated) == "8"
    with pytest.raises(MetadataTargetConflict, match="release fields"):
        verify_release(
            {**updated, "target_commitish": "wrong-ref", "id": 8},
            updated,
        )
    assert requests[0][:2] == ("POST", "/api/v1/repos/acme/repo/labels")
    client.close()


def test_asset_upload_parent_routes_and_filename_validation(tmp_path: Path) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"asset")
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return response(request, 201, {"id": len(paths), "name": "asset.txt", "size": 5})

    client = GiteaMetadataClient(
        "https://gitea.invalid",
        username="author",
        token="token",  # noqa: S106 - synthetic MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    for kind, parent in (("issue", "3"), ("comment", "9"), ("release", "11")):
        value = client.upload_asset("acme/repo", kind, parent, "asset.txt", payload, "text/plain")
        assert verify_asset(value, name="asset.txt", size=5)
    assert paths == [
        "/api/v1/repos/acme/repo/issues/3/assets",
        "/api/v1/repos/acme/repo/issues/comments/9/assets",
        "/api/v1/repos/acme/repo/releases/11/assets",
    ]
    with pytest.raises(MetadataTargetConflict, match="filename"):
        client.upload_asset("acme/repo", "issue", "3", "../asset", payload, "text/plain")
    client.close()


def test_asset_download_requires_same_origin_uuid_size_and_digest(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/attachments/uuid-1"
        return httpx.Response(
            200,
            content=b"asset",
            headers={"content-type": "text/plain", "content-encoding": "identity"},
            request=request,
        )

    client = GiteaMetadataClient(
        "https://gitea.invalid",
        username="admin",
        token="token",  # noqa: S106 - synthetic MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    destination = tmp_path / "asset.bin"
    facts = client.download_asset(
        {
            "uuid": "uuid-1",
            "size": 5,
            "browser_download_url": "https://gitea.invalid/attachments/uuid-1",
        },
        destination,
        max_bytes=5,
    )
    assert facts == (
        5,
        "d59386e0ae435e292fbe0ebcdb954b75ed5fb3922091277cb19f798fc5d50718",
        "text/plain",
    )
    assert destination.read_bytes() == b"asset"
    with pytest.raises(MetadataTargetConflict, match="same-origin"):
        client.download_asset(
            {
                "uuid": "uuid-1",
                "size": 5,
                "browser_download_url": "https://evil.invalid/attachments/uuid-1",
            },
            tmp_path / "evil.bin",
            max_bytes=5,
        )
    client.close()


def test_asset_download_accepts_gitea_installation_subpath_but_not_sibling_path(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/gitea/attachments/uuid-2"
        return httpx.Response(
            200,
            content=b"asset",
            headers={"content-type": "text/plain", "content-encoding": "identity"},
            request=request,
        )

    client = GiteaMetadataClient(
        "https://gitea.invalid/gitea",
        username="admin",
        token="token",  # noqa: S106 - synthetic MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    destination = tmp_path / "asset.bin"
    assert (
        client.download_asset(
            {
                "uuid": "uuid-2",
                "size": 5,
                "browser_download_url": "https://gitea.invalid/gitea/attachments/uuid-2",
            },
            destination,
            max_bytes=5,
        )[0]
        == 5
    )
    with pytest.raises(MetadataTargetConflict, match="same-origin"):
        client.download_asset(
            {
                "uuid": "uuid-2",
                "size": 5,
                "browser_download_url": "https://gitea.invalid/gitea-other/attachments/uuid-2",
            },
            tmp_path / "sibling.bin",
            max_bytes=5,
        )
    client.close()


def test_asset_download_stream_rejects_body_larger_than_reviewed_size(tmp_path: Path) -> None:
    chunks_read: list[int] = []

    class OversizedStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            chunks_read.append(1)
            yield b"012345"
            chunks_read.append(2)
            raise AssertionError("download continued after the reviewed bound")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=OversizedStream(),
            headers={"content-encoding": "identity"},
            request=request,
        )

    client = GiteaMetadataClient(
        "https://gitea.invalid",
        username="admin",
        token="token",  # noqa: S106 - synthetic MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(MetadataTargetConflict, match="exceeds"):
        client.download_asset(
            {
                "uuid": "uuid-large",
                "size": 5,
                "browser_download_url": "https://gitea.invalid/attachments/uuid-large",
            },
            tmp_path / "large.bin",
            max_bytes=5,
        )
    assert chunks_read == [1]
    client.close()
