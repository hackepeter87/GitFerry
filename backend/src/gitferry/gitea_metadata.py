"""Bounded Gitea adapters for labels, releases, and binary assets."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from gitferry.gitea_provisioning import (
    GiteaProvisioningClient,
    _numeric_path_segment,
    _repository_segments,
    require_numeric_id,
)


class MetadataTargetConflict(RuntimeError):
    """A native metadata object or payload does not match the reviewed intent."""


ParentKind = Literal["issue", "comment", "release"]


class GiteaMetadataClient(GiteaProvisioningClient):
    """Use documented Gitea routes without accepting remote download locations."""

    def _list(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 1001):
            value = self._request("GET", path, params={"page": page, "limit": 50}).json()
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise MetadataTargetConflict("Gitea metadata list is malformed")
            result.extend(value)
            if len(value) < 50:
                return result
        raise MetadataTargetConflict("Gitea metadata list exceeds the supported bound")

    def list_labels(self, repository: str) -> list[dict[str, Any]]:
        owner, name = _repository_segments(repository)
        return self._list(f"/repos/{owner}/{name}/labels")

    def create_label(self, repository: str, values: Mapping[str, Any]) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        return self._object(
            "POST",
            f"/repos/{owner}/{name}/labels",
            json={key: values[key] for key in ("name", "color", "description")},
        )

    def update_label(
        self, repository: str, label_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        native = _numeric_path_segment(label_id, "label id")
        return self._object(
            "PATCH",
            f"/repos/{owner}/{name}/labels/{native}",
            json={key: values[key] for key in ("name", "color", "description")},
        )

    def replace_issue_labels(
        self, repository: str, issue_index: int, label_ids: list[int]
    ) -> list[dict[str, Any]]:
        owner, name = _repository_segments(repository)
        value = self._request(
            "PUT",
            f"/repos/{owner}/{name}/issues/{int(issue_index)}/labels",
            json={"labels": label_ids},
        ).json()
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise MetadataTargetConflict("Gitea issue-label response is malformed")
        return value

    def list_releases(self, repository: str) -> list[dict[str, Any]]:
        owner, name = _repository_segments(repository)
        return self._list(f"/repos/{owner}/{name}/releases")

    def get_release(self, repository: str, release_id: str) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        native = _numeric_path_segment(release_id, "release id")
        return self._object("GET", f"/repos/{owner}/{name}/releases/{native}")

    def create_release(self, repository: str, values: Mapping[str, Any]) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        return self._object(
            "POST",
            f"/repos/{owner}/{name}/releases",
            json={
                key: values[key]
                for key in ("tag_name", "target_commitish", "name", "body", "draft", "prerelease")
            },
        )

    def update_release(
        self, repository: str, release_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        owner, name = _repository_segments(repository)
        native = _numeric_path_segment(release_id, "release id")
        return self._object(
            "PATCH",
            f"/repos/{owner}/{name}/releases/{native}",
            json={key: values[key] for key in ("name", "body", "draft", "prerelease")},
        )

    def list_assets(
        self, repository: str, parent_kind: ParentKind, parent_id: str
    ) -> list[dict[str, Any]]:
        owner, name = _repository_segments(repository)
        native = _numeric_path_segment(parent_id, f"{parent_kind} id")
        if parent_kind == "issue":
            path = f"/repos/{owner}/{name}/issues/{native}/assets"
        elif parent_kind == "comment":
            path = f"/repos/{owner}/{name}/issues/comments/{native}/assets"
        else:
            path = f"/repos/{owner}/{name}/releases/{native}/assets"
        return self._list(path)

    def upload_asset(
        self,
        repository: str,
        parent_kind: ParentKind,
        parent_id: str,
        filename: str,
        path: Path,
        media_type: str,
    ) -> dict[str, Any]:
        if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
            raise MetadataTargetConflict("asset filename is not portable")
        owner, name = _repository_segments(repository)
        native = _numeric_path_segment(parent_id, f"{parent_kind} id")
        if parent_kind == "issue":
            endpoint = f"/repos/{owner}/{name}/issues/{native}/assets"
        elif parent_kind == "comment":
            endpoint = f"/repos/{owner}/{name}/issues/comments/{native}/assets"
        else:
            endpoint = f"/repos/{owner}/{name}/releases/{native}/assets"
        with path.open("rb") as stream:
            value = self._request(
                "POST", endpoint, files={"attachment": (filename, stream, media_type)}
            ).json()
        if not isinstance(value, dict):
            raise MetadataTargetConflict("Gitea asset upload response is malformed")
        require_numeric_id(value, "asset")
        if value.get("name") != filename:
            raise MetadataTargetConflict("Gitea asset upload changed the filename")
        return value

    def download_asset(
        self, value: Mapping[str, Any], destination: Path, *, max_bytes: int
    ) -> tuple[int, str, str]:
        """Download only the Gitea-native attachment UUID route on this connection."""
        url = value.get("browser_download_url")
        uuid = value.get("uuid")
        size = value.get("size")
        if (
            not isinstance(url, str)
            or not isinstance(uuid, str)
            or not uuid
            or type(size) is not int
            or size < 0
            or size > max_bytes
        ):
            raise MetadataTargetConflict("asset download facts are incomplete or oversized")
        parsed = urlsplit(url)
        base = urlsplit(str(self._client.base_url))
        if (
            parsed.scheme != base.scheme
            or parsed.netloc != base.netloc
            or parsed.query
            or parsed.fragment
            or parsed.path != f"/attachments/{uuid}"
        ):
            raise MetadataTargetConflict("asset download URL is not the expected same-origin route")
        response = self._client.get(url)
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity") != "identity":
            raise MetadataTargetConflict("compressed asset responses are not accepted")
        digest = hashlib.sha256()
        written = 0
        with destination.open("xb") as stream:
            for chunk in response.iter_bytes():
                written += len(chunk)
                if written > max_bytes or written > size:
                    raise MetadataTargetConflict("asset body exceeds its reviewed size")
                digest.update(chunk)
                stream.write(chunk)
        if written != size:
            raise MetadataTargetConflict("asset body differs from its reviewed size")
        return (
            written,
            digest.hexdigest(),
            response.headers.get("content-type", "application/octet-stream"),
        )


def verify_label(value: Mapping[str, Any], expected: Mapping[str, Any]) -> str:
    if any(value.get(key) != expected.get(key) for key in ("name", "color", "description")):
        raise MetadataTargetConflict("label fields differ from the reviewed intent")
    return require_numeric_id(value, "label")


def verify_release(
    value: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    author_login: str | None = None,
    author_id: int | None = None,
) -> str:
    if any(
        value.get(key) != expected.get(key)
        for key in ("tag_name", "name", "body", "draft", "prerelease")
    ):
        raise MetadataTargetConflict("release fields differ from the reviewed intent")
    if author_login is not None:
        author = value.get("author")
        if (
            not isinstance(author, Mapping)
            or author.get("login") != author_login
            or (author_id is not None and str(author.get("id")) != str(author_id))
        ):
            raise MetadataTargetConflict("native release author differs from the identity binding")
    return require_numeric_id(value, "release")


def verify_asset(value: Mapping[str, Any], *, name: str, size: int) -> str:
    if value.get("name") != name or value.get("size") != size:
        raise MetadataTargetConflict("asset name or size differs from the reviewed payload")
    return require_numeric_id(value, "asset")


__all__ = [
    "GiteaMetadataClient",
    "MetadataTargetConflict",
    "verify_asset",
    "verify_label",
    "verify_release",
]
