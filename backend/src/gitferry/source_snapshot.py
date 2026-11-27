"""Bounded, read-only source snapshot capture from the Gitea API."""

from __future__ import annotations

import json
import ssl
import time
from collections.abc import Mapping
from typing import Any

import httpx

from gitferry.connection_profiles import ProfileValidationError, normalize_url
from gitferry.gitea_provisioning import (
    ProvisioningTargetConflict,
    _repository_segments,
)


class SourceSnapshotError(RuntimeError):
    """A source response was invalid, unsafe, or exceeded the capture bounds."""


_MAX_RESPONSE = 2 * 1024 * 1024
_PAGE_SIZE = 50
_MAX_PAGES = 100
_MAX_METADATA = 2000
_DEADLINE = 120.0


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceSnapshotError("source response contains duplicate JSON keys")
        result[key] = value
    return result


def _id(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise SourceSnapshotError(f"source {label} has an invalid native id")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise SourceSnapshotError(f"source {label} has an invalid value")
    return value


def _user(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise SourceSnapshotError(f"source {label} is malformed")
    return {
        "global_id": str(_id(value.get("id"), f"{label} id")),
        "login": _text(value.get("login"), f"{label} login"),
    }


def _asset(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceSnapshotError(f"source {label} is malformed")
    size = value.get("size")
    if type(size) is not int or size < 0:
        raise SourceSnapshotError(f"source {label} size is invalid")
    return {
        "global_id": str(_id(value.get("id"), label)),
        "uuid": _text(value.get("uuid"), f"{label} uuid"),
        "name": _text(value.get("name"), f"{label} name"),
        "size": size,
        "created_at": _text(value.get("created_at"), f"{label} created_at"),
        "download_url": _text(value.get("browser_download_url"), f"{label} download URL"),
    }


class _SnapshotReader:
    def __init__(
        self,
        base_url: str,
        token: str,
        context: ssl.SSLContext,
        transport: httpx.BaseTransport | None,
    ):
        if not isinstance(token, str) or not token:
            raise SourceSnapshotError("source credential is invalid")
        try:
            base_url = normalize_url(base_url)
        except ProfileValidationError:
            raise SourceSnapshotError("source endpoint is invalid") from None
        self.token = token
        self.deadline = time.monotonic() + _DEADLINE
        self.client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": "token " + token, "Accept-Encoding": "identity"},
            verify=context,
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.metadata_count = 0
        self.total_entries = 0
        self.total_bytes = 0
        self.limitations: list[str] = []
        self.last_response_headers: dict[str, str] = {}

    def close(self) -> None:
        self.client.close()

    def _check(self) -> None:
        if time.monotonic() > self.deadline:
            raise SourceSnapshotError("source snapshot deadline exceeded")

    def _read(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        self._check()
        try:
            with self.client.stream("GET", path, params=params) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("content-encoding", "identity") != "identity"
                ):
                    raise SourceSnapshotError("source API rejected a snapshot request")
                self.last_response_headers = dict(response.headers)
                body = bytearray()
                for chunk in response.iter_bytes():
                    self._check()
                    if len(body) + len(chunk) > _MAX_RESPONSE:
                        raise SourceSnapshotError("source response exceeds the supported size")
                    self.total_bytes += len(chunk)
                    if self.total_bytes > 16 * 1024 * 1024:
                        raise SourceSnapshotError(
                            "source snapshot exceeds the aggregate response bound"
                        )
                    body.extend(chunk)
        except SourceSnapshotError:
            raise
        except (httpx.HTTPError, ValueError, OSError):
            raise SourceSnapshotError("source API request failed") from None
        try:
            value = json.loads(bytes(body), object_pairs_hook=_no_duplicate_keys)
        except (ValueError, RecursionError, SourceSnapshotError):
            raise SourceSnapshotError("source response is invalid JSON") from None
        self._reject_credential(value)
        return value

    def _reject_credential(self, value: Any) -> None:
        pending: list[tuple[Any, int]] = [(value, 0)]
        while pending:
            current, depth = pending.pop()
            if depth > 32:
                raise SourceSnapshotError("source response is too deeply nested")
            if isinstance(current, str):
                if self.token in current:
                    raise SourceSnapshotError("source response reflected the credential")
            elif isinstance(current, Mapping):
                for key, item in current.items():
                    pending.append((key, depth + 1))
                    pending.append((item, depth + 1))
            elif isinstance(current, list):
                pending.extend((item, depth + 1) for item in current)

    def _page(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        metadata: bool = False,
    ) -> list[Any]:
        items: list[Any] = []
        identifiers: set[int] = set()
        for page in range(1, _MAX_PAGES + 1):
            query = dict(params or {})
            query.update(page=page, limit=_PAGE_SIZE)
            value = self._read(path, query)
            if not isinstance(value, list):
                raise SourceSnapshotError("source list response is malformed")
            if len(value) > _PAGE_SIZE:
                raise SourceSnapshotError("source page exceeds the supported size")
            for item in value:
                if not isinstance(item, Mapping):
                    raise SourceSnapshotError("source list item is malformed")
                native_id = _id(item.get("id"), "list item")
                if native_id in identifiers:
                    raise SourceSnapshotError("source list contains duplicate native ids")
                identifiers.add(native_id)
            self.total_entries += len(value)
            if self.total_entries > 10000:
                raise SourceSnapshotError("source snapshot exceeds the aggregate entry bound")
            items.extend(value)
            if metadata:
                self.metadata_count += len(value)
            if self.metadata_count > _MAX_METADATA:
                raise SourceSnapshotError("source metadata exceeds the supported bound")
            if len(value) < _PAGE_SIZE:
                return items
        raise SourceSnapshotError("source list exceeds the supported page bound")

    def _team(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            raise SourceSnapshotError("source team response is malformed")
        team_id = _id(value.get("id"), "team")
        name = _text(value.get("name"), "team name")
        permission = value.get("permission")
        units = value.get("units_map")
        if (
            not isinstance(permission, str)
            or not isinstance(units, Mapping)
            or any(not isinstance(unit, str) for unit in units.values())
        ):
            raise SourceSnapshotError("source team permissions are malformed")
        if (
            permission in {"admin", "owner"}
            or units.get("repo.code") in {"admin", "owner"}
            or units.get("repo.issues") in {"admin", "owner"}
        ):
            self.limitations.append("administrative owner teams are excluded")
            return None
        if any(
            key not in {"repo.code", "repo.issues"} and value != "none"
            for key, value in units.items()
        ):
            self.limitations.append(
                "additional team units are unsupported and were not transferred"
            )
        # Gitea 1.27.2 reports aggregate permission="none" when units_map
        # defines the effective rights. Only equal code/issue units are supported.
        effective = units.get("repo.code")
        if (
            effective not in {"read", "write"}
            or units.get("repo.issues") != effective
            or (permission not in {"none", effective})
        ):
            self.limitations.append("teams with unsupported or split permissions are excluded")
            return None
        return {"global_id": str(team_id), "name": name, "permission": effective, "_id": team_id}

    def capture(
        self,
        selections: list[dict[str, str]],
        *,
        include_metadata: bool,
        include_members: bool,
        include_labels: bool = False,
        include_attachments: bool = False,
        include_releases: bool = False,
        include_release_assets: bool = False,
    ) -> dict[str, Any]:
        repositories: list[dict[str, Any]] = []
        seen: set[str] = set()
        for selection in selections:
            if not isinstance(selection, dict) or set(selection) != {"repository", "global_id"}:
                raise SourceSnapshotError("source selection is malformed")
            repository = selection["repository"]
            selected_id = selection["global_id"]
            if (
                not isinstance(repository, str)
                or not isinstance(selected_id, str)
                or repository in seen
            ):
                raise SourceSnapshotError("source selection is invalid")
            seen.add(repository)
            try:
                owner, name = _repository_segments(repository)
            except (ProvisioningTargetConflict, TypeError, ValueError):
                # Keep path-validation implementation details out of the public
                # snapshot error surface.
                raise SourceSnapshotError("source selection repository is invalid") from None
            repo = self._read(f"/repos/{owner}/{name}")
            if (
                not isinstance(repo, Mapping)
                or str(_id(repo.get("id"), "repository")) != selected_id
                or repo.get("full_name") != repository
            ):
                raise SourceSnapshotError("source repository identity does not match the selection")
            repo_owner = repo.get("owner")
            if not isinstance(repo_owner, Mapping) or repo_owner.get("login") != owner:
                raise SourceSnapshotError("source repository owner does not match the selection")
            organization = self._read(f"/orgs/{owner}")
            if not isinstance(organization, Mapping) or organization.get("name") != owner:
                raise SourceSnapshotError("source organization identity is invalid")
            organization_id = _id(organization.get("id"), "organization")
            if not isinstance(repo_owner, Mapping) or repo_owner.get("id") != organization_id:
                raise SourceSnapshotError("source repository owner does not match organization")
            organization_out = {
                "global_id": str(organization_id),
                "name": _text(organization.get("name"), "organization name"),
            }
            teams: list[dict[str, Any]] = []
            for raw_team in self._page(f"/repos/{owner}/{name}/teams"):
                # Repository membership lists may contain abbreviated permissions.
                # As in the provisioning adapter, read the team itself before
                # deciding which permission dimensions can be represented.
                listed_id = _id(raw_team.get("id"), "team")
                detail = self._read(f"/teams/{listed_id}")
                if not isinstance(detail, Mapping) or detail.get("id") != listed_id:
                    raise SourceSnapshotError("source team identity changed during capture")
                team = self._team(detail)
                if team is None:
                    continue
                team_id = team.pop("_id")
                members: list[dict[str, str]] = []
                if include_members:
                    for member in self._page(f"/teams/{team_id}/members"):
                        members.append(_user(member, "team member"))
                teams.append({**team, "members": members})
            issues: list[dict[str, Any]] = []
            source_labels: list[dict[str, Any]] = []
            releases: list[dict[str, Any]] = []
            if include_labels:
                for raw_label in self._page(f"/repos/{owner}/{name}/labels", metadata=True):
                    source_labels.append(
                        {
                            "global_id": str(_id(raw_label.get("id"), "label")),
                            "name": _text(raw_label.get("name"), "label name"),
                            "color": _text(raw_label.get("color"), "label color"),
                            "description": _text(
                                raw_label.get("description", ""), "label description"
                            ),
                        }
                    )
            if include_metadata:
                for raw_issue in self._page(
                    f"/repos/{owner}/{name}/issues",
                    params={"state": "all", "type": "issues"},
                    metadata=True,
                ):
                    if (
                        not isinstance(raw_issue, Mapping)
                        or raw_issue.get("pull_request") is not None
                    ):
                        raise SourceSnapshotError("source issue list contains a pull request")
                    index = _id(raw_issue.get("number"), "issue index")
                    issue_labels = raw_issue.get("labels")
                    if not isinstance(issue_labels, list) or any(
                        not isinstance(label, Mapping) or not isinstance(label.get("name"), str)
                        for label in issue_labels
                    ):
                        raise SourceSnapshotError("source issue labels are malformed")
                    issue: dict[str, Any] = {
                        "global_id": str(_id(raw_issue.get("id"), "issue")),
                        "issue_index": index,
                        "title": _text(raw_issue.get("title"), "issue title"),
                        "body": _text(raw_issue.get("body"), "issue body"),
                        "state": _text(raw_issue.get("state"), "issue state"),
                        "labels": sorted(label["name"] for label in issue_labels),
                        "author": _user(raw_issue.get("user"), "issue author"),
                        "created_at": _text(raw_issue.get("created_at"), "issue created_at"),
                        "updated_at": _text(raw_issue.get("updated_at"), "issue updated_at"),
                        "comments": [],
                        "attachments": [],
                    }
                    if include_attachments:
                        for raw_asset in self._page(
                            f"/repos/{owner}/{name}/issues/{index}/assets", metadata=True
                        ):
                            issue["attachments"].append(_asset(raw_asset, "issue attachment"))
                    for raw_comment in self._page(
                        f"/repos/{owner}/{name}/issues/{index}/comments", metadata=True
                    ):
                        if not isinstance(raw_comment, Mapping):
                            raise SourceSnapshotError("source comment is malformed")
                        comment: dict[str, Any] = {
                            "global_id": str(_id(raw_comment.get("id"), "comment")),
                            "body": _text(raw_comment.get("body"), "comment body"),
                            "author": _user(raw_comment.get("user"), "comment author"),
                            "created_at": _text(
                                raw_comment.get("created_at"), "comment created_at"
                            ),
                            "updated_at": _text(
                                raw_comment.get("updated_at"), "comment updated_at"
                            ),
                            "attachments": [],
                        }
                        if include_attachments:
                            for raw_asset in self._page(
                                f"/repos/{owner}/{name}/issues/comments/{comment['global_id']}/assets",
                                metadata=True,
                            ):
                                comment["attachments"].append(
                                    _asset(raw_asset, "comment attachment")
                                )
                        issue["comments"].append(comment)
                    issues.append(issue)
            if include_releases:
                for raw_release in self._page(f"/repos/{owner}/{name}/releases", metadata=True):
                    release = {
                        "global_id": str(_id(raw_release.get("id"), "release")),
                        "tag_name": _text(raw_release.get("tag_name"), "release tag"),
                        "target_commitish": _text(
                            raw_release.get("target_commitish"), "release target"
                        ),
                        "name": _text(raw_release.get("name", ""), "release name"),
                        "body": _text(raw_release.get("body", ""), "release body"),
                        "draft": raw_release.get("draft"),
                        "prerelease": raw_release.get("prerelease"),
                        "author": _user(raw_release.get("author"), "release author"),
                        "created_at": _text(raw_release.get("created_at"), "release created_at"),
                        "published_at": raw_release.get("published_at"),
                        "assets": [],
                    }
                    if (
                        type(release["draft"]) is not bool
                        or type(release["prerelease"]) is not bool
                        or (
                            release["published_at"] is not None
                            and not isinstance(release["published_at"], str)
                        )
                    ):
                        raise SourceSnapshotError("source release state is malformed")
                    if include_release_assets:
                        for raw_asset in self._page(
                            f"/repos/{owner}/{name}/releases/{release['global_id']}/assets",
                            metadata=True,
                        ):
                            release["assets"].append(_asset(raw_asset, "release asset"))
                    releases.append(release)
            repositories.append(
                {
                    "repository": repository,
                    "global_id": selected_id,
                    "organization": organization_out,
                    "teams": teams,
                    "issues": issues,
                    "labels": source_labels,
                    "releases": releases,
                }
            )
        if not include_metadata:
            self.limitations.append("issue and comment metadata was explicitly excluded")
        if not include_members:
            self.limitations.append("team members were explicitly excluded")
        omitted = ["LFS", "wiki", "pull requests", "reviews", "other content classes"]
        if not include_labels:
            omitted.append("labels")
        if not include_attachments:
            omitted.append("attachments")
        if not include_releases:
            omitted.append("releases")
        if not include_release_assets:
            omitted.append("release assets")
        self.limitations.extend(
            [
                ", ".join(omitted) + " are not captured",
                "native timestamps are preserved as source strings; "
                "create endpoints do not restore them",
            ]
        )
        return {"repositories": repositories, "limitations": list(dict.fromkeys(self.limitations))}


def read_repository_identity(
    base_url: str,
    token: str,
    context: ssl.SSLContext,
    repository: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Read a native repository ID with the same strict response bounds as capture."""
    owner, name = _repository_segments(repository)
    reader = _SnapshotReader(base_url, token, context, transport)
    try:
        value = reader._read(f"/repos/{owner}/{name}")
        if not isinstance(value, Mapping):
            raise SourceSnapshotError("repository identity is malformed")
        return str(_id(value.get("id"), "repository"))
    finally:
        reader.close()


def capture_source(
    base_url: str,
    token: str,
    context: ssl.SSLContext,
    selections: list[dict[str, str]],
    *,
    include_metadata: bool,
    include_members: bool,
    include_labels: bool = False,
    include_attachments: bool = False,
    include_releases: bool = False,
    include_release_assets: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    reader = _SnapshotReader(base_url, token, context, transport)
    try:
        return reader.capture(
            selections,
            include_metadata=include_metadata,
            include_members=include_members,
            include_labels=include_labels,
            include_attachments=include_attachments,
            include_releases=include_releases,
            include_release_assets=include_release_assets,
        )
    finally:
        reader.close()
