"""Bounded, native Gitea wiki discovery and technical bootstrap."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .gitea_provisioning import (
    GiteaProvisioningClient,
    ProvisioningTargetConflict,
    _repository_segments,
)

_PAGE_LIMIT = 50
_MAX_PAGES = 5000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class GiteaWikiError(ProvisioningTargetConflict):
    """A wiki response cannot be safely classified or used."""


@dataclass(frozen=True)
class WikiState:
    repository_id: int
    pages: tuple[dict[str, Any], ...]


class GiteaWikiClient(GiteaProvisioningClient):
    """Read native wiki facts and perform one explicit technical bootstrap."""

    def verify_native_settings(self, *, lfs: bool) -> None:
        try:
            _, value = self._bounded_json("GET", "/settings/repository", expected_status=200)
        except httpx.HTTPStatusError:
            raise GiteaWikiError("native repository capabilities could not be checked") from None
        if not isinstance(value, dict) or value.get("http_git_disabled") is not False:
            raise GiteaWikiError("target HTTP Git capability is unavailable or unknown")
        if lfs and value.get("lfs_disabled") is not False:
            raise GiteaWikiError("target LFS capability is unavailable or unknown")

    def _wiki_parent(
        self, repository: str, expected_repository_id: str | int
    ) -> tuple[str, str, dict[str, Any]]:
        owner, name = _repository_segments(repository)
        value = self.find_repository(repository)
        if value is None:
            raise GiteaWikiError("wiki repository was not found")
        actual_id = value.get("id")
        if (
            type(expected_repository_id) is not int
            or expected_repository_id <= 0
            or type(actual_id) is not int
            or actual_id <= 0
            or actual_id != expected_repository_id
        ):
            raise GiteaWikiError("wiki repository identity does not match the selected repository")
        if value.get("full_name") != repository:
            raise GiteaWikiError("wiki repository name does not match the selected repository")
        if value.get("has_wiki") is not True:
            raise GiteaWikiError("wiki is disabled for the selected repository")
        external = value.get("external_wiki")
        if external is not None and (
            not isinstance(external, dict)
            or not isinstance(external.get("external_wiki_url", ""), str)
            or external.get("external_wiki_url", "")
        ):
            raise GiteaWikiError("external wiki is outside the native wiki contract")
        return owner, name, value

    @staticmethod
    def _total(response: httpx.Response) -> int:
        raw = response.headers.get("X-Total-Count")
        if raw is None or len(raw) > 5 or not raw.isascii() or not raw.isdecimal():
            raise GiteaWikiError("wiki pagination total is missing or invalid")
        total = int(raw)
        if total > _MAX_PAGES or total < 0:
            raise GiteaWikiError("wiki page count exceeds the supported bound")
        return total

    @staticmethod
    def _pages(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise GiteaWikiError("wiki page response is not an object list")
        return [dict(item) for item in value]

    def read_wiki_state(self, repository: str, expected_repository_id: str | int) -> WikiState:
        owner, name, parent = self._wiki_parent(repository, expected_repository_id)
        path = f"/repos/{owner}/{name}/wiki/pages"
        pages: list[dict[str, Any]] = []
        seen_sub_urls: set[str] = set()
        seen_titles: set[str] = set()
        page = 1
        total: int | None = None
        while total is None or len(pages) < total:
            try:
                response, payload = self._bounded_json(
                    "GET", path, params={"page": page, "limit": _PAGE_LIMIT}
                )
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    raise GiteaWikiError(
                        "wiki pages returned 404; native empty state is not distinguishable"
                    ) from None
                raise GiteaWikiError("wiki pages request failed") from None
            current_total = self._total(response)
            if total is None:
                total = current_total
            elif current_total != total:
                raise GiteaWikiError("wiki pagination total changed during read")
            values = self._pages(payload)
            expected_size = min(_PAGE_LIMIT, total - len(pages))
            if len(values) != expected_size:
                raise GiteaWikiError("wiki pagination has a truncated page")
            for value in values:
                sub_url = value.get("sub_url")
                title = value.get("title")
                if (
                    not isinstance(sub_url, str)
                    or not sub_url
                    or not isinstance(title, str)
                    or not title
                ):
                    raise GiteaWikiError("wiki page identity is incomplete")
                if sub_url in seen_sub_urls or title in seen_titles:
                    raise GiteaWikiError("wiki page identity is duplicated")
                seen_sub_urls.add(sub_url)
                seen_titles.add(title)
                pages.append(value)
            if total == 0:
                break
            if not values:
                raise GiteaWikiError("wiki pagination ended before its declared total")
            page += 1
            if page > _MAX_PAGES:
                raise GiteaWikiError("wiki pagination exceeds the supported bound")
        if total is None or len(pages) != total:
            raise GiteaWikiError("wiki pagination total does not match returned pages")
        return WikiState(repository_id=parent["id"], pages=tuple(pages))

    @staticmethod
    def _page_name(title: str) -> str:
        if not isinstance(title, str) or not title or len(title) > 255:
            raise GiteaWikiError("wiki page title is outside the supported bound")
        if any(ord(char) < 32 or ord(char) == 127 for char in title):
            raise GiteaWikiError("wiki page title contains a control character")
        return quote(title, safe="")

    def get_page(
        self, repository: str, expected_repository_id: str | int, title: str
    ) -> dict[str, Any]:
        owner, name, _ = self._wiki_parent(repository, expected_repository_id)
        try:
            _, value = self._bounded_json(
                "GET", f"/repos/{owner}/{name}/wiki/page/{self._page_name(title)}"
            )
        except httpx.HTTPStatusError:
            raise GiteaWikiError("wiki page request failed") from None
        if not isinstance(value, dict):
            raise GiteaWikiError("wiki page response is not an object")
        if value.get("title") != title:
            raise GiteaWikiError("wiki response page identity does not match the request")
        return value

    def bootstrap(
        self,
        repository: str,
        expected_repository_id: str | int,
        title: str,
        content: str,
        message: str,
    ) -> dict[str, Any]:
        owner, name, _ = self._wiki_parent(repository, expected_repository_id)
        self._page_name(title)
        if not isinstance(content, str) or not content or len(content.encode()) > 1_048_576:
            raise GiteaWikiError("wiki content is outside the supported bound")
        if not isinstance(message, str) or not message or len(message.encode()) > 4096:
            raise GiteaWikiError("wiki commit message is outside the supported bound")
        try:
            response, value = self._bounded_json(
                "POST",
                f"/repos/{owner}/{name}/wiki/new",
                expected_status=201,
                json={
                    "title": title,
                    "content_base64": base64.b64encode(content.encode()).decode("ascii"),
                    "message": message,
                },
            )
        except httpx.HTTPStatusError:
            raise GiteaWikiError("wiki bootstrap request failed") from None
        if not isinstance(value, dict):
            raise GiteaWikiError("wiki bootstrap response is not an object")
        if value.get("title") != title or value.get("sub_url") != title:
            raise GiteaWikiError("wiki bootstrap response has the wrong page identity")
        encoded = value.get("content_base64")
        try:
            decoded = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else None
        except (ValueError, TypeError):
            decoded = None
        if decoded != content.encode():
            raise GiteaWikiError("wiki bootstrap response content does not match the request")
        commit = value.get("last_commit")
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(commit_sha, str) or not _SHA.fullmatch(commit_sha):
            raise GiteaWikiError("wiki bootstrap response has no native commit SHA")
        return value

    def _bounded_json(
        self, method: str, path: str, *, expected_status: int | None = None, **kwargs: Any
    ) -> tuple[httpx.Response, Any]:
        try:
            with self._client.stream(method, path, **kwargs) as response:
                if expected_status is not None and response.status_code != expected_status:
                    response.raise_for_status()
                    raise GiteaWikiError("wiki response status was not the documented status")
                elif response.status_code >= 400:
                    response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes(8192):
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise GiteaWikiError("wiki response exceeds its supported bound")
            value = json.loads(body)
        except GiteaWikiError:
            raise
        except httpx.HTTPStatusError:
            raise
        except (httpx.HTTPError, ValueError, RecursionError):
            raise GiteaWikiError("wiki response was unavailable or invalid") from None
        return response, value

    def find_repository(self, repository: str) -> dict[str, Any] | None:
        owner, name = _repository_segments(repository)
        try:
            response, value = self._bounded_json("GET", f"/repos/{owner}/{name}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise GiteaWikiError("wiki repository lookup failed") from None
        if not isinstance(value, dict):
            raise GiteaWikiError("wiki repository response is not an object")
        return value
