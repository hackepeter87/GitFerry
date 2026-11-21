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
