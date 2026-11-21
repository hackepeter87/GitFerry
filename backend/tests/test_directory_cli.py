import argparse
import json
from pathlib import Path

from gitferry.directory_cli import configure, execute


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    configure(value, None)
    return value


def test_configure_covers_settings_actions_and_local_paths(tmp_path: Path) -> None:
    args = parser().parse_args(
        [
            "--database",
            str(tmp_path / "state.sqlite3"),
            "bootstrap-plan",
            "--instance",
            "target",
            "--source-name",
            "ldap",
            "--gitea-binary",
            "/usr/local/bin/gitea",
            "--gitea-config",
            "/etc/gitea/app.ini",
            "--ca-file",
            "/etc/ssl/ca.pem",
            "--work-path",
            "/var/lib/gitea",
            "--custom-path",
            "/var/lib/gitea/custom",
        ]
    )
    assert args.directory_action == "bootstrap-plan"
    assert args.gitea_binary.is_absolute()


def test_errors_are_fixed_safe_json(tmp_path: Path, capsys) -> None:
    args = parser().parse_args(["--database", str(tmp_path / "state.sqlite3"), "list"])
    assert execute(args) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_installed_entrypoint_shape_and_uncertainty_inspection(tmp_path, capsys):
    import pytest
    from gitferry.cli import main
    from gitferry.directory_execution import DirectoryBootstrapService, DirectoryBootstrapUnknown
    from gitferry.state_store import StateStore

    path = tmp_path / "state.sqlite3"
    state = StateStore(path)
    state.register_instance("target", "Synthetic", "https://target.invalid", "installation")
    state.register_realm("realm", "Synthetic directory", "directory-installation")
    journal = DirectoryBootstrapService(state)
    row = journal.prepare("target", "realm", "ldap", "a" * 64, "b" * 64)

    def lost(_):
        raise RuntimeError("synthetic-sensitive-detail")

    with pytest.raises(DirectoryBootstrapUnknown):
        journal.apply(row, lost)
    assert (
        main(
            [
                "directories",
                "--database",
                str(path),
                "inspect-bootstrap",
                "--instance",
                "target",
                "--source-name",
                "ldap",
            ]
        )
        == 0
    )
    text = capsys.readouterr().out
    assert "synthetic-sensitive-detail" not in text
    result = json.loads(text)
    assert result["record"]["status"] == "outcome_unknown"
    assert result["record"]["native_source_id"] is None
    assert [event["to_status"] for event in result["events"]] == [
        "prepared",
        "executing",
        "outcome_unknown",
    ]
