import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from gitferry.connection_probe import approve_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.state_store import OriginKey, StateStore
from gitferry.transfer_plans import PlanConflict, PlanService


def _store(tmp_path: Path) -> tuple[StateStore, str]:
    store = StateStore(tmp_path / "state.sqlite")
    identity = '{"account_id":"1","ca_digest":"ca","certificate_sha256":"cert","login":"bot"}'
    ProfileService(store).create(
        ConnectionProfile("target", "Target", "https://target.example", "install", "credential")
    )
    with store.transaction() as c:
        c.execute(
            "INSERT INTO connection_checks VALUES (?,?,?,?,?)",
            ("target", 1, identity, identity, "now"),
        )
    evidence = hashlib.sha256(identity.encode()).hexdigest()
    approve_connection(
        ProfileService(store), "target", expected_revision=1, evidence_digest=evidence
    )
    origin = store.register_origin(OriginKey("target", "owner/repo", "issue", "1", 1))
    store.prepare_operation(
        operation_id="op-1",
        origin_id=origin.id,
        target_instance_key="target",
        target_repository_scope="owner/repo",
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "hello"},
    )
    return store, evidence


def _plan(service: PlanService, evidence: str, **changes: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = dict(
        target_instance_key="target",
        profile_revision=1,
        evidence_digest=evidence,
        round_key="round-1",
        inventory={"count": 1},
        operation_ids=["op-1"],
        dependencies={"op-1": []},
    )
    return service.create(**{**arguments, **changes})


def test_create_and_queue_is_durable(tmp_path):
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = service.create(
        target_instance_key="target",
        profile_revision=1,
        evidence_digest=evidence,
        round_key="round-1",
        inventory={"count": 1},
        operation_ids=["op-1"],
        dependencies={"op-1": []},
    )
    assert plan["status"] == "draft"
    assert service.queue(plan["plan_id"], plan["digest"])["status"] == "queued"
    assert (
        PlanService(StateStore(store.path)).get(plan["plan_id"])["operations"][0]["operation_id"]
        == "op-1"
    )


def test_cycles_and_stale_evidence_do_not_mutate(tmp_path):
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    with pytest.raises(PlanConflict):
        service.create(
            target_instance_key="target",
            profile_revision=1,
            evidence_digest=evidence,
            round_key="round-1",
            inventory={},
            operation_ids=["op-1"],
            dependencies={"op-1": ["op-1"]},
        )
    with pytest.raises(PlanConflict):
        service.create(
            target_instance_key="target",
            profile_revision=1,
            evidence_digest="0" * 64,
            round_key="round-1",
            inventory={},
            operation_ids=["op-1"],
            dependencies={"op-1": []},
        )
    assert service.list() == []


def test_real_process_restart_and_changed_plan_input(tmp_path: Path) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    result = subprocess.run(  # noqa: S603 -- fixed synthetic child program, argv carries local test paths
        [
            sys.executable,
            "-c",
            "import json,sys; from gitferry.transfer_plans import PlanService; "
            "from gitferry.state_store import StateStore; "
            "print(json.dumps(PlanService(StateStore(sys.argv[1])).get(sys.argv[2])))",
            str(store.path),
            plan["plan_id"],
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == plan
    assert _plan(service, evidence)["plan_id"] == plan["plan_id"]
    changed = _plan(service, evidence, inventory={"count": 1, "excluded": ["attachments"]})
    assert changed["plan_id"] != plan["plan_id"]
    assert service.get(plan["plan_id"])["document"] == plan["document"]


def test_concurrent_publication_and_atomic_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _plan(service, evidence), range(2)))
    assert results[0]["plan_id"] == results[1]["plan_id"]
    assert len(service.list()) == 1

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("synthetic publication fault")

    monkeypatch.setattr(service, "_read", fail)
    with pytest.raises(RuntimeError):
        _plan(service, evidence, round_key="round-2")
    with store.read_connection() as c:
        assert c.execute("SELECT count(*) FROM transfer_plans").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM plan_steps").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM plan_events").fetchone()[0] == 1


def test_observation_never_replaces_baseline_or_changes_plan(tmp_path: Path) -> None:
    store, evidence = _store(tmp_path)
    origin = store.get_operation("op-1").origin_id
    store.create_transfer_round("baseline-round", "target", "target", "owner/repo")
    store.create_baseline(
        baseline_id="base",
        transfer_round_id="baseline-round",
        origin_id=origin,
        ruleset_name="issue-fields",
        ruleset_version=1,
        baseline={"title": "original"},
        desired={"title": "desired"},
        observed={"title": "original"},
        origin_created_at=None,
        target_native_created_at=None,
    )
    store.prepare_operation(
        operation_id="update",
        origin_id=origin,
        target_instance_key="target",
        target_repository_scope="owner/repo",
        operation_type="update_issue",
        mapping_version=1,
        baseline_id="base",
        intent={"title": "desired"},
    )
    service = PlanService(store)
    args = {"operation_ids": ["update"], "dependencies": {"update": []}}
    plan = _plan(service, evidence, **args)
    store.update_baseline_observation("base", {"title": "unrelated observation"})
    assert _plan(service, evidence, **args)["plan_id"] == plan["plan_id"]
    assert service.queue(plan["plan_id"], plan["digest"])["status"] == "queued"
    assert store.get_baseline("base").baseline == {"title": "original"}


def test_stale_profile_cancel_and_no_success_override(tmp_path: Path) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    service.queue(plan["plan_id"], plan["digest"])
    service.begin(plan["plan_id"])
    service.finish(plan["plan_id"], "running")
    with pytest.raises(PlanConflict):
        service.finish(plan["plan_id"], "confirmed")
    cancelled = service.cancel(plan["plan_id"])
    assert cancelled["status"] == "running" and cancelled["cancel_requested"]
    assert store.get_operation("op-1").status == "prepared"
    assert service.begin(plan["plan_id"])["status"] == "cancelled"
    ProfileService(store).retire("target", expected_revision=1)
    with pytest.raises(PlanConflict):
        service.queue(plan["plan_id"], plan["digest"])


@pytest.mark.parametrize("column", ["routing", "step"])
def test_inconsistent_stored_projection_is_rejected(tmp_path: Path, column: str) -> None:
    store, evidence = _store(tmp_path)
    service = PlanService(store)
    plan = _plan(service, evidence)
    with store.transaction() as connection:
        if column == "routing":
            connection.execute("UPDATE transfer_plans SET round_key='changed'")
        else:
            connection.execute("UPDATE plan_steps SET snapshot_json='{}'")
    with pytest.raises(PlanConflict):
        service.get(plan["plan_id"])
