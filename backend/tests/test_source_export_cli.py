from __future__ import annotations

import json
from pathlib import Path

import pytest
from gitferry import source_export_cli
from gitferry.cli import main
from gitferry.source_exports import GiteaBundleError, SourceSnapshotError
from test_source_exports import configure as configure_source


def test_exports_prepare_list_show_route_to_source_service(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    service = configure_source(tmp_path)
    env = monkeypatch  # type: ignore[assignment]
    env.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))  # type: ignore[attr-defined]
    env.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))  # type: ignore[attr-defined]

    assert (
        main(
            [
                "exports",
                "prepare",
                "--database",
                str(service.state.path),
                "--source-instance",
                "a",
                "--source-revision",
                "1",
                "--recipient-instance",
                "b",
                "--repository",
                "synthetic/selected=7",
                "--include-metadata",
                "--include-members",
                "--export-id",
                "synthetic-export",
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["export_id"] == "synthetic-export"
    assert prepared["request"]["repositories"] == [
        {"repository": "synthetic/selected", "global_id": "7"}
    ]

    assert main(["exports", "list", "--database", str(service.state.path)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [item["export_id"] for item in listed] == ["synthetic-export"]

    assert (
        main(
            [
                "exports",
                "show",
                "--database",
                str(service.state.path),
                "--export-id",
                "synthetic-export",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "prepared"


def test_exports_reject_malformed_repository_without_leaking_input(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    service = configure_source(tmp_path)
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))  # type: ignore[attr-defined]
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))  # type: ignore[attr-defined]

    assert (
        main(
            [
                "exports",
                "prepare",
                "--database",
                str(service.state.path),
                "--source-instance",
                "a",
                "--source-revision",
                "1",
                "--recipient-instance",
                "b",
                "--repository",
                "not-a-selection",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "failed",
        "error": "Source export selection is invalid or unavailable",
    }
    assert "not-a-selection" not in output


def test_exports_prepare_supports_bounded_git_return_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = configure_source(tmp_path)
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))

    assert (
        main(
            [
                "exports",
                "prepare",
                "--database",
                str(service.state.path),
                "--mode",
                "return",
                "--source-instance",
                "a",
                "--source-revision",
                "1",
                "--recipient-instance",
                "b",
                "--repository",
                "synthetic/selected=7",
                "--forward-package-id",
                "forward-1",
                "--forward-package-digest",
                "a" * 64,
                "--export-id",
                "synthetic-return",
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["request"] == {
        "version": 5,
        "mode": "return",
        "forward_package_id": "forward-1",
        "forward_package_digest": "a" * 64,
        "source_instance_key": "a",
        "source_profile_revision": 1,
        "recipient_instance_key": "b",
        "repositories": [{"repository": "synthetic/selected", "global_id": "7"}],
        "include_metadata": False,
        "include_members": False,
        "include_lfs": False,
        "include_wiki": False,
        "include_labels": False,
        "include_attachments": False,
        "include_releases": False,
        "include_release_assets": False,
        "inventory_id": None,
        "inventory_digest": None,
    }


@pytest.mark.parametrize("option", ["--include-metadata", "--include-members", "--inventory-id"])
def test_exports_reject_incompatible_return_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    service = configure_source(tmp_path)
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))
    values = [
        "exports",
        "prepare",
        "--database",
        str(service.state.path),
        "--mode",
        "return",
        "--source-instance",
        "a",
        "--source-revision",
        "1",
        "--recipient-instance",
        "b",
        "--repository",
        "synthetic/selected=7",
        "--forward-package-id",
        "forward-1",
        "--forward-package-digest",
        "a" * 64,
        option,
    ]
    if option == "--inventory-id":
        values.append("inventory-1")
    assert main(values) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


@pytest.mark.parametrize("failure", [SourceSnapshotError, GiteaBundleError])
def test_exports_run_sanitizes_capture_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: type[RuntimeError],
) -> None:
    database = tmp_path / "state.sqlite3"
    (tmp_path / "packages").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))

    def fail_run(self: object, export_id: str) -> dict[str, str]:
        raise failure("secret-capture-detail")

    monkeypatch.setattr(source_export_cli.SourceExports, "run", fail_run)
    assert main(["exports", "run", "--database", str(database), "--export-id", "export-1"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "failed", "error": "Source export execution failed"}
    assert "secret-capture-detail" not in output
