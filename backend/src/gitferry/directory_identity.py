"""Local directory evidence for an explicitly selected native Gitea account.

The selected directory describes the target identity, independently of the
source selector. Reading this evidence never approves a binding or grants rights.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from gitferry.connection_probe import ConnectionUnavailable, LocalReferences
from gitferry.directory_claims import (
    DirectoryClaimsConfig,
    DirectoryClaimsConflict,
    DirectoryClaimsUnavailable,
    observe_claim,
)
from gitferry.directory_settings import DirectorySettingsService
from gitferry.state_codec import canonical_json
from gitferry.state_store import BindingConflict, StateStore


class DirectorySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    source_revision: Annotated[int, Field(ge=1)]
    native_source_id: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")] | None = None
    login: Annotated[str, Field(min_length=1, max_length=128)]


def evidence_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def observe_target_directory(
    state: StateStore,
    instance_key: str,
    selection: DirectorySelection,
    account_evidence: dict[str, Any],
    references: LocalReferences | None = None,
) -> dict[str, Any]:
    native_source_id = account_evidence.get("source_id")
    if (
        not isinstance(native_source_id, str)
        or not native_source_id.isdecimal()
        or int(native_source_id) <= 0
    ):
        raise DirectoryClaimsConflict("native account is not backed by a directory source")
    if selection.native_source_id is None:
        selection = selection.model_copy(update={"native_source_id": native_source_id})
    if (
        account_evidence.get("source_id") != selection.native_source_id
        or account_evidence.get("login_name") != selection.login
    ):
        raise DirectoryClaimsConflict("native account does not match the selected directory login")
    settings = DirectorySettingsService(state)
    record = settings.get(instance_key, selection.source_name)
    if record["revision"] != selection.source_revision:
        raise DirectoryClaimsConflict("directory configuration revision changed")
    source = record["source"]
    lookup = source.get("claimLookup")
    if not isinstance(lookup, dict) or not source.get("caSecretRef"):
        raise DirectoryClaimsConflict("directory claim lookup and explicit CA are required")
    references = references or LocalReferences()
    try:
        ca_bytes = references._read(references.cas, source["caSecretRef"], secret=False)
        ca = ca_bytes.decode("ascii")
        ca_sha256 = hashlib.sha256(ca_bytes).hexdigest()
        password = None
        if lookup.get("bindCredentialRef"):
            password = (
                references._read(references.secrets, lookup["bindCredentialRef"], secret=True)
                .decode("utf-8")
                .rstrip("\r\n")
            )
            if not password or "\x00" in password:
                raise ConnectionUnavailable("directory credential is invalid")
    except (UnicodeError, ConnectionUnavailable):
        raise DirectoryClaimsUnavailable("local directory references are unavailable") from None
    config = DirectoryClaimsConfig(
        host=source["host"],
        port=source.get("port", 636 if source["type"] == "ldaps" else 389),
        security_protocol="ldaps" if source["type"] == "ldaps" else "starttls",
        ca_pem=ca,
        search_base=lookup["searchBase"],
        user_filter=source.get("userFilter", f"({lookup['loginAttribute']}=%s)"),
        login_attribute=lookup["loginAttribute"],
        external_id_attribute=lookup["externalIdAttribute"],
        email_attribute=lookup.get("emailAttribute"),
        bind_dn=lookup.get("bindDn"),
        group_base=lookup.get("groupSearchBase"),
        group_member_attribute=lookup.get("groupMemberAttribute", "member"),
    )
    claim = asdict(observe_claim(config, record["realm_key"], selection.login, password))
    if password and password in canonical_json(claim):
        raise DirectoryClaimsUnavailable("directory response reflected a credential")
    # JSON representation is the persistence/HTTP contract (groups are arrays).
    claim = json.loads(canonical_json(claim))
    claim["realm_key"] = claim.pop("directory_realm")
    latest = settings.get(instance_key, selection.source_name)
    if (
        latest["revision"] != record["revision"]
        or latest["source_digest"] != record["source_digest"]
    ):
        raise DirectoryClaimsConflict("directory configuration changed during observation")
    return {
        "selection": selection.model_dump(),
        "source_digest": record["source_digest"],
        "ca_sha256": ca_sha256,
        "claim": claim,
    }


def same_directory_subject(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Immutable subject fields; trust/config rotation needs a separate explicit review."""
    fields = ("realm_key", "login", "external_id", "dn", "ou_path", "groups", "external_id_kind")
    return all(
        expected["selection"].get(key) == observed["selection"].get(key)
        for key in ("source_name", "native_source_id", "login")
    ) and all(expected["claim"].get(key) == observed["claim"].get(key) for key in fields)


def same_directory_identity(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    """Require the reviewed subject and trust facts; email remains informational."""
    return bool(
        same_directory_subject(expected, observed)
        and expected.get("selection") == observed.get("selection")
        and expected.get("source_digest") == observed.get("source_digest")
        and expected.get("ca_sha256") == observed.get("ca_sha256")
    )


def require_directory_revision(
    connection: sqlite3.Connection, instance_key: str, evidence: dict[str, Any]
) -> None:
    """Check the reviewed local configuration inside the approval transaction."""
    selection = evidence["selection"]
    row = connection.execute(
        "SELECT config_revision,source_sha256 FROM directory_configurations "
        "WHERE instance_key=? AND source_name=?",
        (instance_key, selection["source_name"]),
    ).fetchone()
    if (
        row is None
        or row["config_revision"] != selection["source_revision"]
        or row["source_sha256"] != evidence["source_digest"]
    ):
        raise BindingConflict("directory configuration changed before local approval")
