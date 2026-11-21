"""Local administration of directory authentication-source settings.

This module stores intent for a target-local bootstrap.  It deliberately has no
remote client, secret resolver, or command execution path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from gitferry.blueprints import BlueprintValidationError, validate_document
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.directory_bootstrap import DirectoryBootstrapError, parse_ldap_simple_source
from gitferry.state_store import StateStore

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_WORDS = ("password", "token", "secret", "private_key", "authorization")


class DirectorySettingsError(ValueError):
    """A directory setting is invalid or cannot be safely applied locally."""


class DirectorySettingsConflict(RuntimeError):
    """A setting edit conflicts with the local revision or identity."""


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _reject_raw_secrets(value: Any, path: str = "source") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(word in normalized for word in _SECRET_WORDS) and not normalized.endswith(
                ("_ref", "ref")
            ):
                raise DirectorySettingsError(f"raw secret field is not allowed: {path}.{key}")
            _reject_raw_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_secrets(child, f"{path}[{index}]")


def _metadata_name(source_name: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", source_name.lower()).strip("-")
    return (value or "directory-settings")[:63]


class DirectorySettingsService:
    """Persist and export target-local directory source configuration."""

    def __init__(self, store: StateStore, profiles: ProfileService | None = None):
        self.store = store
        self.profiles = profiles or ProfileService(store)

    def _profile(self, instance_key: str) -> ConnectionProfile:
        try:
            profile = self.profiles.get(instance_key)
        except Exception as exc:
            raise DirectorySettingsConflict("target profile does not exist") from exc
        if not profile.enabled or not profile.locally_approved:
            raise DirectorySettingsConflict("target profile must be enabled and locally approved")
        return profile

    @staticmethod
    def _validate_source(source: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(source, Mapping):
            raise DirectorySettingsError("source must be a mapping")
        result = copy.deepcopy(dict(source))
        _reject_raw_secrets(result)
        name = result.get("name")
        realm = result.get("directoryRealm")
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise DirectorySettingsError("authentication source name is invalid")
        if not isinstance(realm, str) or not realm.strip():
            raise DirectorySettingsError("directoryRealm is required")
        return result

    @staticmethod
    def _validate_blueprint_source(profile: Any, source: Mapping[str, Any]) -> None:
        gitea: dict[str, Any] = {
            "baseUrl": profile.base_url,
            "credentialSecretRef": profile.credential_ref,
            "expectedVersion": "1.27.2",
        }
        if profile.ca_ref:
            gitea["caSecretRef"] = profile.ca_ref
        document = {
            "apiVersion": "gitferry.io/v1",
            "kind": "InstanceBlueprint",
            "metadata": {"name": _metadata_name(str(source["name"]))},
            "spec": {"gitea": gitea, "authenticationSources": [dict(source)]},
        }
        try:
            validate_document(document)
        except BlueprintValidationError as exc:
            raise DirectorySettingsError(
                "source does not satisfy InstanceBlueprint validation"
            ) from exc

    def _record(self, row: sqlite3.Row) -> dict[str, Any]:
        source = json.loads(row["source_json"])
        status, prerequisites = self._readiness(source)
        return {
            "instance_key": row["instance_key"],
            "source_name": row["source_name"],
            "realm_key": row["directory_realm"],
            "source": source,
            "revision": row["config_revision"],
            "source_digest": row["source_sha256"],
            "profile_revision": row["profile_revision"],
            "status": status,
            "prerequisites": prerequisites,
            "updated_at": row["occurred_at"],
        }

    @staticmethod
    def _readiness(source: Mapping[str, Any]) -> tuple[str, list[str]]:
        if source.get("mode") != "simple":
            return (
                "unsupported-automation",
                ["Add this BindDN source in Gitea's authenticated admin UI over HTTPS."],
            )
        try:
            parse_ldap_simple_source(source, "1.27.2")
        except DirectoryBootstrapError as exc:
            raise DirectorySettingsError("simple source prerequisites are invalid") from exc
        return "ready-for-target-local-bootstrap", [
            "target-local Gitea server access",
            "TLS with the configured CA",
            "native Gitea login qualification",
        ]

    def save(
        self,
        instance_key: str,
        source: dict[str, Any],
        expected_revision: int | None,
        actor: str = "local-cli",
    ) -> dict[str, Any]:
        profile = self._profile(instance_key)
        clean = self._validate_source(source)
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise DirectorySettingsError("expected_revision must be a positive integer or None")
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or len(actor) > 120
            or any(ord(char) < 32 for char in actor)
        ):
            raise DirectorySettingsError("actor must be bounded non-empty text")
        self._validate_blueprint_source(profile, clean)
        try:
            self.store.get_realm(str(clean["directoryRealm"]))
        except Exception as exc:
            raise DirectorySettingsConflict("directoryRealm is not registered locally") from exc
        self._readiness(clean)
        payload = _canonical(clean)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM directory_configurations WHERE instance_key=? AND source_name=?",
                (instance_key, clean["name"]),
            ).fetchone()
            if row is None:
                if expected_revision is not None:
                    raise DirectorySettingsConflict("configuration does not exist")
                identity = f"{instance_key}:{clean['name']}"
                configuration_id = "directory-" + hashlib.sha256(identity.encode()).hexdigest()
                try:
                    connection.execute(
                        "INSERT INTO directory_configurations VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            configuration_id,
                            instance_key,
                            clean["name"],
                            clean["directoryRealm"],
                            payload,
                            digest,
                            profile.revision,
                            1,
                            actor,
                            now,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO directory_configuration_events("
                        "configuration_id,config_revision,source_sha256,actor,occurred_at) "
                        "VALUES (?,?,?,?,?)",
                        (configuration_id, 1, digest, actor, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DirectorySettingsConflict(
                        "configuration identity already exists"
                    ) from exc
                row = connection.execute(
                    "SELECT * FROM directory_configurations WHERE configuration_id=?",
                    (configuration_id,),
                ).fetchone()
            else:
                current = int(row["config_revision"])
                if expected_revision != current:
                    raise DirectorySettingsConflict("configuration changed; reload before editing")
                if row["directory_realm"] != clean["directoryRealm"]:
                    raise DirectorySettingsConflict("source identity cannot be redirected")
                if row["source_sha256"] != digest or row["profile_revision"] != profile.revision:
                    revision = current + 1
                    connection.execute(
                        "UPDATE directory_configurations SET directory_realm=?,source_json=?,"
                        "source_sha256=?,profile_revision=?,config_revision=?,actor=?,"
                        "occurred_at=? WHERE configuration_id=?",
                        (
                            clean["directoryRealm"],
                            payload,
                            digest,
                            profile.revision,
                            revision,
                            actor,
                            now,
                            row["configuration_id"],
                        ),
                    )
                    connection.execute(
                        "INSERT INTO directory_configuration_events("
                        "configuration_id,config_revision,source_sha256,actor,occurred_at) "
                        "VALUES (?,?,?,?,?)",
                        (row["configuration_id"], revision, digest, actor, now),
                    )
                row = connection.execute(
                    "SELECT * FROM directory_configurations WHERE configuration_id=?",
                    (row["configuration_id"],),
                ).fetchone()
        return self._record(row)

    def list(self, instance_key: str | None = None) -> list[dict[str, Any]]:
        with self.store.read_connection() as connection:
            if instance_key is None:
                rows = connection.execute(
                    "SELECT * FROM directory_configurations ORDER BY instance_key, source_name"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM directory_configurations WHERE instance_key=? "
                    "ORDER BY source_name",
                    (instance_key,),
                ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, instance_key: str, source_name: str) -> dict[str, Any]:
        with self.store.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM directory_configurations WHERE instance_key=? AND source_name=?",
                (instance_key, source_name),
            ).fetchone()
        if row is None:
            raise DirectorySettingsConflict("directory configuration does not exist")
        return self._record(row)

    def export(self, instance_key: str, source_name: str) -> dict[str, Any]:
        profile = self._profile(instance_key)
        with self.store.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM directory_configurations WHERE instance_key=? AND source_name=?",
                (instance_key, source_name),
            ).fetchone()
        if row is None:
            raise DirectorySettingsConflict("directory configuration does not exist")
        sources = [json.loads(row["source_json"])]
        gitea: dict[str, Any] = {
            "baseUrl": profile.base_url,
            "credentialSecretRef": profile.credential_ref,
        }
        if profile.ca_ref:
            gitea["caSecretRef"] = profile.ca_ref
        document = {
            "apiVersion": "gitferry.io/v1",
            "kind": "InstanceBlueprint",
            "metadata": {"name": _metadata_name(instance_key)},
            "spec": {"gitea": gitea, "authenticationSources": sources},
        }
        validate_document(document)
        return document

    def import_blueprint(
        self,
        instance_key: str,
        document: Mapping[str, Any],
        expected_revision: int | None = None,
        actor: str = "local-cli",
    ) -> dict[str, Any]:
        profile = self._profile(instance_key)
        try:
            validated = validate_document(document)
        except BlueprintValidationError as exc:
            raise DirectorySettingsError("invalid InstanceBlueprint") from exc
        if validated.get("kind") != "InstanceBlueprint":
            raise DirectorySettingsError("expected InstanceBlueprint")
        gitea = validated["spec"]["gitea"]
        if gitea.get("expectedVersion", "1.27.2") != "1.27.2":
            raise DirectorySettingsError("unsupported expected Gitea version")
        expected = {
            "baseUrl": profile.base_url,
            "credentialSecretRef": profile.credential_ref,
            "caSecretRef": profile.ca_ref,
        }
        actual = {
            "baseUrl": gitea.get("baseUrl"),
            "credentialSecretRef": gitea.get("credentialSecretRef"),
            "caSecretRef": gitea.get("caSecretRef"),
        }
        if actual != expected:
            raise DirectorySettingsConflict(
                "blueprint target profile does not match locally approved profile"
            )
        sources = validated["spec"].get("authenticationSources", [])
        if len(sources) != 1:
            raise DirectorySettingsError(
                "blueprint import requires exactly one authentication source"
            )
        return self.save(instance_key, sources[0], expected_revision, actor)


DirectorySettings = DirectorySettingsService

__all__ = [
    "DirectorySettings",
    "DirectorySettingsConflict",
    "DirectorySettingsError",
    "DirectorySettingsService",
]
