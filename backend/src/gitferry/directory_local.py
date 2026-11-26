"""Target-local execution adapter for the secret-free LDAP-simple bootstrap."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from gitferry.directory_bootstrap import (
    DirectoryBootstrapPreflight,
    prepare_ldap_simple_bootstrap,
)
from gitferry.directory_execution import (
    DirectoryBootstrapConflict,
    DirectoryBootstrapRecord,
    DirectoryBootstrapService,
)
from gitferry.state_store import StateStore

_VERSION = re.compile(r"(?<![0-9A-Za-z-])(\d+\.\d+\.\d+)(?![0-9A-Za-z._+-])")
_DECIMAL_ID = re.compile(r"^[0-9]+$")
_SIMPLE_TYPE = "LDAP (simple auth)"
_MAX_CONFIG = 2 * 1024 * 1024
_MAX_CA = 1024 * 1024


class DirectoryLocalError(RuntimeError):
    """A bounded local prerequisite, command, or readback failed."""


@dataclass(frozen=True)
class LocalAuthSource:
    native_source_id: str
    name: str
    source_type: str
    enabled: bool


@dataclass(frozen=True)
class LocalBootstrapPlan:
    preflight: DirectoryBootstrapPreflight
    config_fingerprint: str
    existing_source: LocalAuthSource | None

    @property
    def command(self) -> tuple[str, ...]:
        return self.preflight.command


def _file(path: Path, limit: int, label: str) -> bytes:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise DirectoryLocalError(f"target-local {label} must be an absolute regular file")
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise DirectoryLocalError(f"target-local {label} exceeds size limit")
        return data
    except DirectoryLocalError:
        raise
    except OSError as exc:
        raise DirectoryLocalError(f"target-local {label} could not be read") from exc


class LocalBootstrap:
    """Run only the fixed Gitea admin command on the target host."""

    def __init__(
        self,
        state: StateStore,
        instance_key: str,
        realm_key: str,
        source: Mapping[str, Any],
        gitea_binary: Path,
        gitea_config: Path,
        ca_path: Path,
        *,
        work_path: Path,
        custom_path: Path,
    ) -> None:
        self.state = state
        self.instance_key = instance_key
        self.realm_key = realm_key
        self.source = source
        self.gitea_binary = gitea_binary
        self.gitea_config = gitea_config
        self.ca_path = ca_path
        self.work_path = work_path
        self.custom_path = custom_path
        self.journal = DirectoryBootstrapService(state)

    def _preflight(self) -> tuple[DirectoryBootstrapPreflight, str]:
        config_bytes = _file(self.gitea_config, _MAX_CONFIG, "Gitea config")
        _file(self.ca_path, _MAX_CA, "CA")
        if (
            not self.gitea_binary.is_absolute()
            or self.gitea_binary.is_symlink()
            or not self.gitea_binary.is_file()
            or not os.access(self.gitea_binary, os.X_OK)
        ):
            raise DirectoryLocalError(
                "Gitea binary must be an absolute executable regular file without symlinks"
            )
        try:
            preflight = prepare_ldap_simple_bootstrap(
                self.source,
                gitea_binary=self.gitea_binary,
                gitea_config=self.gitea_config,
                ca_path=self.ca_path,
            )
        except Exception as exc:
            raise DirectoryLocalError("LDAP-simple bootstrap preflight failed") from exc
        if self.source.get("directoryRealm") != self.realm_key:
            raise DirectoryLocalError("directory source realm does not match supplied realm key")
        context = {}
        for label, path in (("work", self.work_path), ("custom", self.custom_path)):
            if not path.is_absolute() or path.is_symlink() or not path.is_dir():
                raise DirectoryLocalError("explicit work/custom directory is unavailable")
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
            context[label] = {"path": str(resolved), "device": stat.st_dev, "inode": stat.st_ino}
        installation = self.state.get_instance(self.instance_key)
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        try:
            parser.read_string("[DEFAULT]\n" + config_bytes.decode("utf-8"))
            root_url = parser.get("server", "ROOT_URL").strip().strip('"')
        except (ValueError, configparser.Error, UnicodeError) as exc:
            raise DirectoryLocalError(
                "explicit server ROOT_URL is required in Gitea config"
            ) from exc
        if root_url.rstrip("/") != installation.base_url.rstrip("/"):
            raise DirectoryLocalError("Gitea config ROOT_URL differs from the registered target")
        prefix = (
            *preflight.command[:3],
            "--work-path",
            str(context["work"]["path"]),
            "--custom-path",
            str(context["custom"]["path"]),
        )
        preflight = replace(preflight, command=(*prefix, *preflight.command[3:]))
        with self.gitea_binary.open("rb") as stream:
            binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        fingerprint = {
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "config_path": str(self.gitea_config.resolve(strict=True)),
            "binary_path": str(self.gitea_binary.resolve(strict=True)),
            "binary_sha256": binary_digest,
            "ca_path": str(self.ca_path.resolve(strict=True)),
            "ca_sha256": hashlib.sha256(_file(self.ca_path, _MAX_CA, "CA")).hexdigest(),
            "instance_key": installation.instance_key,
            "installation_marker": installation.installation_marker,
            "context": context,
        }
        self._version(preflight)
        return preflight, hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True).encode()
        ).hexdigest()

    def _stored(
        self,
        preflight: DirectoryBootstrapPreflight,
        config_fingerprint: str,
        name: str,
    ) -> DirectoryBootstrapRecord | None:
        record = self.journal.get(self.instance_key, name)
        if record is not None and (
            record.realm_key != self.realm_key
            or record.intent_digest != preflight.intent_digest
            or record.config_fingerprint != config_fingerprint
        ):
            raise DirectoryBootstrapConflict("stored directory bootstrap intent or config changed")
        return record

    def _environment(self, preflight: DirectoryBootstrapPreflight) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GITEA_") and key != "CONF_ARG"
        }
        environment.update(dict(preflight.environment))
        return environment

    def _run(self, argv: tuple[str, ...], preflight: DirectoryBootstrapPreflight) -> str:
        try:
            result = subprocess.run(  # noqa: S603 - executable path is validated locally
                list(argv),
                env=self._environment(preflight),
                cwd=self.work_path,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DirectoryLocalError("Gitea local command failed or timed out") from exc
        if result.returncode != 0:
            raise DirectoryLocalError("Gitea local command returned an error")
        return result.stdout

    def _version(self, preflight: DirectoryBootstrapPreflight) -> None:
        output = self._run((os.fspath(self.gitea_binary), "--version"), preflight)
        match = _VERSION.search(output)
        if match is None:
            raise DirectoryLocalError("Gitea binary version is not a stable release version")

    def _list(self, preflight: DirectoryBootstrapPreflight) -> dict[str, LocalAuthSource]:
        argv = (
            *preflight.command[: preflight.command.index("admin")],
            "admin",
            "auth",
            "list",
            "--vertical-bars",
        )
        output = self._run(argv, preflight)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines or [field.strip() for field in lines[0].split("|")] != [
            "ID",
            "Name",
            "Type",
            "Enabled",
        ]:
            raise DirectoryLocalError("Gitea auth list header is invalid")
        sources: dict[str, LocalAuthSource] = {}
        ids: set[str] = set()
        for line in lines[1:]:
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != 4:
                raise DirectoryLocalError("Gitea auth list has an invalid source row")
            native_id, name, source_type, enabled = fields
            if (
                not _DECIMAL_ID.fullmatch(native_id)
                or int(native_id) <= 0
                or not name
                or not source_type
            ):
                raise DirectoryLocalError("Gitea auth list has an invalid source row")
            if enabled.lower() not in {"true", "false"}:
                raise DirectoryLocalError("Gitea auth list has an invalid enabled value")
            if name in sources:
                raise DirectoryLocalError("Gitea auth list has duplicate source names")
            if native_id in ids:
                raise DirectoryLocalError("Gitea auth list has duplicate source IDs")
            ids.add(native_id)
            sources[name] = LocalAuthSource(
                native_source_id=native_id,
                name=name,
                source_type=source_type,
                enabled=enabled.lower() == "true",
            )
        return sources

    @staticmethod
    def _matching(sources: Mapping[str, LocalAuthSource], name: str) -> LocalAuthSource | None:
        return sources.get(name)

    def plan(self) -> LocalBootstrapPlan:
        preflight, config_fingerprint = self._preflight()
        name = str(self.source.get("name", ""))
        stored = self._stored(preflight, config_fingerprint, name)
        existing = self._matching(self._list(preflight), name)
        if existing is not None and stored is None:
            raise DirectoryLocalError("authentication source name already exists")
        return LocalBootstrapPlan(preflight, config_fingerprint, existing)

    def apply(self) -> DirectoryBootstrapRecord:
        preflight, config_fingerprint = self._preflight()
        name = str(self.source.get("name", ""))
        before = self._list(preflight)
        existing = self._matching(before, name)
        current = self._stored(preflight, config_fingerprint, name)
        if current is None and existing is not None:
            raise DirectoryLocalError("authentication source name already exists")
        record = self.journal.prepare(
            self.instance_key,
            self.realm_key,
            name,
            preflight.intent_digest,
            config_fingerprint,
        )

        before_create: dict[str, LocalAuthSource] = {}

        def before_execute() -> None:
            _, current_fingerprint = self._preflight()
            if current_fingerprint != config_fingerprint:
                raise DirectoryLocalError("Gitea execution context changed before local create")
            before_create.update(self._list(preflight))
            if name in before_create:
                raise DirectoryLocalError("authentication source name appeared before create")

        def runner(_: DirectoryBootstrapRecord) -> str:
            self._run(preflight.command, preflight)
            after = self._list(preflight)
            created = after.get(name)
            if created is None or name in before_create:
                raise DirectoryLocalError("Gitea auth source was not uniquely created")
            if created.source_type != _SIMPLE_TYPE or not created.enabled:
                raise DirectoryLocalError("Gitea auth source readback is not LDAP-simple enabled")
            old_ids = {source.native_source_id for source in before_create.values()}
            new_ids = {
                source.native_source_id
                for source in after.values()
                if source.native_source_id not in old_ids
            }
            if new_ids != {created.native_source_id}:
                raise DirectoryLocalError("Gitea auth source ID was not newly allocated")
            return created.native_source_id

        def verify(current_record: DirectoryBootstrapRecord) -> bool:
            observed = self._list(preflight).get(name)
            return bool(
                observed
                and observed.native_source_id == current_record.native_source_id
                and observed.name == name
                and observed.source_type == _SIMPLE_TYPE
                and observed.enabled
            )

        return self.journal.apply(record, runner, verify=verify, before_execute=before_execute)

    def status(self) -> DirectoryBootstrapRecord | None:
        preflight, config_fingerprint = self._preflight()
        name = str(self.source.get("name", ""))
        record = self._stored(preflight, config_fingerprint, name)
        if record is None:
            return None
        if record.status == "confirmed":
            observed = self._list(preflight).get(name)
            if not (
                observed
                and observed.native_source_id == record.native_source_id
                and observed.name == name
                and observed.source_type == _SIMPLE_TYPE
                and observed.enabled
            ):
                raise DirectoryBootstrapConflict("confirmed directory source readback changed")
        return record


__all__ = ["DirectoryLocalError", "LocalAuthSource", "LocalBootstrapPlan", "LocalBootstrap"]
