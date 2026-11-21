"""Local, revisioned Gitea connection profiles; credentials never enter this store."""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator

from gitferry.blueprints import _schema_directory
from gitferry.state_store import StateStore

_KEY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
_PATH = re.compile(r"[/a-zA-Z0-9._~-]*\Z")


class ProfileValidationError(ValueError):
    """Invalid non-secret configuration; messages do not echo input."""


class ProfileConflict(RuntimeError):
    """A stale edit, collision, or attempted redirection requires local resolution."""


def normalize_url(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in value)
    ):
        raise ProfileValidationError("Invalid Gitea URL")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "?" in value
            or "#" in value
            or not _PATH.fullmatch(parts.path)
            or any(segment in {".", ".."} for segment in parts.path.split("/"))
            or "//" in parts.path
        ):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{ipaddress.IPv6Address(host)}]"
        elif not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        authority = host if port in (None, 443) else f"{host}:{port}"
        return urlunsplit(("https", authority, parts.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError):
        raise ProfileValidationError(
            "Gitea requires an HTTPS URL without credentials or query"
        ) from None


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ProfileValidationError("Invalid identifier or local reference")


@dataclass(frozen=True)
class ConnectionProfile:
    instance_key: str
    display_name: str
    base_url: str
    installation_marker: str
    credential_ref: str
    ca_ref: str | None = None
    revision: int = 1
    enabled: bool = True
    locally_approved: bool = False

    def __post_init__(self) -> None:
        for value in (self.instance_key, self.installation_marker, self.credential_ref):
            _identifier(value)
        if self.ca_ref is not None:
            _identifier(self.ca_ref)
        if (
            not isinstance(self.display_name, str)
            or not self.display_name.strip()
            or len(self.display_name) > 120
            or any(ord(char) < 32 or ord(char) == 127 for char in self.display_name)
        ):
            raise ProfileValidationError("Invalid profile display name")
        if type(self.revision) is not int or self.revision < 1:
            raise ProfileValidationError("Invalid profile revision")
        if type(self.enabled) is not bool or type(self.locally_approved) is not bool:
            raise ProfileValidationError("Invalid local profile status")
        object.__setattr__(self, "base_url", normalize_url(self.base_url))


class ProfileService:
    def __init__(self, store: StateStore):
        self.store = store

    @staticmethod
    def _record(row: sqlite3.Row) -> ConnectionProfile:
        return ConnectionProfile(
            **{
                **dict(row),
                "enabled": bool(row["enabled"]),
                "locally_approved": bool(row["locally_approved"]),
            },
        )

    def list_profiles(self) -> list[ConnectionProfile]:
        with self.store.read_connection() as connection:
            return [
                self._record(row)
                for row in connection.execute(
                    "SELECT p.*, i.installation_marker FROM connection_profiles p "
                    "JOIN instances i USING (instance_key) ORDER BY p.instance_key"
                )
            ]

    def get(self, key: str) -> ConnectionProfile:
        _identifier(key)
        with self.store.read_connection() as connection:
            return self._get(connection, key)

    def _get(self, connection: sqlite3.Connection, key: str) -> ConnectionProfile:
        row = connection.execute(
            "SELECT p.*, i.installation_marker FROM connection_profiles p "
            "JOIN instances i USING (instance_key) WHERE p.instance_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            raise ProfileConflict("Connection profile does not exist")
        return self._record(row)

    @staticmethod
    def _event(connection: sqlite3.Connection, profile: ConnectionProfile, action: str) -> None:
        connection.execute(
            "INSERT INTO connection_profile_events(instance_key,revision,action,occurred_at) "
            "VALUES (?,?,?,?)",
            (
                profile.instance_key,
                profile.revision,
                action,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    def _insert(self, connection: sqlite3.Connection, profile: ConnectionProfile) -> None:
        # This uses the existing instance namespace. Conflicts never adopt or redirect an identity.
        instance = connection.execute(
            "SELECT * FROM instances WHERE instance_key = ?",
            (profile.instance_key,),
        ).fetchone()
        if instance is not None:
            if (instance["base_url"], instance["installation_marker"]) != (
                profile.base_url,
                profile.installation_marker,
            ):
                raise ProfileConflict("Existing instance registration must not be redirected")
        else:
            connection.execute(
                "INSERT INTO instances VALUES (?,?,?,?,?)",
                (
                    profile.instance_key,
                    profile.display_name,
                    profile.base_url,
                    profile.installation_marker,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        connection.execute(
            "INSERT INTO connection_profiles VALUES (?,?,?,?,?,?,?,?)",
            (
                profile.instance_key,
                profile.display_name,
                profile.base_url,
                profile.credential_ref,
                profile.ca_ref,
                profile.revision,
                int(profile.enabled),
                int(profile.locally_approved),
            ),
        )
        self._event(connection, profile, "create")

    def create(self, profile: ConnectionProfile) -> ConnectionProfile:
        if profile.revision != 1:
            raise ProfileValidationError("New profiles start at revision 1")
        try:
            with self.store.transaction() as connection:
                self._insert(connection, profile)
        except sqlite3.IntegrityError:
            raise ProfileConflict("Instance or active endpoint is already registered") from None
        return profile

    @staticmethod
    def _check_revision(profile: ConnectionProfile, expected: int) -> None:
        if type(expected) is not int or profile.revision != expected or not profile.enabled:
            raise ProfileConflict("Profile changed or retired; reload before editing")

    def update(
        self,
        key: str,
        *,
        expected_revision: int,
        display_name: str,
        credential_ref: str,
        ca_ref: str | None,
    ) -> ConnectionProfile:
        with self.store.transaction() as connection:
            old = self._get(connection, key)
            self._check_revision(old, expected_revision)
            new = replace(
                old,
                display_name=display_name,
                credential_ref=credential_ref,
                ca_ref=ca_ref,
                revision=old.revision + 1,
                locally_approved=False,
            )
            connection.execute(
                "UPDATE connection_profiles SET display_name=?,credential_ref=?,ca_ref=?,"
                "revision=?,locally_approved=0 WHERE instance_key=?",
                (new.display_name, new.credential_ref, new.ca_ref, new.revision, key),
            )
            self._event(connection, new, "update")
            return new

    def retire(self, key: str, *, expected_revision: int) -> ConnectionProfile:
        with self.store.transaction() as connection:
            return self._retire(connection, key, expected_revision)

    def _retire(self, connection: sqlite3.Connection, key: str, expected: int) -> ConnectionProfile:
        old = self._get(connection, key)
        self._check_revision(old, expected)
        new = replace(old, enabled=False, locally_approved=False, revision=old.revision + 1)
        connection.execute(
            "UPDATE connection_profiles SET enabled=0,locally_approved=0,"
            "revision=? WHERE instance_key=?",
            (new.revision, key),
        )
        self._event(connection, new, "retire")
        return new

    def replace_installation(
        self, key: str, *, expected_revision: int, installation_marker: str
    ) -> ConnectionProfile:
        try:
            with self.store.transaction() as connection:
                old = self._get(connection, key)
                if installation_marker == old.installation_marker:
                    raise ProfileConflict("A rebuild needs a different installation marker")
                self._retire(connection, key, expected_revision)
                new = replace(
                    old,
                    instance_key=str(uuid.uuid4()),
                    installation_marker=installation_marker,
                    revision=1,
                    locally_approved=False,
                )
                self._insert(connection, new)
                return new
        except sqlite3.IntegrityError:
            raise ProfileConflict("Installation marker is already registered") from None

    def export_configuration(self) -> dict[str, Any]:
        return {
            "format": "gitferry-connections",
            "version": 1,
            "profiles": [
                {
                    key: value
                    for key, value in asdict(profile).items()
                    if key not in {"revision", "enabled", "locally_approved"}
                }
                for profile in self.list_profiles()
                if profile.enabled
            ],
        }

    def import_configuration(self, document: dict[str, Any]) -> list[ConnectionProfile]:
        schema = json.loads(
            (_schema_directory() / "connection-profiles.schema.json").read_text(encoding="utf-8")
        )
        if not Draft202012Validator(schema).is_valid(document):
            raise ProfileValidationError("Invalid connection configuration schema")
        if (
            set(document) != {"format", "version", "profiles"}
            or document["format"] != "gitferry-connections"
            or type(document["version"]) is not int
            or document["version"] != 1
            or not isinstance(document["profiles"], list)
            or len(document["profiles"]) > 100
        ):
            raise ProfileValidationError("Unsupported connection configuration")
        profiles = []
        for item in document["profiles"]:
            if not isinstance(item, dict) or set(item) != {
                "instance_key",
                "display_name",
                "base_url",
                "installation_marker",
                "credential_ref",
                "ca_ref",
            }:
                raise ProfileValidationError("Unknown or missing connection fields")
            profiles.append(ConnectionProfile(**item, locally_approved=False))
        try:
            with self.store.transaction() as connection:
                for profile in profiles:
                    self._insert(connection, profile)
        except sqlite3.IntegrityError:
            raise ProfileConflict("Import conflicts with a local profile or endpoint") from None
        return profiles
