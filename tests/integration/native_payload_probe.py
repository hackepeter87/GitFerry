"""File-only native payload delivery and deterministic remote-write interruptions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from support import WORK, ProbeFailure, docker_copy, docker_service_exec

ASSERTIONS = frozenset(
    {
        "source_inventory_and_v3_capture",
        "independent_state_file_only_transfer",
        "native_lfs_digest_and_wiki_history",
        "confirmed_restart_zero_writes",
        "remote_success_before_crash",
        "lost_success_response_persistent_unknown",
        "unknown_restart_no_second_upload",
        "explicit_digest_reconciliation",
        "new_package_preserves_operation_identity",
        "negative_payload_prerequisites",
        "native_return_and_selected_ref_conflict",
    }
)


def _worker(side: str, mode: str, case: str, *, expected_exit: int = 0) -> dict[str, Any]:
    process = docker_service_exec(
        "gitferry-a" if side == "source" else "gitferry-b",
        ["python", "/probe/native_payload_worker.py", mode, "--case", case],
        check=False,
        timeout=180,
    )
    if process.returncode != expected_exit:
        # Retain only credential-free bounded diagnostics. API exception text is
        # deliberately not published; the phase identifies the failed boundary.
        record = {
            "side": side,
            "mode": mode,
            "case": case,
            "exit_code": process.returncode,
            "expected_exit": expected_exit,
            "exception_type": process.stderr.splitlines()[-1].split(":", 1)[0]
            if process.stderr
            else None,
        }
        (WORK / "native-worker-failure.json").write_text(json.dumps(record, indent=2))
        raise ProbeFailure("native payload worker failed; see native-worker-failure.json")
    if expected_exit:
        return {"exit_code": process.returncode}
    result = json.loads(process.stdout)
    (WORK / f"native-{side}-{mode}-{case}.json").write_text(json.dumps(result, indent=2))
    return result


def run_native_payload_probe() -> dict[str, Any]:
    for side in ("gitferry-a", "gitferry-b"):
        names = (
            ("native_payload_worker.py", "native_return_worker.py", "native_negative_worker.py")
            if side == "gitferry-b"
            else (
                "plan_worker.py",
                "native_payload_worker.py",
                "native_return_worker.py",
                "native_negative_worker.py",
            )
        )
        for name in names:
            docker_copy(Path(__file__).with_name(name), f"{side}:/probe/{name}")
    cases: dict[str, Any] = {}
    for case in ("normal", "crash", "lost"):
        source = _worker("source", "source", case)
        archive = WORK / f"native-payload-{case}.zip"
        docker_copy(f"gitferry-a:/state/native-payload-source-{case}/native-payload.zip", archive)
        docker_copy(archive, f"gitferry-b:/tmp/native-payload-{case}.zip")
        applied = _worker("target", "target", case, expected_exit=88 if case == "crash" else 0)
        replay = _worker("target", "replay", case)
        if replay["remote_writes"] != 0 or replay["lfs_uploads"] != 1:
            raise ProbeFailure("native replay duplicated an upload")
        repackaged = _worker("target", "repackage", case)
        cases[case] = {
            "source": source,
            "applied": applied,
            "replay": replay,
            "repackaged": repackaged,
        }
        if case != "normal":
            resolved = _worker("target", "resolve", case)
            if resolved["plan_status"] != "confirmed" or resolved["lfs_uploads"] != 1:
                raise ProbeFailure("native reconciliation failed or repeated LFS upload")
            cases[case]["resolved"] = resolved
    negatives = _worker("source", "negatives", "normal")
    returned_export = _worker("target", "return-export", "normal")
    return_directory = WORK / "native-return"
    docker_copy("gitferry-b:/state/native-payload-target-normal/return-files", return_directory)
    docker_copy(return_directory, "gitferry-a:/tmp/native-return")
    returned = _worker("source", "return-import", "normal")
    assertions = {
        "new_package_preserves_operation_identity": all(
            c["repackaged"]["remote_writes"] == 0
            and c["repackaged"]["lfs_uploads"] == 1
            and c["repackaged"]["operations"] == c["replay"]["operations"]
            for c in cases.values()
        ),
        "negative_payload_prerequisites": negatives["status"] == "passed"
        and all(negatives["assertions"].values()),
        "native_return_and_selected_ref_conflict": returned["status"] == "passed"
        and returned_export["application_return_blocked"]
        and returned["isolation_checked"]
        and returned["control_refs_unchanged"]
        and returned["returned_refs"] != returned["drift_preserved"]
        and returned["lfs_sha256"] == returned_export["files"]["lfs"]["oid"],
        "source_inventory_and_v3_capture": all(
            c["source"]["inventory_status"] == "ready" for c in cases.values()
        ),
        "independent_state_file_only_transfer": all(
            c["replay"]["source_state_absent"] and c["replay"]["isolation_checked"]
            for c in cases.values()
        ),
        "native_lfs_digest_and_wiki_history": cases["normal"]["applied"][
            "wiki_refs_and_content_match"
        ]
        and cases["normal"]["applied"]["lfs_remote_bytes_verified"],
        "confirmed_restart_zero_writes": cases["normal"]["replay"]["plan_status"] == "confirmed"
        and cases["normal"]["replay"]["remote_writes"] == 0,
        "remote_success_before_crash": cases["crash"]["applied"]["exit_code"] == 88
        and cases["crash"]["replay"]["after_remote_crash_marker"]
        and cases["crash"]["replay"]["lfs_remote_bytes_verified"],
        "lost_success_response_persistent_unknown": cases["lost"]["applied"]["response_lost_marker"]
        and cases["lost"]["replay"]["operations"]["upload_lfs_object"] == "outcome_unknown",
        "unknown_restart_no_second_upload": all(
            cases[key]["replay"]["operations"]["upload_lfs_object"] == "outcome_unknown"
            and cases[key]["replay"]["remote_writes"] == 0
            and cases[key]["replay"]["lfs_uploads"] == 1
            for key in ("crash", "lost")
        ),
        "explicit_digest_reconciliation": all(
            cases[key]["resolved"]["plan_status"] == "confirmed"
            and cases[key]["resolved"]["lfs_uploads"] == 1
            for key in ("crash", "lost")
        ),
    }
    if set(assertions) != ASSERTIONS or not all(assertions.values()):
        raise ProbeFailure("native payload assertions incomplete")
    return {
        "status": "passed",
        "gitea_version": "1.27.2",
        "assertions": assertions,
        "cases": cases,
        "negatives": negatives,
        "return_export": returned_export,
        "return_import": returned,
    }
