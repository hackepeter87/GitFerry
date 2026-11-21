from __future__ import annotations

import base64
import json

import httpx
import pytest
from gitferry.gitea_wiki import GiteaWikiClient, GiteaWikiError


def _client(handler):
    return GiteaWikiClient(
        "https://source.invalid",
        username="admin",
        token="synthetic-token",  # noqa: S106 - isolated MockTransport credential
        transport=httpx.MockTransport(handler),
    )


def _repo() -> dict[str, object]:
    return {"id": 17, "full_name": "org/repo", "has_wiki": True}


def test_read_wiki_requires_parent_and_collects_consistent_pages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/wiki/pages"):
            values = [
                {"sub_url": "p1", "title": "Page 1"},
                {"sub_url": "p2", "title": "Page 2"},
            ]
            return httpx.Response(200, headers={"X-Total-Count": "2"}, json=values)
        return httpx.Response(200, json=_repo())

    client = _client(handler)
    try:
        state = client.read_wiki_state("org/repo", 17)
    finally:
        client.close()
    assert state.repository_id == 17
    assert [page["title"] for page in state.pages] == ["Page 1", "Page 2"]
    assert requests[1].url.params["limit"] == "50"


@pytest.mark.parametrize("repo", [{"id": 18, "has_wiki": True}, {"id": 17, "has_wiki": False}])
def test_read_wiki_rejects_wrong_parent_or_disabled(repo: dict[str, object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=repo)

    client = _client(handler)
    try:
        with pytest.raises(GiteaWikiError):
            client.read_wiki_state("org/repo", 17)
    finally:
        client.close()


def test_read_wiki_rejects_partial_or_duplicate_pagination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/wiki/pages"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "2"},
                json=[{"sub_url": "same", "title": "Same"}],
            )
        return httpx.Response(200, json=_repo())

    client = _client(handler)
    try:
        with pytest.raises(GiteaWikiError):
            client.read_wiki_state("org/repo", 17)
    finally:
        client.close()


def test_empty_wiki_404_is_not_treated_as_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/wiki/pages"):
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=_repo())

    client = _client(handler)
    try:
        with pytest.raises(GiteaWikiError, match="not distinguishable"):
            client.read_wiki_state("org/repo", 17)
    finally:
        client.close()


def test_bootstrap_posts_once_and_requires_native_commit_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "title": "Home",
                    "sub_url": "Home",
                    "content_base64": "Y29udGVudA==",
                    "last_commit": {"sha": "a" * 40},
                },
            )
        return httpx.Response(200, json=_repo())

    client = _client(handler)
    try:
        result = client.bootstrap("org/repo", 17, "Home", "content", "bootstrap")
    finally:
        client.close()
    assert result["last_commit"]["sha"] == "a" * 40
    assert len([request for request in requests if request.method == "POST"]) == 1
    assert base64.b64decode(json.loads(requests[-1].content)["content_base64"]) == b"content"


def test_get_page_quotes_documented_path_and_bootstrap_rejects_lost_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/wiki/page/" in request.url.path:
            return httpx.Response(200, json={"title": "A page", "content": "x"})
        if request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "title": "Home",
                    "sub_url": "Home",
                    "content_base64": "Y29udGVudA==",
                },
            )
        return httpx.Response(200, json=_repo())

    client = _client(handler)
    try:
        assert client.get_page("org/repo", 17, "A page")["title"] == "A page"
        with pytest.raises(GiteaWikiError):
            client.bootstrap("org/repo", 17, "Home", "content", "message")
    finally:
        client.close()


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"http_git_disabled": True, "lfs_disabled": False},
        {"http_git_disabled": False, "lfs_disabled": True},
    ],
)
def test_native_target_settings_fail_closed(settings: dict) -> None:
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=settings)

    with _client(handler) as client:
        with pytest.raises(GiteaWikiError):
            client.verify_native_settings(lfs=True)
    assert calls == [("GET", "/api/v1/settings/repository")]
