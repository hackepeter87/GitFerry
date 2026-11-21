from __future__ import annotations

import json
from pathlib import Path

from gitferry.cli import main
from gitferry.state_store import OriginKey, StateStore
from test_transport_manifest import manifest


def unknown_operation(tmp_path: Path) -> tuple[Path, StateStore]:
    database = tmp_path / "state.sqlite3"
    state = StateStore(database)
    state.register_instance("instance-a", "A", "http://a.invalid", "install-a")
    state.register_instance("instance-b", "B", "http://b.invalid", "install-b")
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    state.prepare_operation(
        operation_id="op-17",
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "Synthetic", "body": "Body"},
    )
    state.start_executor_session("crashed")
    state.claim_operation("op-17", "crashed")
    state.start_executor_session("restart", recover_abandoned=True)
    return database, state


def test_cli_shows_unknown_operation_without_secrets(tmp_path: Path, capsys: object) -> None:
    database, _ = unknown_operation(tmp_path)

    exit_code = main(["state", "operation", "--database", str(database), "--operation-id", "op-17"])

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output["status"] == "outcome_unknown"
    assert output["intent_hash"]
    assert output["target"] == {"global_id": None, "issue_index": None}
    assert "reconciliation required" in output["reason"]


def test_cli_explicit_resolution_checks_evidence_and_records_target(
    tmp_path: Path, capsys: object
) -> None:
    database, state = unknown_operation(tmp_path)
    evidence_file = tmp_path / "observed.json"
    evidence_file.write_text(json.dumps({"title": "Synthetic", "body": "Body"}), encoding="utf-8")

    exit_code = main(
        [
            "state",
            "resolve-unknown",
            "--database",
            str(database),
            "--operation-id",
            "op-17",
            "--target-global-id",
            "71",
            "--target-issue-index",
            "8",
            "--observed-intent",
            str(evidence_file),
            "--evidence",
            "Fetched stable target id 71 from instance-b/org/repo",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert output == {
        "operation_id": "op-17",
        "status": "confirmed",
        "target": {"global_id": "71", "issue_index": 8},
    }
    assert state.get_operation("op-17").status == "confirmed"


def test_cli_creates_verifies_and_admits_transport(tmp_path: Path, capsys: object) -> None:
    a_database = tmp_path / "a.sqlite3"
    b_database = tmp_path / "b.sqlite3"
    for database in (a_database, b_database):
        state = StateStore(database)
        state.register_instance("instance-a", "A", "http://a.invalid", "install-a")
        state.register_instance("instance-b", "B", "http://b.invalid", "install-b")
    a = StateStore(a_database)
    origin = a.register_origin(
        OriginKey(
            "instance-a",
            "synthetic/selected",
            "issue",
            "9007199254740993",
            issue_index=3,
            origin_created_at="2026-11-20T09:00:00Z",
        )
    )
    a.add_object_mapping(
        origin.id,
        "instance-a",
        "synthetic/selected",
        "9007199254740993",
        target_issue_index=3,
        target_native_created_at="2026-11-20T09:00:00Z",
    )
    a.create_transfer_round("round-synthetic-1", "instance-a", "instance-b", "selected")
    values = {"title": "Synthetic original", "body": "Before", "state": "open", "labels": []}
    a.create_baseline(
        baseline_id="baseline-a",
        transfer_round_id="round-synthetic-1",
        origin_id=origin.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline=values,
        desired=values,
        observed=values,
        origin_created_at="2026-11-20T09:00:00Z",
        target_native_created_at="2026-11-20T09:00:00Z",
    )
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(manifest()), encoding="utf-8")
    transport = tmp_path / "transport"

    assert (
        main(
            [
                "transport",
                "create",
                "--database",
                str(a_database),
                "--local-instance",
                "instance-a",
                "--input",
                str(draft),
                "--output",
                str(transport),
            ]
        )
        == 0
    )
    created = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert created["status"] == "recorded"
    assert main(["transport", "verify", str(transport)]) == 0
    verified = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert verified["valid"] is True
    assert (
        main(
            [
                "transport",
                "admit",
                "--database",
                str(b_database),
                "--local-instance",
                "instance-b",
                str(transport),
            ]
        )
        == 0
    )
    admitted = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert admitted["status"] == "admitted"


def test_native_resolution_cli_sanitizes_remote_verification_failure(tmp_path, monkeypatch, capsys):
    from gitferry import native_reconciliation
    from gitferry.gitea_lfs import GiteaLFSError

    def unavailable(*args, **kwargs):
        raise GiteaLFSError("synthetic-private-remote-detail")

    (tmp_path / "packages").mkdir()
    (tmp_path / "work").mkdir()
    monkeypatch.setattr(native_reconciliation, "resolve_native_operation", unavailable)
    monkeypatch.setenv("GITFERRY_PACKAGE_DIR", str(tmp_path / "packages"))
    monkeypatch.setenv("GITFERRY_WORK_DIR", str(tmp_path / "work"))
    assert (
        main(
            [
                "plans",
                "resolve-native",
                "--database",
                str(tmp_path / "state.sqlite3"),
                "--operation-id",
                "native-unknown",
                "--evidence",
                "Synthetic verification",
            ]
        )
        == 2
    )
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "failed"
    assert "operation remains unresolved" in output
    assert "synthetic-private-remote-detail" not in output
