from __future__ import annotations

import json
from pathlib import Path

import pytest
from gitferry.auth import AuthService, InvalidCredentials, SessionDenied
from gitferry.cli import main
from gitferry.state_store import StateStore


def password_file(tmp_path: Path, value: str, name: str = "admin-password") -> Path:
    path = tmp_path / name
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_cli_bootstrap_creates_exactly_one_admin_from_secret_file(
    tmp_path: Path, capsys: object
) -> None:
    database = tmp_path / "state.sqlite3"
    secret = password_file(tmp_path, "correct synthetic passphrase")

    exit_code = main(
        [
            "admin",
            "bootstrap",
            "--database",
            str(database),
            "--username",
            "synthetic.admin",
            "--password-file",
            str(secret),
        ]
    )

    assert exit_code == 0
    output_text = capsys.readouterr().out  # type: ignore[attr-defined]
    output = json.loads(output_text)
    assert output == {
        "status": "bootstrapped",
        "username": "synthetic.admin",
        "role": "admin",
    }
    assert "correct synthetic passphrase" not in output_text
    issued = AuthService(StateStore(database)).login(
        "synthetic.admin",
        "correct synthetic passphrase",
        client_key="synthetic-client",
    )
    assert issued.principal.is_admin is True

    second = main(
        [
            "admin",
            "bootstrap",
            "--database",
            str(database),
            "--username",
            "other.admin",
            "--password-file",
            str(secret),
        ]
    )
    assert second == 2
    second_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "already completed" in second_output
    assert "correct synthetic passphrase" not in second_output


def test_cli_recovery_changes_password_and_revokes_sessions(tmp_path: Path, capsys: object) -> None:
    database = tmp_path / "state.sqlite3"
    first_secret = password_file(tmp_path, "correct synthetic passphrase", "first")
    replacement = password_file(tmp_path, "replacement synthetic passphrase", "replacement")
    assert (
        main(
            [
                "admin",
                "bootstrap",
                "--database",
                str(database),
                "--username",
                "synthetic.admin",
                "--password-file",
                str(first_secret),
            ]
        )
        == 0
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    auth = AuthService(StateStore(database))
    issued = auth.login(
        "synthetic.admin",
        "correct synthetic passphrase",
        client_key="synthetic-client",
    )

    exit_code = main(
        [
            "admin",
            "recover",
            "--database",
            str(database),
            "--password-file",
            str(replacement),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output == {
        "status": "recovered",
        "username": "synthetic.admin",
        "sessions": "revoked",
    }
    with pytest.raises(SessionDenied):
        auth.authenticate(issued.session_token)
    with pytest.raises(InvalidCredentials):
        auth.login(
            "synthetic.admin",
            "correct synthetic passphrase",
            client_key="other-client",
        )
    assert (
        auth.login(
            "synthetic.admin",
            "replacement synthetic passphrase",
            client_key="other-client",
        ).principal.username
        == "synthetic.admin"
    )


def test_cli_status_and_missing_bootstrap_are_explicit(tmp_path: Path, capsys: object) -> None:
    database = tmp_path / "state.sqlite3"

    assert main(["admin", "status", "--database", str(database)]) == 0
    assert json.loads(capsys.readouterr().out) == {  # type: ignore[attr-defined]
        "bootstrapRequired": True
    }
    replacement = password_file(tmp_path, "replacement synthetic passphrase")
    assert (
        main(
            [
                "admin",
                "recover",
                "--database",
                str(database),
                "--password-file",
                str(replacement),
            ]
        )
        == 2
    )
    assert "bootstrap is required" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_cli_rejects_symlink_oversized_or_group_writable_password_file(
    tmp_path: Path, capsys: object
) -> None:
    database = tmp_path / "state.sqlite3"
    source = password_file(tmp_path, "correct synthetic passphrase", "source")
    symlink = tmp_path / "linked-password"
    symlink.symlink_to(source)
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"x" * 4097)
    oversized.chmod(0o600)
    writable = password_file(tmp_path, "correct synthetic passphrase", "writable")
    writable.chmod(0o620)

    for rejected in (symlink, oversized, writable):
        exit_code = main(
            [
                "admin",
                "bootstrap",
                "--database",
                str(database),
                "--username",
                "synthetic.admin",
                "--password-file",
                str(rejected),
            ]
        )
        assert exit_code == 2
        output = capsys.readouterr().out  # type: ignore[attr-defined]
        assert "correct synthetic passphrase" not in output
