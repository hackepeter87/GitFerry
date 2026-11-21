from __future__ import annotations

import json
from pathlib import Path

from gitferry.package_admission_cli import configure, execute


class _Admissions:
    instances: list[_Admissions] = []

    def __init__(self, state, paths) -> None:
        self.paths = paths
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.instances.append(self)

    def plan_review(self, package_ref: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("plan-review", package_ref, kwargs))
        return {"status": "reviewed"}

    def list(self) -> list[dict[str, object]]:
        self.calls.append(("list", "", {}))
        return [{"package_ref": "synthetic-package"}]


def test_cli_routes_plan_review_and_uses_environment_paths(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from argparse import ArgumentParser

    database = tmp_path / "state.sqlite3"
    package_dir = tmp_path / "packages"
    work_dir = tmp_path / "work"
    package_dir.mkdir()
    work_dir.mkdir()
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(package_dir))
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(work_dir))
    monkeypatch.setattr("gitferry.package_admission_cli.PackageAdmissions", _Admissions)
    parser = ArgumentParser()
    configure(parser, None)
    args = parser.parse_args(
        [
            "plan-review",
            "--database",
            str(database),
            "--package-ref",
            "synthetic-package",
            "--target-instance",
            "synthetic-target",
            "--profile-revision",
            "3",
            "--exclude-metadata",
            "--apply-members",
            "--author-credential",
            "binding-a=credential-a",
        ]
    )
    assert execute(args) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "reviewed"}
    instance = _Admissions.instances[-1]
    assert instance.paths.package_directory == package_dir
    assert instance.paths.work_directory == work_dir
    assert instance.calls == [
        (
            "plan-review",
            "synthetic-package",
            {
                "target_instance_key": "synthetic-target",
                "profile_revision": 3,
                "include_metadata": False,
                "apply_members": True,
                "author_credential_refs": {"binding-a": "credential-a"},
            },
        )
    ]


def test_cli_rejects_invalid_credential_binding_without_secrets(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from argparse import ArgumentParser

    monkeypatch.setattr("gitferry.package_admission_cli.PackageAdmissions", _Admissions)
    parser = ArgumentParser()
    configure(parser, str(tmp_path / "state.sqlite3"))
    args = parser.parse_args(
        [
            "plan-review",
            "--package-ref",
            "p",
            "--target-instance",
            "t",
            "--profile-revision",
            "1",
            "--author-credential",
            "not-a-binding",
        ]
    )
    assert execute(args) == 2
    output = capsys.readouterr().out
    assert "not-a-binding" not in output
    assert "invalid or unavailable" in output
