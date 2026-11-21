from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from gitferry.directory_execution import (
    DirectoryBootstrapConflict,
    DirectoryBootstrapUnknown,
)
from gitferry.directory_local import DirectoryLocalError, LocalBootstrap
from gitferry.state_store import StateStore

SOURCE = {
    "mode": "simple",
    "type": "ldaps",
    "name": "Synthetic-LDAP",
    "directoryRealm": "realm-a",
    "host": "openldap",
    "port": 636,
    "userDn": "uid={username},ou=people,dc=example,dc=org",
    "userFilter": "(uid=%s)",
    "emailAttribute": "mail",
    "usernameAttribute": "uid",
    "caSecretRef": "directory-ca",
}


def _setup(tmp_path: Path, *, listing: list[list[str]] | None = None):
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("instance-a", "Synthetic A", "http://a.invalid", "install-a")
    state.register_realm("realm-a", "Synthetic LDAP", "realm-marker-a")
    config = tmp_path / "app.ini"
    config.write_text(
        "[server]\nROOT_URL = http://a.invalid\nAPP_NAME = Synthetic\n", encoding="utf-8"
    )
    ca = tmp_path / "ca.pem"
    ca.write_text("synthetic-ca\n", encoding="utf-8")
    listing_path = tmp_path / "listing.json"
    listing_path.write_text(json.dumps(listing or []), encoding="utf-8")
    binary = tmp_path / "gitea-fake"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('Gitea version: ' + os.environ.get('FAKE_VERSION', '1.27.2'))\n"
        "    raise SystemExit(0)\n"
        "path = pathlib.Path(os.environ['FAKE_LISTING'])\n"
        "rows = json.loads(path.read_text())\n"
        "if sys.argv[-3:] == ['auth', 'list', '--vertical-bars']:\n"
        "    print('ID|Name|Type|Enabled')\n"
        "    for row in rows:\n"
        "        print('|'.join(row))\n"
        "    raise SystemExit(0)\n"
        "if sys.argv[-2:] == ['add-ldap-simple', '--unused']:\n"
        "    raise SystemExit(2)\n"
        "if 'add-ldap-simple' in sys.argv:\n"
        "    rows.append(['7', 'Synthetic-LDAP', 'LDAP (simple auth)', 'true'])\n"
        "    path.write_text(json.dumps(rows))\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    os.environ["FAKE_LISTING"] = str(listing_path)
    return state, LocalBootstrap(
        state,
        "instance-a",
        "realm-a",
        dict(SOURCE),
        binary,
        config,
        ca,
        work_path=tmp_path,
        custom_path=tmp_path,
    )


def test_plan_checks_version_and_returns_secret_free_fixed_command(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path)
    plan = bootstrap.plan()
    assert "--email-attribute" in plan.command
    assert plan.command[plan.command.index("--email-attribute") + 1] == "mail"
    assert "password" not in " ".join(plan.command).lower()
    assert len(plan.config_fingerprint) == 64


def test_source_realm_must_match_supplied_realm(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path)
    bootstrap.source["directoryRealm"] = "other-realm"  # type: ignore[index]
    with pytest.raises(DirectoryLocalError, match="realm"):
        bootstrap.plan()


def test_apply_creates_and_confirmed_replay_only_reads_back(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path)
    confirmed = bootstrap.apply()
    assert confirmed.status == "confirmed"
    assert confirmed.native_source_id == "7"
    replay = bootstrap.apply()
    assert replay == confirmed
    assert bootstrap.status() == confirmed
    assert bootstrap.plan().config_fingerprint == confirmed.config_fingerprint


def test_changed_config_cannot_be_reported_as_confirmed(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path)
    confirmed = bootstrap.apply()
    bootstrap.gitea_config.write_text(
        "[server]\nROOT_URL = http://a.invalid\nAPP_NAME = Changed\n", encoding="utf-8"
    )
    with pytest.raises(DirectoryBootstrapConflict, match="intent or config"):
        bootstrap.status()
    with pytest.raises(DirectoryBootstrapConflict, match="intent or config"):
        bootstrap.apply()
    assert confirmed.status == "confirmed"


def test_existing_name_is_refused_without_adoption(tmp_path: Path) -> None:
    _, bootstrap = _setup(
        tmp_path,
        listing=[["3", "Synthetic-LDAP", "LDAP (simple auth)", "true"]],
    )
    with pytest.raises(DirectoryLocalError, match="already exists"):
        bootstrap.plan()
    with pytest.raises(DirectoryLocalError, match="already exists"):
        bootstrap.apply()


def test_invalid_auth_list_is_rejected(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path, listing=[["not-a-number", "Other", "LDAP", "true"]])
    with pytest.raises(DirectoryLocalError, match="invalid source row"):
        bootstrap.plan()


