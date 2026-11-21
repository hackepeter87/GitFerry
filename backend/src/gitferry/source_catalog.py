"""Read-only, bounded source browsing through locally approved connection profiles."""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any

import httpx

from gitferry.connection_probe import ConnectionUnavailable, LocalReferences, probe_connection
from gitferry.connection_profiles import ProfileConflict, ProfileService
from gitferry.gitea_provisioning import (
    ProvisioningTargetConflict,
    _path_segment,
    _repository_segments,
)

PAGE_SIZE = 50
MAX_PAGE_BYTES = 2 * 1024 * 1024
PAGE_DEADLINE_SECONDS = 15.0


class _SourceReferences(LocalReferences):
    """Keep the check and catalog request on the same privately captured material."""

    def __init__(self, references: LocalReferences, credential_ref: str, ca_ref: str | None):
        self._credential_ref, self._ca_ref = credential_ref, ca_ref
        self._references = references
        self._token: str | None = None
        self._tls: tuple[ssl.SSLContext, str] | None = None

    def token(self, reference: str) -> str:
        if reference != self._credential_ref:
            raise ConnectionUnavailable("Source credential reference changed")
        if self._token is None:
            self._token = self._references.token(reference)
        return self._token

    def tls(self, reference: str | None) -> tuple[ssl.SSLContext, str]:
        if reference != self._ca_ref:
            raise ConnectionUnavailable("Source CA reference changed")
        if self._tls is None:
            self._tls = self._references.tls(reference)
        return self._tls


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response field")
        result[key] = value
    return result


async def _read_page(
    base_url: str,
    token: str,
    context: ssl.SSLContext,
    path: str,
    page: int,
    transport: httpx.AsyncBaseTransport | None,
) -> list[dict[str, Any]]:
    async def read() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": "token " + token, "Accept-Encoding": "identity"},
            verify=context,
            timeout=5,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            async with client.stream(
                "GET", path, params={"page": page, "limit": PAGE_SIZE}
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("encoded catalog response")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_PAGE_BYTES:
                        raise ValueError("catalog response exceeds bound")
                    data.extend(chunk)
                if token.encode() in data:
                    raise ValueError("credential reflected by remote")
                value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique)
                if not isinstance(value, list) or len(value) > PAGE_SIZE:
                    raise ValueError("invalid catalog page")
                if any(not isinstance(item, dict) for item in value):
                    raise ValueError("invalid catalog entry")
                return value

    return await asyncio.wait_for(read(), timeout=PAGE_DEADLINE_SECONDS)


def browse_source(
    service: ProfileService,
    key: str,
    *,
    expected_revision: int,
    kind: str,
    organization: str | None,
    page: int,
    references: LocalReferences | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Return one selection page, never claim it is a complete content inventory."""
    if type(expected_revision) is not int or expected_revision < 1:
        raise ProfileConflict("Source profile revision must be a positive integer")
    if type(page) is not int or not 1 <= page <= 10000:
        raise ProfileConflict("Source page must be an integer from 1 to 10000")
    if kind == "organizations" and organization is None:
        path = "/admin/orgs"
    elif kind == "repositories" and isinstance(organization, str):
        path = "/orgs/" + _path_segment(organization, "organization") + "/repos"
    else:
        raise ProfileConflict("Select organizations or repositories within one organization")
    profile = service.get(key)
    service._check_revision(profile, expected_revision)
    if not profile.locally_approved:
        raise ProfileConflict("Test and locally approve this connection before browsing")
    refs = _SourceReferences(
        references or LocalReferences(), profile.credential_ref, profile.ca_ref
    )
    evidence = probe_connection(
        service,
        key,
        expected_revision=expected_revision,
        references=refs,
        transport=transport,
    )
    if evidence["requires_approval"]:
        raise ProfileConflict("Source identity changed; review the connection evidence")
    token = refs.token(profile.credential_ref)
    context, ca_digest = refs.tls(profile.ca_ref)
    if ca_digest != evidence["observed_identity"]["ca_digest"]:
        raise ProfileConflict("Source CA changed after the connection check")
    try:
        values = asyncio.run(_read_page(profile.base_url, token, context, path, page, transport))
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in values:
            native_id = value.get("id")
            if type(native_id) is not int or native_id < 1 or str(native_id) in seen:
                raise ValueError("invalid or duplicate source identity")
            seen.add(str(native_id))
            if kind == "organizations":
                name = value.get("name")
                if not isinstance(name, str) or token in name:
                    raise ValueError("missing organization name")
                _path_segment(name, "organization")
                entries.append({"id": str(native_id), "name": name})
            else:
                repository = value.get("full_name")
                if not isinstance(repository, str) or token in repository:
                    raise ValueError("missing repository scope")
                owner, _ = _repository_segments(repository)
                if owner != organization or type(value.get("private")) is not bool:
                    raise ValueError("repository is outside selected organization")
                entries.append(
                    {"id": str(native_id), "repository": repository, "private": value["private"]}
                )
    except (TimeoutError, httpx.HTTPError, ValueError, RecursionError, ProvisioningTargetConflict):
        raise ConnectionUnavailable(
            "Source listing failed; verify source access and retry this page. "
            "No complete inventory was recorded."
        ) from None
    current = service.get(key)
    service._check_revision(current, expected_revision)
    if not current.locally_approved:
        raise ProfileConflict("Source approval changed while browsing")
    return {
        "instance_key": key,
        "profile_revision": expected_revision,
        "evidence_digest": evidence["evidence_digest"],
        "kind": kind,
        "organization": organization,
        "page": page,
        "entries": entries,
        "next_page": page + 1 if len(entries) == PAGE_SIZE else None,
        "inventory_complete": False,
    }
