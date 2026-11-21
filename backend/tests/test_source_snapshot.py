from __future__ import annotations

import ssl

import httpx
import pytest
from gitferry.source_snapshot import SourceSnapshotError, capture_source


@pytest.mark.parametrize("aggregate_permission", ["none", "write"])
def test_capture_source_reads_only_selected_repository_and_reports_team_limits(
    aggregate_permission: str,
) -> None:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.method == "GET"
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                json={"id": 7, "full_name": "acme/one", "owner": {"id": 3, "login": "acme"}},
            )
        if request.url.path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme"})
        if request.url.path.endswith("/teams/10"):
            return httpx.Response(
                200,
                json={
                    "id": 10,
                    "name": "writers",
                    "permission": aggregate_permission,
                    "units_map": {"repo.code": "write", "repo.issues": "write"},
                },
            )
        if request.url.path.endswith("/teams/11"):
            return httpx.Response(
                200,
                json={
                    "id": 11,
                    "name": "owners",
                    "permission": "admin",
                    "units_map": {"repo.code": "admin"},
                },
            )
        if request.url.path.endswith("/repos/acme/one/teams"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 10,
                        "name": "writers",
                        "permission": "write",
                        "units_map": {"repo.code": "write", "repo.issues": "write"},
                    },
                    {
                        "id": 11,
                        "name": "owners",
                        "permission": "admin",
                        "units_map": {"repo.code": "admin", "repo.issues": "admin"},
                    },
                ],
            )
        raise AssertionError(request.url)

    result = capture_source(
        "https://source.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        [{"repository": "acme/one", "global_id": "7"}],
        include_metadata=False,
        include_members=False,
        transport=httpx.MockTransport(handle),
    )
    assert [item["repository"] for item in result["repositories"]] == ["acme/one"]
    assert result["repositories"][0]["teams"] == [
        {"global_id": "10", "name": "writers", "permission": "write", "members": []}
    ]
    assert any("administrative owner teams" in item for item in result["limitations"])
    assert any("explicitly excluded" in item for item in result["limitations"])
    assert all("acme/other" not in call for call in calls)


def test_capture_source_rejects_pull_requests_and_credential_reflection() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                json={"id": 7, "full_name": "acme/one", "owner": {"id": 3, "login": "acme"}},
            )
        if request.url.path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme"})
        if request.url.path.endswith("/repos/acme/one/teams"):
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/repos/acme/one/issues"):
            return httpx.Response(200, json=[{"id": 8, "number": 1, "pull_request": {}}])
        raise AssertionError(request.url)

    with pytest.raises(SourceSnapshotError, match="pull request"):
        capture_source(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            include_metadata=True,
            include_members=False,
            transport=httpx.MockTransport(handle),
        )


def test_capture_source_rejects_owner_mismatch_and_failed_later_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                json={"id": 7, "full_name": "acme/one", "owner": {"id": 99, "login": "acme"}},
            )
        if request.url.path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme"})
        raise AssertionError(request.url)

    with pytest.raises(SourceSnapshotError, match="owner"):
        capture_source(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            include_metadata=False,
            include_members=False,
            transport=httpx.MockTransport(handle),
        )

    def paged(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                json={"id": 7, "full_name": "acme/one", "owner": {"id": 3, "login": "acme"}},
            )
        if request.url.path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme"})
        if request.url.path.endswith("/repos/acme/one/teams"):
            if request.url.params.get("page") == "1":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": n,
                            "name": "t",
                            "permission": "read",
                            "units_map": {"repo.code": "read", "repo.issues": "read"},
                        }
                        for n in range(1, 51)
                    ],
                )
            return httpx.Response(503)
        raise AssertionError(request.url)

    with pytest.raises(SourceSnapshotError):
        capture_source(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            include_metadata=False,
            include_members=False,
            transport=httpx.MockTransport(paged),
        )


@pytest.mark.parametrize("mode", ["oversized", "repeated"])
def test_capture_source_rejects_oversized_and_repeated_pages(mode: str) -> None:
    pages: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200, json={"id": 7, "full_name": "acme/one", "owner": {"id": 3, "login": "acme"}}
            )
        if request.url.path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme"})
        assert request.url.path.endswith("/repos/acme/one/teams")
        pages.append(int(request.url.params["page"]))
        # A full first page forces another request, which repeats the same IDs.
        count = 51 if mode == "oversized" else 50
        return httpx.Response(200, json=[{"id": value} for value in range(1, count + 1)])

    message = "page exceeds" if mode == "oversized" else "duplicate native ids"
    with pytest.raises(SourceSnapshotError, match=message):
        capture_source(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            include_metadata=False,
            include_members=False,
            transport=httpx.MockTransport(handle),
        )
    assert pages == ([1] if mode == "oversized" else [1, 2])


def test_capture_source_rejects_escaped_credential_in_json_key() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                content=b'{"id":7,"full_name":"acme/one","owner":{"id":3,"login":"acme","\\u0073ynthetic-token":"x"}}',
            )
        raise AssertionError(request.url)

    with pytest.raises(SourceSnapshotError, match="reflected"):
        capture_source(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            include_metadata=False,
            include_members=False,
            transport=httpx.MockTransport(handle),
        )


@pytest.mark.parametrize("mode", ["oversized", "compressed", "duplicate"])
def test_repository_identity_uses_bounded_response_reader(mode: str) -> None:
    from gitferry.source_snapshot import read_repository_identity

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        if mode == "oversized":
            return httpx.Response(200, content=b" " * (2 * 1024 * 1024 + 1))
        if mode == "compressed":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"")
        return httpx.Response(200, content=b'{"id":7,"id":8}')

    with pytest.raises(SourceSnapshotError):
        read_repository_identity(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            "acme/one",
            transport=httpx.MockTransport(handle),
        )
