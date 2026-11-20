from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_outer_recovery_covers_primary_and_failure_contract_projects(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    primary = results_root / "integration-results"
    failure = results_root / "integration-failure-results"
    for directory, project, image in (
        (primary, "gitferry-it-primary", "gitferry-it-runtime:primary"),
        (failure, "gitferry-it-failure", "gitferry-it-runtime:failure"),
    ):
        directory.mkdir(parents=True)
        (directory / "project-name").write_text(project + "\n", encoding="utf-8")
        (directory / "runtime-image").write_text(image + "\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\n', encoding="utf-8")
    docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(docker_log),
        "GITFERRY_IT_RESULTS_ROOT": str(results_root),
        "GITFERRY_IT_PROJECT": "gitferry-it-primary",
        "GITFERRY_IT_RUNTIME_IMAGE": "gitferry-it-runtime:primary",
    }

    subprocess.run(  # noqa: S603 - repository script path and arguments are fixed
        [ROOT / "tests/integration/capture_diagnostics.sh"], check=True, env=env
    )
    for directory in (primary, failure):
        assert (directory / "workflow-diagnostics/compose-ps.json").is_file()
        assert (directory / "workflow-diagnostics/compose-images.json").is_file()
        assert (directory / "workflow-diagnostics/compose.log").is_file()

    docker_log.write_text("", encoding="utf-8")
    subprocess.run(  # noqa: S603 - repository script path and arguments are fixed
        [ROOT / "tests/integration/clean.sh"], check=True, env=env
    )
    calls = docker_log.read_text(encoding="utf-8")
    assert calls.count("compose -p gitferry-it-primary ") == 1
    assert calls.count("compose -p gitferry-it-failure ") == 1
    assert calls.count("image rm gitferry-it-runtime:primary") == 1
    assert calls.count("image rm gitferry-it-runtime:failure") == 1
    for project in ("gitferry-it-primary", "gitferry-it-failure"):
        label = f"label=gitferry.integration.project={project}"
        assert f"ps --all --quiet --filter {label}" in calls
        assert f"volume ls --quiet --filter {label}" in calls
