from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from gitferry.gitea_provisioning import (
    GiteaProvisioningClient,
    ProvisioningTargetConflict,
    require_numeric_id,
    verify_organization,
    verify_team,
)


def test_gitea_per_unit_team_permission_allows_none_aggregate_permission() -> None:
    assert (
        verify_team(
            {
                "id": 2,
                "name": "readers",
                "permission": "none",
                "includes_all_repositories": False,
                "can_create_org_repo": False,
                "units_map": {
                    "repo.code": "read",
                    "repo.issues": "read",
                    "repo.releases": "read",
                },
            },
            "readers",
            "read",
        )
        == "2"
    )


def test_path_segments_are_validated_and_benign_segments_keep_exact_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": 7,
                "name": "readers.team",
                "permission": "read",
                "includes_all_repositories": False,
                "can_create_org_repo": False,
                "units_map": {
                    "repo.code": "read",
                    "repo.issues": "read",
                    "repo.releases": "read",
                },
            },
        )

    client = GiteaProvisioningClient(
        "https://target.invalid",
        username="technical-admin",
        password="synthetic",  # noqa: S106 - isolated MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    try:
        client.create_team("safe-org", "readers.team", "read")
        with pytest.raises(ProvisioningTargetConflict, match="organization"):
            client.create_team("../admin", "readers", "read")
        with pytest.raises(ProvisioningTargetConflict, match="username"):
            client.add_team_member("7", "reader?admin=true")
    finally:
        client.close()
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/api/v1/orgs/safe-org/teams"),
        ("GET", "/api/v1/teams/7"),
    ]
    assert json.loads(requests[0].content)["units_map"] == {
        "repo.code": "read",
        "repo.issues": "read",
        "repo.releases": "read",
    }


@pytest.mark.parametrize("identifier", [True, False, 0, -1, "0", "-1", "abc"])
def test_numeric_ids_reject_booleans_and_non_positive_values(identifier: Any) -> None:
    with pytest.raises(ProvisioningTargetConflict, match="stable numeric id"):
        require_numeric_id({"id": identifier}, "test")


@pytest.mark.parametrize("extra_permission", ["read", "write"])
def test_security_relevant_response_fields_are_required(extra_permission: str) -> None:
    with pytest.raises(ProvisioningTargetConflict):
        verify_organization({"id": 1, "username": "org", "visibility": "public"}, "org")
    with pytest.raises(ProvisioningTargetConflict):
        verify_team(
            {
                "id": 2,
                "name": "readers",
                "permission": "read",
                "includes_all_repositories": False,
                "can_create_org_repo": True,
                "units_map": {
                    "repo.code": "read",
                    "repo.issues": "read",
                    "repo.releases": "read",
                },
            },
            "readers",
            "read",
        )
    with pytest.raises(ProvisioningTargetConflict):
        verify_team(
            {
                "id": 2,
                "name": "readers",
                "permission": "read",
                "includes_all_repositories": False,
                "can_create_org_repo": False,
                "units_map": {
                    "repo.code": "read",
                    "repo.issues": "read",
                    "repo.releases": "read",
                    "repo.wiki": extra_permission,
                },
            },
            "readers",
            "read",
        )


def test_release_unit_permission_is_required() -> None:
    with pytest.raises(ProvisioningTargetConflict):
        verify_team(
            {
                "id": 2,
                "name": "readers",
                "permission": "read",
                "includes_all_repositories": False,
                "can_create_org_repo": False,
                "units_map": {"repo.code": "read", "repo.issues": "read"},
            },
            "readers",
            "read",
        )


def test_target_lookup_distinguishes_absence_and_unique_team_without_writes() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/orgs/missing"):
            return httpx.Response(404, json={"message": "not found"})
        if request.url.path.endswith("/repos/access-lab/selected-private"):
            return httpx.Response(
                200,
                json={
                    "id": 11,
                    "full_name": "access-lab/selected-private",
                    "private": True,
                },
            )
        if request.url.path.endswith("/orgs/access-lab/teams"):
            return httpx.Response(
                200,
                json=[
                    {"id": 21, "name": "readers"},
                    {"id": 22, "name": "writers"},
                ],
            )
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    client = GiteaProvisioningClient(
        "https://target.invalid",
        username="technical-admin",
        password="synthetic",  # noqa: S106 - isolated MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.find_organization("missing") is None
        assert client.find_repository("access-lab/selected-private")["id"] == 11
        assert client.find_team("access-lab", "writers")["id"] == 22
        assert client.find_team("access-lab", "absent") is None
    finally:
        client.close()
    assert all(method == "GET" for method, _ in requests)


def test_target_lookup_rejects_duplicate_team_names() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"id": 21, "name": "readers"}, {"id": 22, "name": "readers"}],
        )

    client = GiteaProvisioningClient(
        "https://target.invalid",
        username="technical-admin",
        password="synthetic",  # noqa: S106 - isolated MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProvisioningTargetConflict, match="not unique"):
            client.find_team("access-lab", "readers")
    finally:
        client.close()


def test_account_lookup_uses_the_documented_public_user_read_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/users/reader-b":
            return httpx.Response(200, json={"id": 51, "login": "reader-b", "is_admin": False})
        return httpx.Response(405, json={"message": "method not allowed"})

    client = GiteaProvisioningClient(
        "https://target.invalid",
        username="technical-admin",
        password="synthetic",  # noqa: S106 - isolated MockTransport credential
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.get_user("reader-b")["id"] == 51
    finally:
        client.close()
    assert [request.url.path for request in requests] == ["/api/v1/users/reader-b"]
