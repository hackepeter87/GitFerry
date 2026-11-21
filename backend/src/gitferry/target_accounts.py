"""Read-only native account verification shared by package and identity administration."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from gitferry.connection_probe import (
    ConnectionUnavailable,
    LocalReferences,
    _json,
    probe_connection,
)
from gitferry.connection_profiles import ProfileService
from gitferry.gitea_provisioning import verify_account
from gitferry.package_admission import PackageAdmissionConflict, _authority
from gitferry.source_catalog import _SourceReferences
from gitferry.state_codec import canonical_json
from gitferry.state_store import StateStore


def check_target_account(
    state: StateStore,
    *,
    target_instance_key: str,
    profile_revision: int,
    target_login: str,
    credential_ref: str | None = None,
    references: LocalReferences | None = None,
) -> dict[str, Any]:
    references = references or LocalReferences()
    with state.read_connection() as connection:
        authority = _authority(connection, target_instance_key, profile_revision)
    profiles = ProfileService(state)
    profile = profiles.get(target_instance_key)
    refs = _SourceReferences(references, profile.credential_ref, profile.ca_ref)
    evidence = probe_connection(
        profiles,
        profile.instance_key,
        expected_revision=profile_revision,
        references=refs,
    )
    if evidence["requires_approval"]:
        raise PackageAdmissionConflict("target connection changed; local approval required")
    token = (
        references.token(credential_ref) if credential_ref else refs.token(profile.credential_ref)
    )
    context, ca_digest = refs.tls(profile.ca_ref)
    if ca_digest != evidence["observed_identity"]["ca_digest"]:
        raise PackageAdmissionConflict("target CA changed")

    async def read() -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=profile.base_url + "/api/v1/",
            verify=context,
            headers={"Authorization": "token " + token, "Accept-Encoding": "identity"},
            follow_redirects=False,
            trust_env=False,
            timeout=10,
        ) as client:
            path = "user" if credential_ref else "users/" + quote(target_login, safe="")
            account, fingerprint = await _json(client, path)
            if fingerprint != evidence["observed_identity"]["certificate_sha256"]:
                raise ConnectionUnavailable("target TLS identity changed")
            if token in canonical_json(account):
                raise ConnectionUnavailable("remote response reflected a credential")
            return account

    async def bounded() -> dict[str, Any]:
        return await asyncio.wait_for(read(), timeout=15)

    try:
        account = asyncio.run(bounded())
    except (TimeoutError, httpx.HTTPError) as error:
        raise ConnectionUnavailable("target account check failed") from error
    identifier = account.get("id")
    if type(identifier) is not int or not 0 < identifier <= 2**63 - 1:
        raise PackageAdmissionConflict("invalid native account ID")
    verify_account(account, target_login, identifier)
    with state.read_connection() as connection:
        if _authority(connection, target_instance_key, profile_revision) != authority:
            raise PackageAdmissionConflict("target approval changed during account check")
    return {
        "authority_digest": authority,
        "target_login": target_login,
        "target_account_id": str(identifier),
        "credential_checked": credential_ref is not None,
    }
