from __future__ import annotations

from pathlib import Path

import pytest
from gitferry.identity_decisions import (
    IdentityDecisionConflict,
    IdentityDecisionService,
    IdentityDecisionStale,
)
from gitferry.state_store import BindingConflict, StateStore


def service(tmp_path: Path) -> IdentityDecisionService:
    state = StateStore(tmp_path / "state.sqlite3")
    state.register_instance("target", "Target", "https://target.invalid", "install-target")
    state.register_realm("realm", "Directory", "forest-synthetic")
    return IdentityDecisionService(state)


def candidate(service: IdentityDecisionService) -> dict:
    return service.submit_candidate(
        selector={
            "realm_key": "realm",
            "login": "sam",
            "external_id": "S-1-5-21-100",
            "ou_path": "",
        },
        target={"instance_key": "target", "account_id": 41, "login": "sam-target"},
        prerequisites={"issue_author": True, "comment_author": False, "team_member": True},
        authority_digest="a" * 64,
        profile_revision=1,
    )


def test_candidate_is_durable_and_audit_is_append_only(tmp_path: Path) -> None:
    first = service(tmp_path)
    item = candidate(first)
    reopened = IdentityDecisionService(StateStore(tmp_path / "state.sqlite3"))
    assert reopened.get_candidate(item["candidate_id"])["revision"] == 1
    assert reopened.events(item["candidate_id"])[0]["decision"] == "pending"
    rejected = reopened.decide(
        item["candidate_id"], expected_revision=1, decision="rejected", actor="admin"
    )
    assert rejected["status"] == "rejected"
    assert len(reopened.events(item["candidate_id"])) == 2


def test_stale_and_redirected_decisions_do_not_mutate(tmp_path: Path) -> None:
    svc = service(tmp_path)
    item = candidate(svc)
    with pytest.raises(IdentityDecisionStale):
        svc.decide(item["candidate_id"], expected_revision=2, decision="rejected", actor="admin")
    with pytest.raises(IdentityDecisionConflict):
        svc.submit_candidate(
            candidate_id=item["candidate_id"],
            selector={"realm_key": "realm", "login": "other", "external_id": "x", "ou_path": ""},
            target={"instance_key": "target", "account_id": 41, "login": "other"},
            prerequisites={"issue_author": True, "comment_author": False, "team_member": True},
            authority_digest="a" * 64,
            profile_revision=1,
        )
    assert svc.get_candidate(item["candidate_id"])["revision"] == 1


def test_binding_conflict_is_persisted_without_partial_binding(tmp_path: Path) -> None:
    svc = service(tmp_path)
    item = candidate(svc)

    def conflict(_record: dict, connection: object) -> None:
        connection.execute(
            "INSERT INTO identity_bindings VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "temporary",
                "realm",
                "sam",
                "S-1-5-21-100",
                "",
                "target",
                41,
                "sam-target",
                1,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        raise BindingConflict("target account is already bound")

    result = svc.decide(
        item["candidate_id"],
        expected_revision=1,
        decision="approved",
        actor="admin",
        binder=conflict,
    )
    assert result["status"] == "conflict"
    assert svc.events(item["candidate_id"])[-1]["decision"] == "conflict"
    with svc.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0


def test_approval_requires_binder_and_rejected_can_reopen(tmp_path: Path) -> None:
    svc = service(tmp_path)
    item = candidate(svc)
    with pytest.raises(ValueError):
        svc.decide(item["candidate_id"], expected_revision=1, decision="approved", actor="admin")
    rejected = svc.decide(
        item["candidate_id"], expected_revision=1, decision="rejected", actor="admin"
    )
    reopened = svc.reopen(
        item["candidate_id"], expected_revision=rejected["revision"], actor="admin"
    )
    assert reopened["status"] == "pending"


def test_real_process_restart_preserves_decisions_and_large_id(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    svc = service(tmp_path)
    item = candidate(svc)
    expected = svc.decide(
        item["candidate_id"], expected_revision=1, decision="rejected", actor="synthetic-operator"
    )
    script = """import json,sys
from gitferry.state_store import StateStore
from gitferry.identity_decisions import IdentityDecisionService
s=IdentityDecisionService(StateStore(sys.argv[1]))
print(json.dumps([s.get_candidate(sys.argv[2]),s.events(sys.argv[2])]))
"""
    observed = subprocess.run(  # noqa: S603 - fixed test script and synthetic arguments
        [sys.executable, "-c", script, str(svc.state.path), item["candidate_id"]],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    record, events = json.loads(observed.stdout)
    assert record == expected
    assert events == svc.events(item["candidate_id"])
    assert isinstance(record["target"]["account_id"], str)


def test_two_candidates_for_same_identity_require_explicit_choice(tmp_path: Path) -> None:
    svc = service(tmp_path)
    first = candidate(svc)
    other = svc.submit_candidate(
        selector=first["selector"],
        target={"instance_key": "target", "account_id": 42, "login": "other-target"},
        prerequisites=first["prerequisites"],
        authority_digest="a" * 64,
        profile_revision=1,
    )
    assert first["candidate_id"] != other["candidate_id"]
    assert [item["status"] for item in svc.list_candidates()] == ["pending", "pending"]
    with svc.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 0


def test_competing_process_approvals_cannot_create_two_bindings(tmp_path: Path) -> None:
    import subprocess
    import sys

    svc = service(tmp_path)
    item = candidate(svc)
    # Each independent process attempts the same stale revision. SQLite serializes
    # the decision and binding in one short transaction, not a Python object lock.
    script = """import sys
from gitferry.state_store import StateStore
from gitferry.identity_decisions import IdentityDecisionService,IdentityDecisionStale
state=StateStore(sys.argv[1]); service=IdentityDecisionService(state)
def bind(record,connection):
 state.confirm_identity_binding(binding_id='explicit',source_realm_key='realm',
  source_login='sam',source_external_id='S-1-5-21-100',source_ou_path='',
  target_instance_key='target',target_account_id=41,target_login='sam-target',
  mapping_version=1,connection=connection)
try:
 service.decide(sys.argv[2],expected_revision=1,decision='approved',actor='worker',binder=bind)
 print('approved')
except IdentityDecisionStale: print('stale')
"""
    processes = [
        subprocess.Popen(  # noqa: S603 - fixed test script and synthetic arguments
            [sys.executable, "-c", script, str(svc.state.path), item["candidate_id"]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=20) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    assert sorted(stdout.strip() for stdout, _ in results) == ["approved", "stale"]
    with svc.state.read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM identity_bindings").fetchone()[0] == 1
    assert len(svc.events(item["candidate_id"])) == 2
