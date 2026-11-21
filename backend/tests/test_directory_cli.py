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
        ]
    )
    assert args.directory_action == "bootstrap-plan"
    assert args.gitea_binary.is_absolute()


def test_errors_are_fixed_safe_json(tmp_path: Path, capsys) -> None:
    args = parser().parse_args(["--database", str(tmp_path / "state.sqlite3"), "list"])
    assert execute(args) == 0
    assert json.loads(capsys.readouterr().out) == []
