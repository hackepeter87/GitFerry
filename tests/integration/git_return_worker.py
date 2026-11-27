"""Real file-only Git return using the two independent GitFerry state stores."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import plan_worker
from gitferry.connection_probe import LocalReferences
from gitferry.git_return_operations import reconcile_git_ref_operation
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.operations import OperationBlocked, OperationExecutor
from gitferry.package_admission import PackageAdmissions
from gitferry.package_archive import receive_package_archive
from gitferry.plan_diagnostics import PreflightBlocked, PreflightCheck
from gitferry.plan_runner import PlanRunner
from gitferry.runtime import RuntimePaths
from gitferry.source_export_jobs import SourceExportJobs
from gitferry.state_store import StateStore
from gitferry.transfer_plans import PlanService

REPOSITORY = "source-export-lab/selected"
CONTROL_REPOSITORY = "source-export-lab/not-selected"
A_ROOT = Path("/state/source-export")
B_ROOT = Path("/state/package-admission")
A_URL = "http://source:3000"
B_URL = "http://target:3000"
B_RETURN_ARCHIVE = B_ROOT / "git-return-real.zip"
A_RETURN_ARCHIVE = A_ROOT / "git-return-real.zip"


class DiagnosticReturnAdapter(GiteaPlanAdapter):
    """Keep the closed product status while retaining synthetic lab diagnostics."""

    preflight_failure: str | None = None

    def preflight(self, plan: dict[str, Any]) -> None:
        self.check = PreflightCheck.SELECTION
        self.scope = None
        try:
            self._preflight(plan)
        except Exception as exc:
            self.preflight_failure = f"{type(exc).__name__}: {exc}"
            raise PreflightBlocked(self.check, self.scope) from None


def _paths(root: Path) -> RuntimePaths:
    database = root / "state/state.sqlite3"
    return RuntimePaths(database, root / "packages", root / "work")


@contextmanager
def _git_environment(root: Path) -> Iterator[dict[str, str]]:
    script = root / "git-return-askpass.sh"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  *Username*) printf "%s\\n" "gitferry-admin" ;;\n'
        '  *) printf "%s\\n" "$GITFERRY_RETURN_PASSWORD" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    environment = {
        **os.environ,
        "GIT_ASKPASS": str(script),
        "GIT_ASKPASS_REQUIRE": "force",
        "GIT_TERMINAL_PROMPT": "0",
        "GITFERRY_RETURN_PASSWORD": os.environ["GITFERRY_IT_ADMIN_PASSWORD"],
    }
    try:
        yield environment
    finally:
        script.unlink(missing_ok=True)


def _git_refs(root: Path, base_url: str, repository: str) -> dict[str, str]:
    with _git_environment(root) as environment:
        output = subprocess.run(  # noqa: S603
            [
                shutil.which("git") or "/usr/bin/git",
                "ls-remote",
                "--refs",
                f"{base_url}/{repository}.git",
            ],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    return {
        ref: oid
        for line in output.splitlines()
        for oid, ref in [line.split("\t", 1)]
        if ref.startswith("refs/")
    }


def _clone_and_verify_return(
    root: Path,
    base_url: str,
    repository: str,
    ref: str,
    oid: str,
    *,
    content_oid: str,
) -> bool:
    """Verify the reconciled ref and offline content through one real clone."""

    with tempfile.TemporaryDirectory(prefix="gitferry-return-clone-", dir=root / "work") as raw:
        clone = Path(raw) / "repository"
        with _git_environment(root) as environment:
            subprocess.run(  # noqa: S603
                [
                    shutil.which("git") or "/usr/bin/git",
                    "clone",
                    "--no-checkout",
                    f"{base_url}/{repository}.git",
                    str(clone),
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(  # noqa: S603
                [shutil.which("git") or "/usr/bin/git", "cat-file", "-e", f"{oid}^{{commit}}"],
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            )
            content = subprocess.run(  # noqa: S603
                [
                    shutil.which("git") or "/usr/bin/git",
                    "show",
                    "--format=",
                    f"{content_oid}:OFFLINE-RETURN.txt",
                ],
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            )
            if content.stdout != "synthetic offline return\n":
                raise RuntimeError("returned clone content differs from the offline commit")
            result = subprocess.run(  # noqa: S603
                [shutil.which("git") or "/usr/bin/git", "ls-remote", "--refs", "origin", ref],
                cwd=clone,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
        return any(line.split("\t", 1) == [oid, ref] for line in result.stdout.splitlines())


def _unreachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return False
    except OSError:
        return True


def _admin_repository(base_url: str) -> dict[str, Any]:
    response = httpx.get(
        f"{base_url}/api/v1/repos/{REPOSITORY}",
        auth=("gitferry-admin", os.environ["GITFERRY_IT_ADMIN_PASSWORD"]),
        timeout=20,
        trust_env=False,
    )
    response.raise_for_status()
    return dict(response.json())


def _git_set_return_ref(root: Path, base_url: str, *, delete: bool = False) -> None:
    """Create/remove only the synthetic selected ref used by the drift probe."""

    with tempfile.TemporaryDirectory(prefix="gitferry-drift-", dir=root / "work") as raw:
        clone = Path(raw) / "repository"
        with _git_environment(root) as environment:
            subprocess.run(
                [
                    shutil.which("git") or "/usr/bin/git",
                    "clone",
                    f"{base_url}/{REPOSITORY}.git",
                    str(clone),
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            refspec = (
                ":refs/heads/offline-return"
                if delete
                else "refs/heads/main:refs/heads/offline-return"
            )
            subprocess.run(
                [shutil.which("git") or "/usr/bin/git", "push", "origin", refspec],
                env=environment,
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            )


def _set_branch_protection(base_url: str, *, enabled: bool) -> None:
    path = f"{base_url}/api/v1/repos/{REPOSITORY}/branch_protections"
    auth = ("gitferry-admin", os.environ["GITFERRY_IT_ADMIN_PASSWORD"])
    if enabled:
        response = httpx.post(
            path,
            auth=auth,
            json={"rule_name": "offline-return", "enable_push": False},
            timeout=20,
            trust_env=False,
        )
        response.raise_for_status()
    else:
        response = httpx.delete(f"{path}/offline-return", auth=auth, timeout=20, trust_env=False)
        if response.status_code not in (200, 204, 404):
            response.raise_for_status()
    deadline = time.monotonic() + 10
    while True:
        observed = httpx.get(path, auth=auth, timeout=20, trust_env=False)
        observed.raise_for_status()
        rules = observed.json()
        if not isinstance(rules, list):
            raise RuntimeError("branch protection verification returned a non-list response")
        present = any(
            isinstance(rule, dict)
            and (rule.get("rule_name") or rule.get("branch_name")) == "offline-return"
            for rule in rules
        )
        if present is enabled:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("branch protection state did not reach the requested value")
        time.sleep(0.1)


def _create_offline_commit() -> dict[str, str]:
    before = _git_refs(B_ROOT, B_URL, REPOSITORY)
    if "refs/heads/offline-return" in before:
        return before
    with tempfile.TemporaryDirectory(prefix="gitferry-return-", dir=B_ROOT / "work") as raw:
        clone = Path(raw) / "repository"
        with _git_environment(B_ROOT) as environment:
            subprocess.run(  # noqa: S603
                [
                    shutil.which("git") or "/usr/bin/git",
                    "clone",
                    f"{B_URL}/{REPOSITORY}.git",
                    str(clone),
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(  # noqa: S603
                [shutil.which("git") or "/usr/bin/git", "config", "user.name", "Offline User"],
                cwd=clone,
                check=True,
            )
            subprocess.run(  # noqa: S603
                [
                    shutil.which("git") or "/usr/bin/git",
                    "config",
                    "user.email",
                    "offline@example.invalid",
                ],
                cwd=clone,
                check=True,
            )
            subprocess.run(  # noqa: S603
                [shutil.which("git") or "/usr/bin/git", "switch", "-c", "offline-return"],
                cwd=clone,
                check=True,
            )
            (clone / "OFFLINE-RETURN.txt").write_text(
                "synthetic offline return\n", encoding="utf-8"
            )
            subprocess.run(  # noqa: S603
                [shutil.which("git") or "/usr/bin/git", "add", "OFFLINE-RETURN.txt"],
                cwd=clone,
                check=True,
            )
            subprocess.run(  # noqa: S603
                [shutil.which("git") or "/usr/bin/git", "commit", "-m", "Offline return"],
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(  # noqa: S603
                [
                    shutil.which("git") or "/usr/bin/git",
                    "push",
                    "origin",
                    "HEAD:refs/heads/offline-return",
                ],
                env=environment,
                cwd=clone,
                check=True,
                capture_output=True,
                text=True,
            )
    return _git_refs(B_ROOT, B_URL, REPOSITORY)


def export_return() -> dict[str, Any]:
    paths = _paths(B_ROOT)
    state = StateStore(paths.state_database)
    with state.read_connection() as connection:
        receipt = connection.execute(
            "SELECT package_id,digest_sha256 FROM package_receipts ORDER BY received_at LIMIT 1"
        ).fetchone()
    if receipt is None:
        raise RuntimeError("forward package receipt is missing on B")
    current = _create_offline_commit()
    repository = _admin_repository(B_URL)
    request = {
        "version": 5,
        "mode": "return",
        "forward_package_id": receipt["package_id"],
        "forward_package_digest": receipt["digest_sha256"],
        "source_instance_key": "target",
        "source_profile_revision": 1,
        "recipient_instance_key": "source",
        "repositories": [{"repository": REPOSITORY, "global_id": str(repository["id"])}],
        "include_metadata": False,
        "include_members": False,
    }
    jobs = SourceExportJobs(state, paths, LocalReferences(B_ROOT / "secrets", B_ROOT))
    capture_failure: list[str] = []
    run_owned = jobs.exports._run_owned

    def observed_run_owned(export_id: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return run_owned(export_id, **kwargs)
        except Exception as error:
            capture_failure.append(f"{type(error).__name__}: {error}")
            raise

    jobs.exports._run_owned = observed_run_owned  # type: ignore[method-assign]
    plan_worker.ROOT = B_ROOT
    plan_worker.URL = B_URL
    with plan_worker.proxy(preserve_origin=True):
        jobs.create("git-return-real", request)
        result = jobs.run_next()
    if result is None or result["status"] != "ready":
        detail = capture_failure[-1] if capture_failure else "no capture exception recorded"
        raise RuntimeError(f"return export did not reach ready: {detail}")
    stream, details = jobs.open_download("git-return-real")
    with stream, B_RETURN_ARCHIVE.open("wb") as output:
        shutil.copyfileobj(stream, output)
    return {
        "status": details["status"],
        "package_digest": details["package_digest"],
        "archive_sha256": details["archive_sha256"],
        "offline_oid": current["refs/heads/offline-return"],
        "source_unreachable": _unreachable("source", 3000),
    }


def apply_return() -> dict[str, Any]:
    paths = _paths(A_ROOT)
    state = StateStore(paths.state_database)
    package_ref = "received-git-return"
    destination = paths.package_directory / package_ref
    if not destination.exists():
        receive_package_archive(A_RETURN_ARCHIVE, destination)
    admissions = PackageAdmissions(state, paths)
    artifact = admissions.artifact(package_ref)
    admissions.admit(
        package_ref,
        target_instance_key="source",
        profile_revision=1,
        expected_digest=artifact.digest_sha256,
    )
    review = admissions.plan_review(
        package_ref,
        target_instance_key="source",
        profile_revision=1,
        include_metadata=False,
    )
    plan = admissions.prepare_plan(
        package_ref,
        target_instance_key="source",
        profile_revision=1,
        expected_review_digest=review["review_digest"],
        include_metadata=False,
    )
    service = PlanService(state)
    service.queue(plan["plan_id"], plan["digest"])
    before_control = _git_refs(A_ROOT, A_URL, CONTROL_REPOSITORY)
    plan_worker.ROOT = A_ROOT
    plan_worker.URL = A_URL
    adapters: list[DiagnosticReturnAdapter] = []

    def adapter(_: dict[str, Any]) -> DiagnosticReturnAdapter:
        created = DiagnosticReturnAdapter(
            state,
            paths,
            LocalReferences(A_ROOT / "secrets", A_ROOT / "ca"),
        )
        adapters.append(created)
        return created

    with plan_worker.proxy(preserve_origin=True):
        result = PlanRunner(service, adapter).run(plan["plan_id"])
    if result["status"] != "confirmed":
        diagnostic = adapters[-1].preflight_failure if adapters else "adapter was not created"
        raise RuntimeError(
            f"return application ended as {result['status']}: {result.get('reason')}; "
            f"synthetic preflight diagnostic: {diagnostic}"
        )
    after = _git_refs(A_ROOT, A_URL, REPOSITORY)
    after_control = _git_refs(A_ROOT, A_URL, CONTROL_REPOSITORY)
    with state.read_connection() as connection:
        executing = connection.execute(
            "SELECT count(*) FROM operation_events e JOIN operations o "
            "ON o.operation_id=e.operation_id "
            "WHERE o.operation_type='push_git_ref' AND e.to_status='executing'"
        ).fetchone()[0]
    return {
        "status": result["status"],
        "plan_id": plan["plan_id"],
        "return_digest": artifact.digest_sha256,
        "refs": after,
        "control_unchanged": before_control == after_control,
        "push_attempts": executing,
        "target_unreachable": _unreachable("target", 3000),
    }


def _prepare_return_plan() -> tuple[StateStore, RuntimePaths, PlanService, dict[str, Any], Any]:
    paths = _paths(A_ROOT)
    state = StateStore(paths.state_database)
    package_ref = "received-git-return"
    destination = paths.package_directory / package_ref
    if not destination.exists():
        receive_package_archive(A_RETURN_ARCHIVE, destination)
    admissions = PackageAdmissions(state, paths)
    artifact = admissions.artifact(package_ref)
    admissions.admit(
        package_ref,
        target_instance_key="source",
        profile_revision=1,
        expected_digest=artifact.digest_sha256,
    )
    review = admissions.plan_review(
        package_ref, target_instance_key="source", profile_revision=1, include_metadata=False
    )
    plan = admissions.prepare_plan(
        package_ref,
        target_instance_key="source",
        profile_revision=1,
        expected_review_digest=review["review_digest"],
        include_metadata=False,
    )
    service = PlanService(state)
    return state, paths, service, plan, artifact


def _run_return_plan(
    state: StateStore,
    paths: RuntimePaths,
    service: PlanService,
    plan: dict[str, Any],
    *,
    crash_after_remote: bool = False,
) -> dict[str, Any]:
    service.queue(plan["plan_id"], plan["digest"])
    plan_worker.ROOT = A_ROOT
    plan_worker.URL = A_URL

    adapters: list[DiagnosticReturnAdapter] = []

    def adapter(_: dict[str, Any]) -> DiagnosticReturnAdapter:
        created = DiagnosticReturnAdapter(
            state, paths, LocalReferences(A_ROOT / "secrets", A_ROOT / "ca")
        )
        adapters.append(created)
        return created

    def crash() -> None:
        if crash_after_remote:
            os._exit(42)

    with plan_worker.proxy(preserve_origin=True):
        result = PlanRunner(
            service, adapter, after_remote_hook=crash if crash_after_remote else None
        ).run(plan["plan_id"])
    if crash_after_remote:
        diagnostic = adapters[-1].preflight_failure if adapters else "adapter was not created"
        raise RuntimeError(
            "crash hook was not reached: "
            f"plan={result['status']} reason={result.get('reason')} preflight={diagnostic}"
        )
    return result


def drift_return() -> dict[str, Any]:
    state, paths, service, plan, _ = _prepare_return_plan()
    control = _git_refs(A_ROOT, A_URL, CONTROL_REPOSITORY)
    _git_set_return_ref(A_ROOT, A_URL)
    drifted = _git_refs(A_ROOT, A_URL, REPOSITORY)
    try:
        result = _run_return_plan(state, paths, service, plan)
        after = _git_refs(A_ROOT, A_URL, REPOSITORY)
    finally:
        _git_set_return_ref(A_ROOT, A_URL, delete=True)
    restored = _git_refs(A_ROOT, A_URL, REPOSITORY)
    return {
        "status": result["status"],
        "reason": result.get("reason"),
        "no_push": after == drifted,
        "cleanup_restored": "refs/heads/offline-return" not in restored,
        "control_unchanged": control == _git_refs(A_ROOT, A_URL, CONTROL_REPOSITORY),
        "target_unreachable": _unreachable("target", 3000),
    }


def protected_return() -> dict[str, Any]:
    state, paths, service, plan, _ = _prepare_return_plan()
    before = _git_refs(A_ROOT, A_URL, REPOSITORY)
    control = _git_refs(A_ROOT, A_URL, CONTROL_REPOSITORY)
    _set_branch_protection(A_URL, enabled=True)
    try:
        result = _run_return_plan(state, paths, service, plan)
        after = _git_refs(A_ROOT, A_URL, REPOSITORY)
    finally:
        _set_branch_protection(A_URL, enabled=False)
    return {
        "status": result["status"],
        "reason": result.get("reason"),
        "no_push": after == before,
        "control_unchanged": control == _git_refs(A_ROOT, A_URL, CONTROL_REPOSITORY),
        "target_unchanged": before == after,
    }


def crash_return() -> dict[str, Any]:
    state, paths, service, plan, _ = _prepare_return_plan()
    _run_return_plan(state, paths, service, plan, crash_after_remote=True)
    raise RuntimeError("crash hook unexpectedly returned")


def recover_return() -> dict[str, Any]:
    paths = _paths(A_ROOT)
    state = StateStore(paths.state_database)
    with OperationExecutor(state, "git-return-recovery"):
        pass
    with state.read_connection() as connection:
        unknown = connection.execute(
            "SELECT operation_id FROM operations WHERE operation_type='push_git_ref' "
            "AND status='outcome_unknown' ORDER BY operation_id"
        ).fetchall()
    if len(unknown) != 1:
        raise RuntimeError(f"expected one interrupted Git return operation, got {len(unknown)}")
    operation_id = unknown[0][0]
    operation = state.get_operation(operation_id)
    if operation.status != "outcome_unknown":
        raise RuntimeError(f"expected unknown operation after crash, got {operation.status}")
    repository = _admin_repository(A_URL)
    refs = _git_refs(A_ROOT, A_URL, REPOSITORY)
    live = {
        "target_instance_key": "source",
        "repository_id": str(repository["id"]),
        "repository_scope": REPOSITORY,
        "refs": refs,
    }
    rejected: dict[str, bool] = {}
    for name, candidate in {
        "instance": {**live, "target_instance_key": "wrong"},
        "repository": {**live, "repository_id": "wrong"},
        "scope": {**live, "repository_scope": "wrong/repository"},
        "ref": {**live, "refs": {"refs/heads/other": operation.intent["new_oid"]}},
        "oid": {**live, "refs": {operation.intent["ref"]: "0" * 40}},
    }.items():
        try:
            reconcile_git_ref_operation(
                state,
                operation.operation_id,
                lambda candidate=candidate: candidate,
                evidence=f"wrong {name} rejected",
            )
        except OperationBlocked:
            rejected[name] = True
    reconcile_git_ref_operation(
        state, operation.operation_id, lambda: live, evidence="live exact Git ref"
    )
    with state.read_connection() as connection:
        plan_id = connection.execute(
            "SELECT plan_id FROM plan_steps WHERE operation_id=?", (operation.operation_id,)
        ).fetchone()[0]
    service = PlanService(state)
    plan = service.get(plan_id)
    service.finish(plan_id, "blocked", "reconciled unknown; resuming plan")
    service.queue(plan_id, plan["digest"])
    plan_worker.ROOT = A_ROOT
    plan_worker.URL = A_URL

    def adapter(_: dict[str, Any]) -> DiagnosticReturnAdapter:
        return DiagnosticReturnAdapter(
            state, paths, LocalReferences(A_ROOT / "secrets", A_ROOT / "ca")
        )

    with plan_worker.proxy(preserve_origin=True):
        resumed = PlanRunner(service, adapter).run(plan_id)
    final_refs = _git_refs(A_ROOT, A_URL, REPOSITORY)
    offline_oid = final_refs.get("refs/heads/offline-return")
    if offline_oid is None:
        raise RuntimeError("returned offline content ref is missing")
    clone_reachable = _clone_and_verify_return(
        A_ROOT,
        A_URL,
        REPOSITORY,
        operation.intent["ref"],
        str(operation.intent["new_oid"]),
        content_oid=offline_oid,
    )
    with state.read_connection() as connection:
        push_attempts = connection.execute(
            "SELECT count(*) FROM operation_events e JOIN operations o "
            "ON o.operation_id=e.operation_id WHERE o.operation_type='push_git_ref' "
            "AND e.to_status='executing'"
        ).fetchone()[0]
    return {
        "status": resumed["status"],
        "restart_status": operation.status,
        "wrong_target_rejected": all(rejected.values()),
        "rejected": rejected,
        "remote_ref": operation.intent["ref"],
        "remote_oid": refs.get(operation.intent["ref"]),
        "intended_oid": operation.intent["new_oid"],
        "final_refs": final_refs,
        "clone_reachable": clone_reachable,
        "return_digest": PackageAdmissions(state, paths)
        .artifact("received-git-return")
        .digest_sha256,
        "push_attempts": push_attempts,
    }


def replay_return() -> dict[str, Any]:
    paths = _paths(A_ROOT)
    state = StateStore(paths.state_database)
    service = PlanService(state)
    plans = service.list()
    return_plans = [
        plan for plan in plans if plan["document"]["inventory"]["request"]["mode"] == "return"
    ]
    if len(return_plans) != 1:
        raise RuntimeError("expected exactly one durable return plan")
    existing = return_plans[0]
    before = _git_refs(A_ROOT, A_URL, REPOSITORY)
    admissions = PackageAdmissions(state, paths)
    artifact = admissions.artifact("received-git-return")
    admissions.admit(
        "received-git-return",
        target_instance_key="source",
        profile_revision=1,
        expected_digest=artifact.digest_sha256,
    )
    review = admissions.plan_review(
        "received-git-return",
        target_instance_key="source",
        profile_revision=1,
        include_metadata=False,
    )
    replayed = admissions.prepare_plan(
        "received-git-return",
        target_instance_key="source",
        profile_revision=1,
        expected_review_digest=review["review_digest"],
        include_metadata=False,
    )
    if replayed["plan_id"] != existing["plan_id"] or replayed["status"] != "confirmed":
        raise RuntimeError(
            "restart replay did not resolve to the confirmed return plan: "
            f"existing={existing['plan_id']}:{existing['status']} "
            f"replayed={replayed['plan_id']}:{replayed['status']} "
            f"reason={existing.get('reason')} "
            f"existing_digest={existing['document']['inventory']['request'].get('package_digest')} "
            f"artifact_digest={artifact.digest_sha256}"
        )
    after = _git_refs(A_ROOT, A_URL, REPOSITORY)
    with state.read_connection() as connection:
        executing = connection.execute(
            "SELECT count(*) FROM operation_events e JOIN operations o "
            "ON o.operation_id=e.operation_id "
            "WHERE o.operation_type='push_git_ref' AND e.to_status='executing'"
        ).fetchone()[0]
    return {
        "status": replayed["status"],
        "same_refs": before == after,
        "push_attempts": executing,
    }


def main() -> None:
    actions = {
        "export-b": export_return,
        "apply-a": apply_return,
        "replay-a": replay_return,
        "drift-a": drift_return,
        "protected-a": protected_return,
        "crash-a": crash_return,
        "recover-a": recover_return,
    }
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=actions)
    args = parser.parse_args()
    try:
        print(json.dumps(actions[args.mode](), sort_keys=True))
    except Exception as error:
        evidence = {
            "exception_type": type(error).__name__,
            "message": str(error),
            "frames": [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(error.__traceback__)
            ],
        }
        print("GITFERRY_PROBE_ERROR " + json.dumps(evidence), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
