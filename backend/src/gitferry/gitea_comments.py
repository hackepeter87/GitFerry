"""Bounded Gitea issue-comment reads and writes with explicit parent verification."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from gitferry.state_store import TargetConfirmation


class CommentTargetConflict(RuntimeError):
    """A mapped comment is absent from, or does not belong to, its expected issue."""


class GiteaCommentClient:
    """Small adapter for normal issue comments; credentials remain outside durable state."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str,
        password: str,
        verify: bool | str = True,
        timeout: float = 20.0,
    ):
        self.authenticated_login = username
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            auth=(username, password),
            verify=verify,
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GiteaCommentClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def list_comments(self, repository: str, issue_index: int) -> list[dict[str, Any]]:
        """Read the complete issue comment list without a time-window filter."""

        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._request(
                "GET",
                f"/repos/{repository}/issues/{issue_index}/comments",
                params={"page": page, "limit": 50},
            )
            if not isinstance(batch, list):
                raise CommentTargetConflict("Gitea comment list response is not an array")
            comments.extend(batch)
            if len(batch) < 50:
                return comments
            page += 1

    @staticmethod
    def _belongs_to_issue(comment: Mapping[str, Any], issue_index: int) -> bool:
        issue_url = comment.get("issue_url")
        return isinstance(issue_url, str) and issue_url.rstrip("/").endswith(f"/{issue_index}")

    def verified_comment(
        self, repository: str, issue_index: int, comment_global_id: str
    ) -> dict[str, Any]:
        matches = [
            comment
            for comment in self.list_comments(repository, issue_index)
            if str(comment.get("id")) == comment_global_id
        ]
        if len(matches) != 1 or not self._belongs_to_issue(matches[0], issue_index):
            raise CommentTargetConflict(
                "mapped comment is not uniquely present on the expected parent issue"
            )
        return matches[0]

    def create_comment(self, repository: str, issue_index: int, body: str) -> dict[str, Any]:
        value = self._request(
            "POST",
            f"/repos/{repository}/issues/{issue_index}/comments",
            json={"body": body},
        )
        if not isinstance(value, dict) or not self._belongs_to_issue(value, issue_index):
            raise CommentTargetConflict("created comment response has the wrong parent issue")
        return value

    def update_comment(self, repository: str, comment_global_id: str, body: str) -> dict[str, Any]:
        value = self._request(
            "PATCH",
            f"/repos/{repository}/issues/comments/{comment_global_id}",
            json={"body": body},
        )
        if not isinstance(value, dict):
            raise CommentTargetConflict("updated comment response is not an object")
        return value


def confirmation_from_comment(comment: Mapping[str, Any], issue_index: int) -> TargetConfirmation:
    identifier = comment.get("id")
    if not isinstance(identifier, int) and not (
        isinstance(identifier, str) and identifier.isdecimal()
    ):
        raise CommentTargetConflict("comment response has no stable numeric id")
    created_at = comment.get("created_at")
    return TargetConfirmation(
        target_global_id=str(identifier),
        target_issue_index=issue_index,
        target_native_created_at=created_at if isinstance(created_at, str) else None,
    )


def verify_comment_facts(
    comment: Mapping[str, Any],
    *,
    issue_index: int,
    expected_body: str,
    expected_author_login: str,
) -> None:
    if not GiteaCommentClient._belongs_to_issue(comment, issue_index):
        raise CommentTargetConflict("comment belongs to another parent issue")
    user = comment.get("user")
    actual_login = user.get("login") if isinstance(user, Mapping) else None
    if actual_login != expected_author_login:
        raise CommentTargetConflict("native comment author does not match identity binding")
    if comment.get("body") != expected_body:
        raise CommentTargetConflict("comment body does not match the expected content")
