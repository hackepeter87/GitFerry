import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from gitferry.connection_profiles import (
    ConnectionProfile,
    ProfileConflict,
    ProfileService,
    ProfileValidationError,
    normalize_url,
)
from gitferry.state_store import StateStore


def profile(key: str = "a") -> ConnectionProfile:
    return ConnectionProfile(
        key, "Synthetic Gitea", "https://forge.invalid", "install-1", "token-a"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://forge.invalid",
        "https://user:password@forge.invalid",
        "https://forge.invalid?token=x",
        "https://forge.invalid/#fragment",
        "https://forge.invalid/../admin",
        "https://forge.invalid/%2e%2e",
        "https://forge.invalid\\@other.invalid",
        "https://forge.invalid\n",
        "file:///etc/passwd",
    ],
)
def test_unsafe_urls_rejected(url: str) -> None:
    with pytest.raises(ProfileValidationError):
        normalize_url(url)


def test_normalization_retains_subpath() -> None:
    assert normalize_url("https://FORGE.invalid:443/gitea/") == "https://forge.invalid/gitea"


def test_registration_is_atomic_and_not_redirectable(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    service = ProfileService(store)
    saved = service.create(profile())
    assert store.get_instance("a").installation_marker == saved.installation_marker
    with pytest.raises(ProfileConflict):
        service.create(replace(profile(), base_url="https://elsewhere.invalid"))
    with pytest.raises(ProfileConflict):
        service.create(replace(profile("b"), installation_marker="install-2"))
    with store.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM instances").fetchone()[0] == 1


def test_revision_protects_update_and_rebuild_keeps_old_identity(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    service = ProfileService(store)
    old = service.create(profile())
    edited = service.update(
        "a", expected_revision=1, display_name="Renamed", credential_ref="rotated", ca_ref=None
    )
    assert edited.instance_key == old.instance_key and edited.revision == 2
    with pytest.raises(ProfileConflict):
        service.update(
            "a", expected_revision=1, display_name="Stale", credential_ref="stale", ca_ref=None
        )
    rebuilt = service.replace_installation(
        "a", expected_revision=2, installation_marker="install-2"
    )
    assert rebuilt.instance_key != old.instance_key
    assert not service.get("a").enabled
    assert store.get_instance("a").installation_marker == "install-1"
    assert service.get(rebuilt.instance_key).enabled
    store.validate_integrity()


def test_profile_survives_real_process_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    ProfileService(StateStore(path)).create(profile())
    result = subprocess.run(  # noqa: S603 - fixed interpreter/script and synthetic database path
        [
            sys.executable,
            "-c",
            "import sys; from gitferry.state_store import StateStore; "
            "from gitferry.connection_profiles import ProfileService; "
            "p=ProfileService(StateStore(sys.argv[1])).get('a'); "
            "assert p.credential_ref=='token-a' and p.revision==1; print(p.instance_key)",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "a"


def test_transport_cannot_supply_secret_or_local_approval(tmp_path: Path) -> None:
    service = ProfileService(StateStore(tmp_path / "state.sqlite3"))
    document = service.export_configuration()
    assert document == {"format": "gitferry-connections", "version": 1, "profiles": []}
    service.create(profile())
    document = service.export_configuration()
    document["profiles"][0]["token"] = "synthetic-secret"  # noqa: S105 - rejection fixture
    with pytest.raises(ProfileValidationError):
        service.import_configuration(document)


def test_import_is_atomic_and_requires_local_approval(tmp_path: Path) -> None:
    source = ProfileService(StateStore(tmp_path / "source.sqlite3"))
    source.create(profile())
    document = source.export_configuration()
    target = ProfileService(StateStore(tmp_path / "target.sqlite3"))
    assert not target.import_configuration(document)[0].locally_approved
    original = target.list_profiles()
    with pytest.raises(ProfileConflict):
        target.import_configuration(document)
    assert target.list_profiles() == original
    fresh = ProfileService(StateStore(tmp_path / "fresh.sqlite3"))
    document["profiles"].append(document["profiles"][0])
    with pytest.raises(ProfileConflict):
        fresh.import_configuration(document)
    assert fresh.list_profiles() == []
    with fresh.store.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM instances").fetchone()[0] == 0


def test_cli_export_import_uses_same_profiles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from gitferry.cli import main

    database = tmp_path / "state.sqlite3"
    assert (
        main(
            [
                "connections",
                "--database",
                str(database),
                "create",
                "--base-url",
                "https://forge.invalid",
                "--installation-marker",
                "install-a",
                "--display-name",
                "Synthetic",
                "--credential-ref",
                "token-a",
            ]
        )
        == 0
    )
    profile_id = json.loads(capsys.readouterr().out)["instance_key"]
    assert main(["connections", "--database", str(database), "export"]) == 0
    exported = capsys.readouterr().out
    assert "locally_approved" not in exported
    document = tmp_path / "profiles.json"
    document.write_text(exported)
    target = tmp_path / "target.sqlite3"
    assert main(["connections", "--database", str(target), "import", str(document)]) == 0
    imported = json.loads(capsys.readouterr().out)[0]
    assert imported["instance_key"] == profile_id and not imported["locally_approved"]
    prefix = ["connections", "--database", str(target)]
    assert (
        main(
            prefix
            + [
                "update",
                profile_id,
                "--expected-revision",
                "1",
                "--display-name",
                "Changed",
                "--credential-ref",
                "token-b",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["revision"] == 2
    assert (
        main(
            prefix
            + [
                "replace-installation",
                profile_id,
                "--expected-revision",
                "2",
                "--installation-marker",
                "install-b",
            ]
        )
        == 0
    )
    replacement = json.loads(capsys.readouterr().out)
    assert replacement["instance_key"] != profile_id and not replacement["locally_approved"]
    assert main(prefix + ["retire", replacement["instance_key"], "--expected-revision", "1"]) == 0
    assert not json.loads(capsys.readouterr().out)["enabled"]
    assert main(prefix + ["list"]) == 0
    assert all(not value["enabled"] for value in json.loads(capsys.readouterr().out))


def test_concurrent_creates_cannot_claim_same_endpoint(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    state = StateStore(tmp_path / "state.sqlite3")

    def create(key: str) -> bool:
        try:
            ProfileService(state).create(
                replace(profile(key), installation_marker="install-" + key)
            )
            return True
        except ProfileConflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(create, ["a", "b"])) == 1
    assert len(ProfileService(state).list_profiles()) == 1
    state.validate_integrity()


def test_schema_six_preserves_existing_registrations(tmp_path: Path) -> None:
    import sqlite3

    from gitferry.state_migrations import MIGRATIONS

    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as connection:
        for version in range(1, 6):
            connection.executescript(MIGRATIONS[version])
            connection.execute("INSERT INTO schema_migrations VALUES (?, 'synthetic')", (version,))
        connection.execute("PRAGMA user_version=5")
        connection.execute(
            "INSERT INTO instances VALUES ('a','old','https://forge.invalid','install-1','synthetic')"
        )
    state = StateStore(path)
    ProfileService(state).create(profile())
    assert state.get_instance("a").display_name == "old"
    state.validate_integrity()
