"""Selected native inventory; unavailable enumeration is never an empty collection."""

from __future__ import annotations

import json
import re
import ssl
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, Literal

import httpx

from gitferry.content_inventory import (
    CONTENT_CLASSES,
    ClassObservation,
    InventoryDocument,
    InventoryObject,
    RepositoryInventory,
)
from gitferry.gitea_provisioning import _path_segment, _repository_segments
from gitferry.source_snapshot import (
    _PAGE_SIZE,
    SourceSnapshotError,
    _id,
    _SnapshotReader,
    _text,
    _user,
)


class InventoryCollectionError(ValueError):
    """Source identity or inventory cannot be established safely."""


class _InventoryReader(_SnapshotReader):
    def _entries(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise SourceSnapshotError("inventory list is malformed")
        self.total_entries += len(value)
        if self.total_entries > 10000:
            raise SourceSnapshotError("inventory exceeds its aggregate entry bound")
        return value

    def page(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        ids: bool = True,
        null_if_zero: bool = False,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[int] = set()
        expected: int | None = None
        for page in range(1, 101):
            raw = self._read(path, {**dict(params or {}), "page": page, "limit": _PAGE_SIZE})
            header = self.last_response_headers.get("x-total-count")
            # Qualified Gitea issue-reaction endpoint returns null with explicit
            # X-Total-Count: 0. Other lists never infer zero from null.
            if null_if_zero and raw is None and header == "0":
                raw = []
            values = self._entries(raw)
            if header is None or not header.isascii() or not header.isdigit() or len(header) > 8:
                raise SourceSnapshotError("inventory pagination total is missing or invalid")
            total = int(header)
            expected = total if expected is None else expected
            if (
                expected != total
                or total > 5000
                or len(values) != min(_PAGE_SIZE, total - len(items))
            ):
                raise SourceSnapshotError("inventory pagination is incomplete or changed")
            for value in values:
                if ids:
                    identifier = _id(value.get("id"), "inventory list item")
                    if identifier in seen:
                        raise SourceSnapshotError("inventory list has duplicate IDs")
                    seen.add(identifier)
            items.extend(values)
            if len(items) == total:
                return items
        raise SourceSnapshotError("inventory exceeds its page bound")

    def full(self, path: str, *, empty_reactions: bool = False) -> list[dict[str, Any]]:
        # Only endpoints documented without pagination use this method. Byte,
        # entry and deadline limits are still enforced by the shared reader.
        raw = self._read(path)
        # Gitea 1.27.2 comment reactions use JSON null for the empty full list.
        # Callers enable this only for the separately qualified reaction route.
        return self._entries([] if empty_reactions and raw is None else raw)


def _unknown(
    name: str, reason: str, status: Literal["blocked", "not-supported"] = "blocked"
) -> ClassObservation:
    return ClassObservation(content_class=name, status=status, count=None, reason=reason)


def _observed(
    name: str, items: list[InventoryObject], limitations: list[str] | None = None
) -> ClassObservation:
    return ClassObservation(
        content_class=name,
        status="observed",
        count=len(items),
        objects=sorted(items, key=lambda value: (value.namespace, value.key)),
        limitations=limitations or [],
    )


def _attempt(name: str, call: Callable[[], ClassObservation]) -> ClassObservation:
    try:
        return call()
    except (SourceSnapshotError, ValueError, TypeError, KeyError):
        return _unknown(
            name,
            "Enumeration failed or returned inconsistent/unsupported facts; "
            "inspect source access and retry with a new snapshot",
        )


def _author(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    user = _user(value, "inventory author")
    return {"account_id": user["global_id"], "login": user["login"]}


def _parent(content_class: str, item: InventoryObject) -> dict[str, str]:
    return {"content_class": content_class, "key": item.key}


def _object(
    value: dict[str, Any],
    namespace: str,
    fields: tuple[str, ...],
    *,
    parent: dict[str, str] | None = None,
    issue: bool = False,
    author_key: str | None = "user",
    payload: bool = False,
) -> InventoryObject:
    identifier = str(_id(value.get("id"), "inventory object"))
    size = value.get("size") if payload else None
    if payload and (type(size) is not int or size < 0):
        raise SourceSnapshotError("payload size is missing or invalid")
    facts = {key: value[key] for key in fields if key in value}
    if issue:
        labels = value.get("labels", [])
        if not isinstance(labels, list):
            raise SourceSnapshotError("issue labels are malformed")
        facts["label_ids"] = sorted(str(_id(label.get("id"), "issue label")) for label in labels)
        milestone = value.get("milestone")
        if milestone is not None and not isinstance(milestone, dict):
            raise SourceSnapshotError("issue milestone is malformed")
        facts["milestone_id"] = str(_id(milestone.get("id"), "milestone")) if milestone else None
        assignees = value.get("assignees", []) or []
        if not isinstance(assignees, list):
            raise SourceSnapshotError("issue assignees are malformed")
        facts["assignees"] = sorted(
            (_user(user, "assignee") for user in assignees), key=lambda user: user["global_id"]
        )
    return InventoryObject(
        key=identifier,
        namespace=namespace,
        native_id=identifier,
        issue_index=str(_id(value.get("number"), "issue index")) if issue else None,
        parent=parent,
        author=_author(value.get(author_key)) if author_key else None,
        facts=facts,
        size_bytes=size,
    )


def _require(observations: dict[str, ClassObservation], *classes: str) -> None:
    if any(observations[name].status != "observed" for name in classes):
        raise SourceSnapshotError("parent enumeration is incomplete")


def collect_inventory(
    base_url: str,
    token: str,
    context: ssl.SSLContext,
    selections: list[dict[str, str]],
    requested_classes: list[str],
    *,
    source_instance_key: str,
    installation_marker: str,
    snapshot_id: str,
    created_at: str,
    transport: httpx.BaseTransport | None = None,
) -> InventoryDocument:
    reader = _InventoryReader(base_url, token, context, transport)
    try:
        version = reader._read("/version")
        if not isinstance(version, dict) or version.get("version") != "1.27.2":
            raise InventoryCollectionError("source inventory requires qualified Gitea 1.27.2")
        repositories = [
            _repository(reader, selection, requested_classes) for selection in selections
        ]
        return InventoryDocument(
            snapshot_id=snapshot_id,
            created_at=created_at,
            source_instance_key=source_instance_key,
            source_installation_marker=installation_marker,
            gitea_version="1.27.2",
            repositories=repositories,
        )
    except (SourceSnapshotError, TypeError, ValueError):
        raise InventoryCollectionError(
            "selected source identity or inventory is unavailable"
        ) from None
    finally:
        reader.close()


def _repository(
    reader: _InventoryReader, selection: dict[str, str], requested: list[str]
) -> RepositoryInventory:
    if set(selection) != {"repository", "global_id"}:
        raise InventoryCollectionError("invalid repository selection")
    scope = selection["repository"]
    owner, name = _repository_segments(scope)
    path = f"/repos/{owner}/{name}"
    repo = reader._read(path)
    if (
        not isinstance(repo, dict)
        or str(_id(repo.get("id"), "repository")) != selection["global_id"]
        or repo.get("full_name") != scope
    ):
        raise InventoryCollectionError("selected repository identity differs")
    repo_owner = repo.get("owner")
    if not isinstance(repo_owner, dict) or repo_owner.get("login") != owner:
        raise InventoryCollectionError("selected repository owner differs")
    observations: dict[str, ClassObservation] = {}

    def collect(key: str, call: Callable[[], ClassObservation]) -> None:
        observations[key] = _attempt(key, call)

    if repo.get("empty") is True:
        observations["git_refs"] = _observed(
            "git_refs", [], ["Native Gitea empty=true; export separately rechecks Git refs"]
        )
    else:
        collect("git_refs", lambda: _refs(reader, path))
    observations["lfs"] = _unknown(
        "lfs",
        "No REST LFS enumeration; pointer and object-store inspection remains required",
        "not-supported",
    )
    collect("wiki", lambda: _wiki(reader, path))
    collect("organizations", lambda: _organization(reader, owner, repo_owner["id"]))
    collect("teams", lambda: _teams(reader, path))
    collect("permissions", lambda: _permissions(reader, path, observations))
    for class_name, suffix, params in (
        ("issues", "/issues", {"state": "all", "type": "issues"}),
        ("pull_requests", "/pulls", {"state": "all"}),
    ):
        collect(class_name, partial(_issues, reader, path + suffix, class_name, params))

    for class_name, suffix, fields, params in (
        ("labels", "/labels", ("name", "color", "description"), {}),
        (
            "milestones",
            "/milestones",
            ("title", "state", "description", "due_on", "created_at", "updated_at"),
            {"state": "all"},
        ),
        (
            "releases",
            "/releases",
            ("tag_name", "name", "body", "draft", "prerelease", "created_at", "published_at"),
            {},
        ),
    ):
        collect(class_name, partial(_flat, reader, path + suffix, class_name, fields, params))

    collect("comments", lambda: _comments(reader, path, observations, pulls=False))
    collect("pull_comments", lambda: _comments(reader, path, observations, pulls=True))
    collect("attachments", lambda: _assets(reader, path, observations, releases=False))
    collect("release_assets", lambda: _assets(reader, path, observations, releases=True))
    collect("timelines", lambda: _timelines(reader, path, observations))
    collect("reactions", lambda: _reactions(reader, path, observations))
    collect("reviews", lambda: _reviews(reader, path, observations))
    collect("review_comments", lambda: _review_comments(reader, path, observations))
    collect("identities", lambda: _identities(observations, requested))
    flags = {
        key: repo[key]
        for key in ("has_actions", "has_packages", "has_projects", "has_wiki", "has_releases")
        if key in repo
    }
    observations["other_content"] = _unknown(
        "other_content",
        "Additional Gitea features are not enumerated: " + json.dumps(flags, sort_keys=True),
        "not-supported",
    )
    size = repo.get("size")
    return RepositoryInventory(
        repository=scope,
        native_id=selection["global_id"],
        requested_classes=requested,
        classes=[observations[key] for key in CONTENT_CLASSES],
        git_size_estimate_bytes=size * 1024 if type(size) is int and size >= 0 else None,
        size_assumptions=[
            "Gitea repository size is an estimate in KiB; "
            "payload bytes and temporary copies are additional"
        ],
    )


def _flat(
    reader: _InventoryReader,
    endpoint: str,
    name: str,
    fields: tuple[str, ...],
    params: Mapping[str, Any],
) -> ClassObservation:
    return _observed(
        name,
        [
            _object(value, name, fields, author_key="author" if name == "releases" else None)
            for value in reader.page(endpoint, params=params)
        ],
    )


def _refs(reader: _InventoryReader, path: str) -> ClassObservation:
    objects = []
    for value in reader.full(path + "/git/refs"):
        ref = _text(value.get("ref"), "Git ref")
        obj = value.get("object")
        if (
            not ref.startswith("refs/")
            or not isinstance(obj, dict)
            or not isinstance(obj.get("sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", obj["sha"])
        ):
            raise SourceSnapshotError("Git ref identity is malformed")
        objects.append(
            InventoryObject(
                key=ref,
                namespace="git-ref",
                facts={"ref": ref, "sha": obj["sha"], "type": obj.get("type")},
            )
        )
    return _observed("git_refs", objects)


def _wiki(reader: _InventoryReader, path: str) -> ClassObservation:
    objects = []
    for value in reader.page(path + "/wiki/pages", ids=False):
        title = _text(value.get("title"), "wiki page")
        commit = value.get("last_commit")
        if not title or not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
            raise SourceSnapshotError("wiki page identity is malformed")
        objects.append(
            InventoryObject(
                key=title,
                namespace="wiki-path",
                facts={"title": title, "last_commit_sha": commit["sha"]},
            )
        )
    return _observed(
        "wiki",
        objects,
        [
            "Page paths are inventory references, not a permanent migration origin; "
            "native size is unknown"
        ],
    )


def _organization(reader: _InventoryReader, owner: str, expected: Any) -> ClassObservation:
    value = reader._read("/orgs/" + owner)
    if (
        not isinstance(value, dict)
        or value.get("name") != owner
        or _id(value.get("id"), "organization") != expected
    ):
        raise SourceSnapshotError("organization identity differs")
    return _observed(
        "organizations",
        [_object(value, "organization-id", ("name", "description", "visibility"), author_key=None)],
    )


def _teams(reader: _InventoryReader, path: str) -> ClassObservation:
    objects = []
    for listed in reader.full(path + "/teams"):
        identifier = _id(listed.get("id"), "team")
        detail = reader._read(f"/teams/{identifier}")
        if not isinstance(detail, dict) or detail.get("id") != identifier:
            raise SourceSnapshotError("team detail identity differs")
        members = [
            _user(value, "team member") for value in reader.page(f"/teams/{identifier}/members")
        ]
        item = _object(
            detail,
            "team-id",
            ("name", "permission", "units_map", "can_create_org_repo", "includes_all_repositories"),
            author_key=None,
        )
        item.facts["members"] = sorted(members, key=lambda member: member["global_id"])
        objects.append(item)
    return _observed("teams", objects)


def _permissions(
    reader: _InventoryReader, path: str, observations: dict[str, ClassObservation]
) -> ClassObservation:
    _require(observations, "teams")
    objects = [
        InventoryObject(
            key="team:" + team.key,
            namespace="repository-permission",
            parent=_parent("teams", team),
            facts={k: v for k, v in team.facts.items() if k != "members"},
        )
        for team in observations["teams"].objects
    ]
    for value in reader.page(path + "/collaborators"):
        user = _user(value, "collaborator")
        detail = reader._read(
            path + "/collaborators/" + _path_segment(user["login"], "login") + "/permission"
        )
        if (
            not isinstance(detail, dict)
            or _user(detail.get("user"), "collaborator permission") != user
        ):
            raise SourceSnapshotError("collaborator permission identity differs")
        objects.append(
            InventoryObject(
                key="user:" + user["global_id"],
                namespace="repository-permission",
                author={"account_id": user["global_id"], "login": user["login"]},
                facts={"permission": detail.get("permission")},
            )
        )
    return _observed(
        "permissions",
        objects,
        ["Technical owner and direct-collaborator facts do not authorize target grants"],
    )


def _issues(
    reader: _InventoryReader, path: str, name: str, params: dict[str, str]
) -> ClassObservation:
    objects = []
    for value in reader.page(path, params=params):
        if name == "issues" and value.get("pull_request") is not None:
            raise SourceSnapshotError("normal issue listing contains a pull request")
        item = _object(
            value,
            "global-issue-id" if name == "issues" else "global-pull-id",
            (
                "title",
                "body",
                "state",
                "created_at",
                "updated_at",
                "is_locked",
                "merged",
                "merge_commit_sha",
            ),
            issue=True,
        )
        for side in ("head", "base"):
            if side in value:
                ref = value[side]
                if not isinstance(ref, dict):
                    raise SourceSnapshotError("pull ref is malformed")
                item.facts[side] = {key: ref[key] for key in ("sha", "ref") if key in ref}
        objects.append(item)
    return _observed(name, objects)


def _parents(observations: dict[str, ClassObservation]) -> list[tuple[str, InventoryObject]]:
    _require(observations, "issues", "pull_requests")
    return [
        (name, item) for name in ("issues", "pull_requests") for item in observations[name].objects
    ]


def _comments(
    reader: _InventoryReader, path: str, observations: dict[str, ClassObservation], *, pulls: bool
) -> ClassObservation:
    kind = "pull_requests" if pulls else "issues"
    _require(observations, kind)
    objects = []
    for parent in observations[kind].objects:
        for value in reader.page(path + f"/issues/{parent.issue_index}/comments"):
            objects.append(
                _object(
                    value,
                    "global-comment-id",
                    ("body", "created_at", "updated_at"),
                    parent=_parent(kind, parent),
                )
            )
    return _observed("pull_comments" if pulls else "comments", objects)


def _assets(
    reader: _InventoryReader,
    path: str,
    observations: dict[str, ClassObservation],
    *,
    releases: bool,
) -> ClassObservation:
    objects = []
    if releases:
        _require(observations, "releases")
        parents = [
            ("releases", item, path + f"/releases/{item.native_id}/assets")
            for item in observations["releases"].objects
        ]
    else:
        parents = [
            (kind, item, path + f"/issues/{item.issue_index}/assets")
            for kind, item in _parents(observations)
        ]
        _require(observations, "comments", "pull_comments")
        parents.extend(
            (name, item, path + f"/issues/comments/{item.native_id}/assets")
            for name in ("comments", "pull_comments")
            for item in observations[name].objects
        )
    for kind, item, endpoint in parents:
        for value in reader.full(endpoint):
            objects.append(
                _object(
                    value,
                    "global-attachment-id",
                    ("uuid", "name", "size", "created_at"),
                    parent=_parent(kind, item),
                    payload=True,
                    author_key=None,
                )
            )
    return _observed("release_assets" if releases else "attachments", objects)


def _timelines(
    reader: _InventoryReader, path: str, observations: dict[str, ClassObservation]
) -> ClassObservation:
    objects = []
    for kind, parent in _parents(observations):
        for value in reader.page(path + f"/issues/{parent.issue_index}/timeline"):
            objects.append(
                _object(
                    value,
                    "global-timeline-id",
                    (
                        "type",
                        "body",
                        "created_at",
                        "updated_at",
                        "old_title",
                        "new_title",
                        "old_ref",
                        "new_ref",
                        "review_id",
                        "ref_commit_sha",
                    ),
                    parent=_parent(kind, parent),
                )
            )
    return _observed("timelines", objects)


def _reactions(
    reader: _InventoryReader, path: str, observations: dict[str, ClassObservation]
) -> ClassObservation:
    objects = []
    targets = [
        (kind, item, path + f"/issues/{item.issue_index}/reactions", True)
        for kind, item in _parents(observations)
    ]
    _require(observations, "comments", "pull_comments")
    targets.extend(
        (name, item, path + f"/issues/comments/{item.native_id}/reactions", False)
        for name in ("comments", "pull_comments")
        for item in observations[name].objects
    )
    for kind, parent, endpoint, paged in targets:
        values = (
            reader.page(endpoint, ids=False, null_if_zero=True)
            if paged
            else reader.full(endpoint, empty_reactions=True)
        )
        for value in values:
            author = _author(value.get("user"))
            content = _text(value.get("content"), "reaction")
            created = _text(value.get("created_at"), "reaction time")
            if author is None:
                raise SourceSnapshotError("reaction author is missing")
            key = json.dumps(
                [kind, parent.key, author["account_id"], content, created],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            objects.append(
                InventoryObject(
                    key=key,
                    namespace="reaction-natural-key",
                    parent=_parent(kind, parent),
                    author=author,
                    facts={"content": content, "created_at": created},
                )
            )
    return _observed("reactions", objects)


def _reviews(
    reader: _InventoryReader, path: str, observations: dict[str, ClassObservation]
) -> ClassObservation:
    _require(observations, "pull_requests")
    objects = []
    for parent in observations["pull_requests"].objects:
        for value in reader.page(path + f"/pulls/{parent.issue_index}/reviews"):
            item = _object(
                value,
                "global-review-id",
                ("state", "body", "submitted_at", "updated_at", "commit_id", "dismissed", "stale"),
                parent=_parent("pull_requests", parent),
            )
            item.issue_index = parent.issue_index
            objects.append(item)
    return _observed("reviews", objects)


def _review_comments(
    reader: _InventoryReader, path: str, observations: dict[str, ClassObservation]
) -> ClassObservation:
    _require(observations, "reviews")
    objects = []
    for parent in observations["reviews"].objects:
        for value in reader.full(
            path + f"/pulls/{parent.issue_index}/reviews/{parent.native_id}/comments"
        ):
            if value.get("pull_request_review_id") != int(parent.native_id or "0"):
                raise SourceSnapshotError("review comment parent differs")
            objects.append(
                _object(
                    value,
                    "global-review-comment-id",
                    (
                        "body",
                        "path",
                        "position",
                        "original_position",
                        "created_at",
                        "updated_at",
                        "commit_id",
                        "original_commit_id",
                    ),
                    parent=_parent("reviews", parent),
                )
            )
    return _observed("review_comments", objects)


def _identities(
    observations: dict[str, ClassObservation], requested: list[str]
) -> ClassObservation:
    required: tuple[str, ...] = (
        "teams",
        "permissions",
        "issues",
        "pull_requests",
        "comments",
        "pull_comments",
        "reviews",
        "review_comments",
        "releases",
        "reactions",
        "timelines",
    )
    required = tuple(name for name in required if name in requested)
    _require(observations, *required)
    users: dict[str, dict[str, str]] = {}

    def add(user: dict[str, str]) -> None:
        if users.setdefault(user["account_id"], user) != user:
            raise SourceSnapshotError("source identity has contradictory logins")

    for name in required:
        for item in observations[name].objects:
            if item.author is not None:
                add(item.author)
            for assignee in item.facts.get("assignees", []):
                add({"account_id": assignee["global_id"], "login": assignee["login"]})
    for team in observations["teams"].objects if "teams" in required else []:
        for member in team.facts["members"]:
            add({"account_id": member["global_id"], "login": member["login"]})
    return _observed(
        "identities",
        [
            InventoryObject(key=key, native_id=key, namespace="instance-account-id", facts=user)
            for key, user in users.items()
        ],
        ["Native source accounts are observed claims only, not approved cross-realm bindings"],
    )
