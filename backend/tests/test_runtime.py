from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest
from gitferry.runtime import (
    RuntimePaths,
    RuntimeStorageError,
    initialize_runtime,
)
from gitferry.state_migrations import SCHEMA_VERSION
from gitferry.state_models import StateStoreError, UnsupportedSchemaVersion


def runtime_paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        state_database=tmp_path / "state" / "gitferry.sqlite3",
        package_directory=tmp_path / "packages",
        work_directory=tmp_path / "work",
    )


def test_fresh_runtime_storage_is_private_and_migrated(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path)

    context = initialize_runtime(paths)

    assert context.paths == paths
    assert context.store.connection_settings() == {
        "foreign_keys": 1,
        "journal_mode": "wal",
        "synchronous": 2,
    }
    connection = sqlite3.connect(paths.state_database)
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    connection.close()
    assert stat.S_IMODE(paths.state_database.stat().st_mode) == 0o600
    for directory in (
        paths.state_database.parent,
        paths.package_directory,
        paths.work_directory,
    ):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_runtime_paths_are_loaded_from_explicit_environment(tmp_path: Path) -> None:
    environment = {
        "GITFERRY_STATE_DB": str(tmp_path / "state" / "state.sqlite3"),
        "GITFERRY_PACKAGE_DIR": str(tmp_path / "package"),
        "GITFERRY_WORK_DIR": str(tmp_path / "work"),
    }

    assert RuntimePaths.from_environment(environment) == RuntimePaths(
        state_database=tmp_path / "state" / "state.sqlite3",
        package_directory=tmp_path / "package",
        work_directory=tmp_path / "work",
    )


@pytest.mark.parametrize(
    "paths",
    [
        RuntimePaths(Path("relative.sqlite3"), Path("/packages"), Path("/work")),
        RuntimePaths(Path("/state/db.sqlite3"), Path("relative-packages"), Path("/work")),
        RuntimePaths(Path("/state/db.sqlite3"), Path("/packages"), Path("relative-work")),
    ],
)
def test_runtime_rejects_relative_managed_paths(paths: RuntimePaths) -> None:
    with pytest.raises(RuntimeStorageError, match="must be absolute"):
        initialize_runtime(paths)


def test_runtime_rejects_overlapping_managed_directories(tmp_path: Path) -> None:
    paths = RuntimePaths(
        state_database=tmp_path / "state" / "gitferry.sqlite3",
        package_directory=tmp_path / "data",
        work_directory=tmp_path / "data" / "work",
    )

    with pytest.raises(RuntimeStorageError, match="must not overlap"):
        initialize_runtime(paths)


def test_runtime_rejects_symlinked_storage_component(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    symlink = tmp_path / "linked"
    symlink.symlink_to(real, target_is_directory=True)
    paths = RuntimePaths(
        state_database=symlink / "state" / "gitferry.sqlite3",
        package_directory=tmp_path / "packages",
        work_directory=tmp_path / "work",
    )

    with pytest.raises(RuntimeStorageError, match="symbolic link"):
        initialize_runtime(paths)


def test_runtime_rejects_read_only_required_directory(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path)
    paths.package_directory.mkdir(mode=0o700)
    paths.package_directory.chmod(0o500)
    try:
        with pytest.raises(RuntimeStorageError, match="not writable"):
            initialize_runtime(paths)
    finally:
        paths.package_directory.chmod(0o700)


def test_runtime_refuses_newer_schema_without_replacing_it(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path)
    paths.state_database.parent.mkdir(mode=0o700)
    connection = sqlite3.connect(paths.state_database)
    connection.execute("CREATE TABLE future_data (value TEXT NOT NULL)")
    connection.execute("INSERT INTO future_data VALUES ('keep-me')")
    connection.execute("PRAGMA user_version = 999")
    connection.commit()
    connection.close()
    paths.state_database.chmod(0o600)

    with pytest.raises(UnsupportedSchemaVersion, match="newer schema version 999"):
        initialize_runtime(paths)

    connection = sqlite3.connect(paths.state_database)
    assert connection.execute("SELECT value FROM future_data").fetchone() == ("keep-me",)
    connection.close()


def test_runtime_refuses_corrupt_database_without_replacing_it(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path)
    paths.state_database.parent.mkdir(mode=0o700)
    original = b"not-a-sqlite-database\x00keep-this"
    paths.state_database.write_bytes(original)
    paths.state_database.chmod(0o600)

    with pytest.raises(StateStoreError, match="cannot open state database"):
        initialize_runtime(paths)

    assert paths.state_database.read_bytes() == original


def test_runtime_rejects_existing_state_database_with_broad_permissions(
    tmp_path: Path,
) -> None:
    paths = runtime_paths(tmp_path)
    paths.state_database.parent.mkdir(mode=0o700)
    paths.state_database.touch(mode=0o644)
    os.chmod(paths.state_database, 0o644)

    with pytest.raises(RuntimeStorageError, match="permissions must be 0600"):
        initialize_runtime(paths)
