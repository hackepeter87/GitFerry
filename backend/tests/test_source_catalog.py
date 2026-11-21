from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from gitferry.connection_probe import ConnectionUnavailable, approve_connection, probe_connection
from gitferry.connection_profiles import ProfileConflict
from gitferry.source_catalog import browse_source
from test_connection_probe import setup


def prepared(tmp_path: Path, listing: httpx.Response):
    service, references = setup(tmp_path)
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "token synthetic-test-token"
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "1.27.2"})
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "login": "technical-admin"})
        return listing

    transport = httpx.MockTransport(handle)
    evidence = probe_connection(
        service, "a", expected_revision=1, references=references, transport=transport
    )
    approve_connection(
        service, "a", expected_revision=1, evidence_digest=evidence["evidence_digest"]
    )
    return service, references, transport, calls


def test_source_pages_keep_ids_exact_and_do_not_claim_content_completeness(tmp_path: Path) -> None:
    native_id = 9007199254740993
    service, refs, transport, calls = prepared(
        tmp_path, httpx.Response(200, json=[{"id": native_id, "name": "synthetic-org"}])
    )
    result = browse_source(
        service,
        "a",
        expected_revision=1,
        kind="organizations",
        organization=None,
        page=2,
        references=refs,
        transport=transport,
    )
    assert result["entries"] == [{"id": str(native_id), "name": "synthetic-org"}]
    assert result["inventory_complete"] is False and result["next_page"] is None
    assert calls[-1].url.path == "/subpath/api/v1/admin/orgs"
    assert dict(calls[-1].url.params) == {"page": "2", "limit": "50"}
    with service.store.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


def test_source_repository_page_is_scoped_to_selected_organization(tmp_path: Path) -> None:
    service, refs, transport, calls = prepared(
        tmp_path,
        httpx.Response(
            200,
            json=[
                {
                    "id": 7,
                    "full_name": "org/private",
                    "private": True,
                    "clone_url": "https://untrusted.invalid",
                }
            ],
        ),
    )
    result = browse_source(
        service,
        "a",
        expected_revision=1,
        kind="repositories",
        organization="org",
        page=1,
        references=refs,
        transport=transport,
    )
    assert calls[-1].url.path.endswith("/orgs/org/repos")
    assert result["entries"] == [{"id": "7", "repository": "org/private", "private": True}]


@pytest.mark.parametrize(
    "listing",
    [
        httpx.Response(302, headers={"location": "https://untrusted.invalid"}),
        httpx.Response(403, text="synthetic-test-token"),
        httpx.Response(200, content=b'[{"id":1,"id":2,"name":"x"}]'),
        httpx.Response(200, json=[{"id": True, "name": "x"}]),
        httpx.Response(200, json=[{"id": 1, "name": "../x"}]),
        httpx.Response(200, json=[{"id": 1, "name": "synthetic-test-token"}]),
        httpx.Response(200, json=[{"id": 1, "name": "x"}] * 2),
    ],
)
def test_failed_or_untrusted_source_page_has_no_partial_result(tmp_path: Path, listing) -> None:
    service, refs, transport, calls = prepared(tmp_path, listing)
    with pytest.raises(ConnectionUnavailable) as error:
        browse_source(
            service,
            "a",
            expected_revision=1,
            kind="organizations",
            organization=None,
            page=1,
            references=refs,
            transport=transport,
        )
    assert "synthetic-test-token" not in str(error.value)
    assert all(request.url.host == "forge.invalid" for request in calls)


def test_source_browsing_requires_local_approval_before_any_request(tmp_path: Path) -> None:
    service, refs = setup(tmp_path)
    with pytest.raises(ProfileConflict, match="approve"):
        browse_source(
            service,
            "a",
            expected_revision=1,
            kind="organizations",
            organization=None,
            page=1,
            references=refs,
        )


def test_catalog_page_size_and_total_deadline_are_bounded(tmp_path: Path, monkeypatch) -> None:
    from gitferry import source_catalog

    service, refs, transport, _ = prepared(tmp_path, httpx.Response(200, content=b" " * 100))
    monkeypatch.setattr(source_catalog, "MAX_PAGE_BYTES", 32)
    with pytest.raises(ConnectionUnavailable):
        browse_source(
            service,
            "a",
            expected_revision=1,
            kind="organizations",
            organization=None,
            page=1,
            references=refs,
            transport=transport,
        )

    class SlowBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"["
            await asyncio.sleep(1)
            yield b"]"

        async def aclose(self):
            self.closed = True

    body = SlowBody()
    other = tmp_path / "slow"
    other.mkdir()
    service, refs, transport, _ = prepared(other, httpx.Response(200, stream=body))
    monkeypatch.setattr(source_catalog, "PAGE_DEADLINE_SECONDS", 0.02)
    with pytest.raises(ConnectionUnavailable):
        browse_source(
            service,
            "a",
            expected_revision=1,
            kind="organizations",
            organization=None,
            page=1,
            references=refs,
            transport=transport,
        )
    assert body.closed


def test_source_cli_uses_shared_service_and_preserves_strings(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from gitferry import profile_cli
    from gitferry.cli import main

    service, _ = setup(tmp_path)
    calls = []

    def browse(profiles, key, **values):
        calls.append((str(profiles.store.path), key, values))
        return {"entries": [{"id": "9007199254740993", "name": "synthetic"}]}

    monkeypatch.setattr(profile_cli, "browse_source", browse)
    assert (
        main(
            [
                "connections",
                "--database",
                str(service.store.path),
                "browse",
                "a",
                "--expected-revision",
                "1",
                "--kind",
                "organizations",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["entries"][0]["id"] == "9007199254740993"
    assert calls == [
        (
            str(service.store.path),
            "a",
            {
                "expected_revision": 1,
                "kind": "organizations",
                "organization": None,
                "page": 1,
            },
        )
    ]
