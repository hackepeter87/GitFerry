from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def _run_stage(results: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script and arguments
        [
            sys.executable,
            ROOT / "tests/integration/stage_artifacts.py",
            "--results-root",
            results,
            "--output",
            output,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_staging_allowlist_redacts_secrets_and_excludes_payloads(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    results = results_root / "integration-results"
    (results / "workflow-diagnostics").mkdir(parents=True)
    (results / "work").mkdir()
    (results / "project-name").write_text("gitferry-it-test\n", encoding="utf-8")
    (results / "report.json").write_text(
        '{"status":"failed","nested":{"api_token":"hidden-json",'
        '"api_tokens":["hidden-list"],"authorization":{"value":"hidden-object"},'
        '"session_count":3,"message":"Authorization: Bearer embedded-hidden"}}\n',
        encoding="utf-8",
    )
    (results / "gitea-upgrade-report.json").write_text(
        '{"status":"passed","source":{"version":"1.27.2"},"target":{"version":"28.4.0"}}\n',
        encoding="utf-8",
    )
    (results / "workflow-diagnostics/compose.log").write_text(
        "Authorization: Bearer hidden\nCookie: session=hidden\n"
        "password=hidden\n-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    (results / "work/browser-phase0.log").write_text(
        "  - cookie: session=browser-secret; gitferry_csrf=also-hidden\nfailed\n",
        encoding="utf-8",
    )
    (results / "work/application-plan-tls/trusted.key").parent.mkdir()
    (results / "work/application-plan-tls/trusted.key").write_text("PRIVATE", encoding="utf-8")
    (results / "package.zip").write_bytes(b"payload")
    output = results_root / "integration-artifacts"

    completed = _run_stage(results_root, output)

    assert completed.returncode == 0, completed.stderr
    published = "\n".join(
        path.read_text(encoding="utf-8") for path in output.rglob("*") if path.is_file()
    )
    assert "hidden" not in published
    assert "browser-secret" not in published
    assert "also-hidden" not in published
    assert "embedded-hidden" not in published
    assert "hidden-list" not in published
    assert "hidden-object" not in published
    assert "BEGIN PRIVATE KEY" not in published
    assert not (output / "integration-results/work/application-plan-tls/trusted.key").exists()
    assert not (output / "integration-results/package.zip").exists()
    assert "[REDACTED]" in published
    report = json.loads((output / "integration-results/report.json").read_text(encoding="utf-8"))
    assert report["nested"]["api_token"] == "[REDACTED]"  # noqa: S105 -- marker, not a password
    assert report["nested"]["api_tokens"] == "[REDACTED]"  # noqa: S105 -- marker, not a password
    assert report["nested"]["authorization"] == "[REDACTED]"  # noqa: S105 -- marker
    assert report["nested"]["session_count"] == 3
    assert report["nested"]["message"] == "Authorization: [REDACTED]"
    upgrade = json.loads(
        (output / "integration-results/gitea-upgrade-report.json").read_text(encoding="utf-8")
    )
    assert upgrade["status"] == "passed"


def test_staging_fails_without_results(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    completed = _run_stage(missing, missing / "integration-artifacts")

    assert completed.returncode != 0
    assert "no integration result directory" in completed.stderr


def test_staging_rejects_allowlisted_symbolic_link(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    results = results_root / "integration-results"
    results.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("sensitive", encoding="utf-8")
    (results / "report.json").symlink_to(outside)

    completed = _run_stage(results_root, results_root / "integration-artifacts")

    assert completed.returncode != 0
    assert "symbolic link" in completed.stderr


def test_staging_rejects_allowlisted_hardlink(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    work = results_root / "integration-results/work"
    work.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_text("sensitive", encoding="utf-8")
    (work / "browser-leak.log").hardlink_to(outside)

    completed = _run_stage(results_root, results_root / "integration-artifacts")

    assert completed.returncode != 0
    assert "hard-linked" in completed.stderr


def test_staging_rejects_unscoped_output_without_deleting_it(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    (results_root / "integration-results").mkdir(parents=True)
    unsafe = tmp_path / "unrelated"
    unsafe.mkdir()
    marker = unsafe / "keep"
    marker.write_text("preserved", encoding="utf-8")

    completed = _run_stage(results_root, unsafe)

    assert completed.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserved"
