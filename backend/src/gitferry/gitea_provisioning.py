"""Narrow Gitea adapter for bounded organization/team/repository provisioning."""

from __future__ import annotations

import re
import ssl
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx


class ProvisioningTargetConflict(RuntimeError):
    """The target response does not match the explicitly requested structure."""


class GiteaProvisioningClient:
    """Administrative structure adapter; credentials never enter GitFerry state."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str,
        password: str | None = None,
        token: str | None = None,
        verify: bool | str | ssl.SSLContext = True,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if (password is None) == (token is None):
            raise ValueError("supply one local credential mechanism")
        self.authenticated_login = username
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            auth=(username, password) if password is not None else None,
            headers={"Authorization": "token " + token} if token is not None else None,
            verify=verify,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GiteaProvisioningClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def _object(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        value = self._request(method, path, **kwargs).json()
        if not isinstance(value, dict):
            raise ProvisioningTargetConflict("Gitea response is not an object")
        return value

    def _optional_object(self, path: str) -> dict[str, Any] | None:
        response = self._client.get(path)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ProvisioningTargetConflict("Gitea response is not an object")
        return value

    def create_organization(self, username: str, visibility: str) -> dict[str, Any]:
        _path_segment(username, "organization")
        return self._object("POST", "/orgs", json={"username": username, "visibility": visibility})

    def find_organization(self, username: str) -> dict[str, Any] | None:
        return self._optional_object(f"/orgs/{_path_segment(username, 'organization')}")

    def create_team(self, organization: str, name: str, permission: str) -> dict[str, Any]:
        organization_path = _path_segment(organization, "organization")
        created = self._object(
            "POST",
            f"/orgs/{organization_path}/teams",
            json={
                "name": name,
                "permission": permission,
                "can_create_org_repo": False,
                "includes_all_repositories": False,
                "units_map": {"repo.code": permission, "repo.issues": permission},
            },
        )
        team_id = _numeric_path_segment(require_numeric_id(created, "team"), "team id")
        return self._object("GET", f"/teams/{team_id}")

    def find_team(self, organization: str, name: str) -> dict[str, Any] | None:
        organization_path = _path_segment(organization, "organization")
        _path_segment(name, "team")
        page = 1
        matches: list[dict[str, Any]] = []
        while True:
            value = self._request(
                "GET", f"/orgs/{organization_path}/teams", params={"page": page, "limit": 50}
            ).json()
            if not isinstance(value, list):
                raise ProvisioningTargetConflict("Gitea team list is not an array")
            matches.extend(
                item for item in value if isinstance(item, dict) and item.get("name") == name
            )
            if len(value) < 50:
                break
            page += 1
        if len(matches) > 1:
            raise ProvisioningTargetConflict("target team name is not unique")
        return matches[0] if matches else None

    def create_repository(self, organization: str, name: str, private: bool) -> dict[str, Any]:
        organization_path = _path_segment(organization, "organization")
        return self._object(
            "POST",
            f"/orgs/{organization_path}/repos",
            json={"name": name, "private": private, "auto_init": False},
        )

    def find_repository(self, repository: str) -> dict[str, Any] | None:
        owner, name = _repository_segments(repository)
        return self._optional_object(f"/repos/{owner}/{name}")

    def assign_team_repository(self, team_id: str, repository: str) -> dict[str, Any]:
        team_path = _numeric_path_segment(team_id, "team id")
        owner, name = _repository_segments(repository)
        path = f"/teams/{team_path}/repos/{owner}/{name}"
        self._request("PUT", path)
        return self._object("GET", path)

    def add_team_member(self, team_id: str, username: str) -> dict[str, Any]:
        team_path = _numeric_path_segment(team_id, "team id")
        username_path = _path_segment(username, "username")
        self._request("PUT", f"/teams/{team_path}/members/{username_path}")
        page = 1
        matches: list[dict[str, Any]] = []
        while True:
            value = self._request(
                "GET", f"/teams/{team_path}/members", params={"page": page, "limit": 50}
            ).json()
            if not isinstance(value, list):
                raise ProvisioningTargetConflict("Gitea team member list is not an array")
            matches.extend(
                item for item in value if isinstance(item, dict) and item.get("login") == username
            )
            if len(value) < 50:
                break
            page += 1
        if len(matches) != 1:
            raise ProvisioningTargetConflict(
                "mapped account is not uniquely present in the expected team"
            )
        return matches[0]

    def get_user(self, username: str) -> dict[str, Any]:
        return self._object("GET", f"/users/{_path_segment(username, 'username')}")

    def get_authenticated_user(self) -> dict[str, Any]:
        return self._object("GET", "/user")


_GITEA_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


def _path_segment(value: str, label: str) -> str:
    if not _GITEA_PATH_SEGMENT.fullmatch(value):
        raise ProvisioningTargetConflict(f"invalid Gitea {label}")
    return quote(value, safe="")


def _numeric_path_segment(value: str, label: str) -> str:
    if not value.isdecimal() or int(value) <= 0:
        raise ProvisioningTargetConflict(f"invalid Gitea {label}")
    return quote(value, safe="")


def _repository_segments(repository: str) -> tuple[str, str]:
    owner, separator, name = repository.partition("/")
    if separator != "/" or "/" in name:
        raise ProvisioningTargetConflict("repository must have exactly one owner separator")
    return _path_segment(owner, "repository owner"), _path_segment(name, "repository name")


def require_numeric_id(value: Mapping[str, Any], kind: str) -> str:
    identifier = value.get("id")
    valid_integer = (
        isinstance(identifier, int) and not isinstance(identifier, bool) and identifier > 0
    )
    valid_string = isinstance(identifier, str) and identifier.isdecimal() and int(identifier) > 0
    if not valid_integer and not valid_string:
        raise ProvisioningTargetConflict(f"{kind} response has no stable numeric id")
    return str(identifier)


def verify_organization(value: Mapping[str, Any], username: str) -> str:
    if value.get("username") != username or value.get("visibility") != "private":
        raise ProvisioningTargetConflict("created organization has the wrong username")
    return require_numeric_id(value, "organization")


def verify_team(value: Mapping[str, Any], name: str, permission: str) -> str:
    units = value.get("units_map")
    unit_permissions_safe = isinstance(units, Mapping) and all(
        unit in {"repo.code", "repo.issues"} or unit_permission == "none"
        for unit, unit_permission in units.items()
    )
    if (
        value.get("name") != name
        or value.get("permission") not in (permission, "none")
        or not isinstance(units, Mapping)
        or units.get("repo.code") != permission
        or units.get("repo.issues") != permission
        or value.get("includes_all_repositories") is not False
        or value.get("can_create_org_repo") is not False
        or not unit_permissions_safe
    ):
        observed = {
            "name": value.get("name"),
            "permission": value.get("permission"),
            "includes_all_repositories": value.get("includes_all_repositories"),
            "can_create_org_repo": value.get("can_create_org_repo"),
            "units_map": dict(units) if isinstance(units, Mapping) else None,
        }
        raise ProvisioningTargetConflict(
            f"created team does not match the requested permissions: {observed}"
        )
    return require_numeric_id(value, "team")


def verify_repository(value: Mapping[str, Any], repository: str, private: bool) -> str:
    if value.get("full_name") != repository or value.get("private") is not private:
        raise ProvisioningTargetConflict("created repository does not match the requested target")
    return require_numeric_id(value, "repository")


def verify_account(value: Mapping[str, Any], username: str, account_id: int) -> None:
    if (
        value.get("login") != username
        or value.get("id") != account_id
        or value.get("is_admin") is not False
    ):
        raise ProvisioningTargetConflict(
            "mapped team member is absent, redirected, or has administrative privileges"
        )


def verify_technical_administrator(value: Mapping[str, Any], username: str) -> int:
    identifier = require_numeric_id(value, "authenticated administrator")
    if value.get("login") != username or value.get("is_admin") is not True:
        raise ProvisioningTargetConflict(
            "provisioning client is not the authenticated technical administrator"
        )
    return int(identifier)
