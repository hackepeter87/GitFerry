"""Fail-closed runtime storage initialization for the packaged application."""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from gitferry.state_store import StateStore

DEFAULT_STATE_DATABASE = Path("/var/lib/gitferry/state/gitferry.sqlite3")
DEFAULT_PACKAGE_DIRECTORY = Path("/var/lib/gitferry/packages")
DEFAULT_WORK_DIRECTORY = Path("/var/lib/gitferry/work")


class RuntimeStorageError(RuntimeError):
    """The configured packaged-runtime storage boundary is unsafe or unusable."""


@dataclass(frozen=True)
class RuntimePaths:
    """Operator-configurable persistent paths used by one GitFerry runtime."""

    state_database: Path
    package_directory: Path
    work_directory: Path

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> RuntimePaths:
        values = os.environ if environment is None else environment
        return cls(
            state_database=Path(values.get("GITFERRY_STATE_DB", str(DEFAULT_STATE_DATABASE))),
            package_directory=Path(
                values.get("GITFERRY_PACKAGE_DIR", str(DEFAULT_PACKAGE_DIRECTORY))
            ),
            work_directory=Path(values.get("GITFERRY_WORK_DIR", str(DEFAULT_WORK_DIRECTORY))),
        )


@dataclass(frozen=True)
class RuntimeContext:
    """Initialized local runtime resources shared by REST, CLI, and workers."""

    paths: RuntimePaths
    store: StateStore


@dataclass
class RuntimeStatus:
    """Small in-process readiness state; durable work remains in SQLite."""

    _phase: str = "starting"
    _mode: str = "managed"
    _schema_version: int | None = None
    _failure_code: str | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def starting(self, *, managed: bool) -> None:
        with self._lock:
            self._phase = "starting"
            self._mode = "managed" if managed else "development-unmanaged"
            self._schema_version = None
            self._failure_code = None

    def ready(self, *, schema_version: int | None) -> None:
        with self._lock:
            self._phase = "ready"
            self._schema_version = schema_version
            self._failure_code = None

    def failed(self, error: BaseException) -> None:
        with self._lock:
            self._phase = "failed"
            self._failure_code = type(error).__name__

    def stopping(self) -> None:
        with self._lock:
            self._phase = "stopping"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result: dict[str, Any] = {
                "status": self._phase,
                "mode": self._mode,
            }
            if self._schema_version is not None:
                result["schemaVersion"] = self._schema_version
            if self._failure_code is not None:
                result["failureCode"] = self._failure_code
            return result

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._phase == "ready"


def initialize_runtime(paths: RuntimePaths | None = None) -> RuntimeContext:
    """Validate persistent storage and migrate state without unsafe fallbacks."""

    selected = paths or RuntimePaths.from_environment()
    _validate_path_contract(selected)
    for directory in (
        selected.state_database.parent,
        selected.package_directory,
        selected.work_directory,
    ):
        _ensure_private_writable_directory(directory)
    if selected.state_database.exists():
        _validate_existing_database(selected.state_database)
    else:
        _create_private_database_file(selected.state_database)
    store = StateStore(selected.state_database)
    return RuntimeContext(paths=selected, store=store)


def _validate_path_contract(paths: RuntimePaths) -> None:
    labelled = (
        ("state database", paths.state_database),
        ("package directory", paths.package_directory),
        ("work directory", paths.work_directory),
    )
    for label, path in labelled:
        if not path.is_absolute():
            raise RuntimeStorageError(f"{label} must be absolute")
        if ".." in path.parts:
            raise RuntimeStorageError(f"{label} must not contain parent traversal")
        _reject_symlink_components(path)

    directories = (
        paths.state_database.parent,
        paths.package_directory,
        paths.work_directory,
    )
    for index, left in enumerate(directories):
        for right in directories[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise RuntimeStorageError("managed state, package, and work paths must not overlap")


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise RuntimeStorageError("managed storage path contains a symbolic link")


def _ensure_private_writable_directory(path: Path) -> None:
    existed = path.exists()
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeStorageError("required storage directory cannot be created") from exc
    if not path.is_dir():
        raise RuntimeStorageError("required storage path is not a directory")
    if not existed:
        path.chmod(0o700)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeStorageError("required storage directory permissions must be 0700")
    probe = path / f".gitferry-write-probe-{uuid.uuid4().hex}"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(probe, flags, 0o600)
    except OSError as exc:
        raise RuntimeStorageError("required storage directory is not writable") from exc
    else:
        os.close(descriptor)
        probe.unlink()


def _validate_existing_database(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode):
        raise RuntimeStorageError("state database path is not a regular file")
    if stat.S_IMODE(details.st_mode) != 0o600:
        raise RuntimeStorageError("state database permissions must be 0600")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeStorageError("state database is not writable") from exc
    else:
        os.close(descriptor)


def _create_private_database_file(path: Path) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeStorageError("state database cannot be created safely") from exc
    else:
        os.close(descriptor)