def test_version_mismatch_is_rejected(tmp_path: Path) -> None:
    state, default_bootstrap = _setup(tmp_path)
    bootstrap = LocalBootstrap(
        state,
        default_bootstrap.instance_key,
        default_bootstrap.realm_key,
        SOURCE,
        default_bootstrap.gitea_binary,
        default_bootstrap.gitea_config,
        default_bootstrap.ca_path,
        work_path=tmp_path,
        custom_path=tmp_path,
        expected_version="1.27.3",
    )
    with pytest.raises(DirectoryLocalError, match="version"):
        bootstrap.plan()


def test_development_version_suffix_is_rejected(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path)
    os.environ["FAKE_VERSION"] = "1.27.2-dev"
    try:
        with pytest.raises(DirectoryLocalError, match="version"):
            bootstrap.plan()
    finally:
        os.environ.pop("FAKE_VERSION", None)


def test_create_failure_becomes_durable_unknown(tmp_path: Path) -> None:
    state, bootstrap = _setup(tmp_path)
    bootstrap.source["name"] = "Different"  # type: ignore[index]
    with pytest.raises(DirectoryBootstrapUnknown):
        bootstrap.apply()
    record = bootstrap.journal.get("instance-a", "Different")
    assert record is not None and record.status == "outcome_unknown"


def test_oversized_config_is_rejected(tmp_path: Path) -> None:
    _, bootstrap = _setup(tmp_path)
    bootstrap.gitea_config.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(DirectoryLocalError, match="size limit"):
        bootstrap.plan()


@pytest.mark.parametrize(
    "field", ["host", "userDn", "userFilter", "emailAttribute", "usernameAttribute"]
)
def test_control_characters_never_start_or_poison_journal(tmp_path, field):
    state, bootstrap = _setup(tmp_path)
    bootstrap.source[field] = "invalid\x00field"
    with pytest.raises(DirectoryLocalError, match="preflight"):
        bootstrap.apply()
    assert bootstrap.journal.get("instance-a", SOURCE["name"]) is None
    with state.read_connection() as connection:
        assert (
            connection.execute("SELECT count(*) FROM directory_bootstrap_events").fetchone()[0] == 0
        )


def test_context_override_is_scrubbed_and_explicit_context_change_conflicts(tmp_path, monkeypatch):
    _, bootstrap = _setup(tmp_path)
    plan = bootstrap.plan()
    monkeypatch.setenv("GITEA_WORK_DIR", "/unreviewed/server")
    monkeypatch.setenv("GITEA_CUSTOM", "/unreviewed/custom")
    monkeypatch.setenv("GITEA__database__PATH", "/unreviewed/database")
    monkeypatch.setenv("CONF_ARG", "--config /unreviewed/config")
    assert bootstrap.plan().config_fingerprint == plan.config_fingerprint
    env = bootstrap._environment(plan.preflight)
    assert not any(key.startswith("GITEA_") or key == "CONF_ARG" for key in env)
    confirmed = bootstrap.apply()
    changed = tmp_path / "different-work"
    changed.mkdir()
    bootstrap.work_path = changed
    with pytest.raises(DirectoryBootstrapConflict):
        bootstrap.apply()
    assert bootstrap.journal.get("instance-a", SOURCE["name"]) == confirmed


def test_target_root_url_mismatch_stops_before_execution(tmp_path):
    _, bootstrap = _setup(tmp_path)
    bootstrap.gitea_config.write_text("[server]\nROOT_URL=https://other.invalid\n")
    with pytest.raises(DirectoryLocalError, match="registered target"):
        bootstrap.apply()
    assert bootstrap.journal.get("instance-a", SOURCE["name"]) is None


@pytest.mark.parametrize("failure", ["listing", "changed-config"])
def test_definite_prerequisite_failure_remains_prepared(tmp_path, monkeypatch, failure):
    _, bootstrap = _setup(tmp_path)
    original = bootstrap._list
    calls = 0

    def listing(preflight):
        nonlocal calls
        calls += 1
        if calls == 1 and failure == "changed-config":
            with bootstrap.gitea_config.open("a") as stream:
                stream.write("\n# changed before the write lease\n")
        if calls == 2 and failure == "listing":
            raise DirectoryLocalError("synthetic readback unavailable")
        return original(preflight)

    monkeypatch.setattr(bootstrap, "_list", listing)
    with pytest.raises(DirectoryLocalError):
        bootstrap.apply()
    row = bootstrap.journal.get("instance-a", SOURCE["name"])
    assert row.status == "prepared"
    assert [
        event["to_status"] for event in bootstrap.journal.events("instance-a", SOURCE["name"])
    ] == ["prepared"]
    assert json.loads((tmp_path / "listing.json").read_text()) == []
