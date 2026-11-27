from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gitferry.operations import OperationBlocked, OperationExecutor, OutcomeUnknownError
from gitferry.provisioning import (
    BundleIntent,
    ProvisioningMember,
    ProvisioningPlan,
    ProvisioningTeam,
    preflight_provisioning_operations,
    prepare_provisioning_operations,
    provision_selected_repository,
)
from gitferry.state_store import OriginKey, StateStore, TargetConfirmation


class FakeProvisioningClient:
    authenticated_login = "technical-admin"

    def __init__(self) -> None:
        self.organizations = 0
        self.repositories = 0
        self.teams = 0
        self.assignments = 0
        self.memberships = 0
        self.remote_reads = 0
        self.existing_organizations: dict[str, dict[str, Any]] = {}
        self.existing_repositories: dict[str, dict[str, Any]] = {}
        self.existing_teams: dict[tuple[str, str], dict[str, Any]] = {}
        self.fail_after_repository = False
        self.users = {
            "reader-b": {"id": 51, "login": "reader-b", "is_admin": False},
            "writer-b": {"id": 52, "login": "writer-b", "is_admin": False},
        }
        self.repository = {
            "id": 201,
            "full_name": "access-lab/selected-private",
            "private": True,
        }

    def create_organization(self, username: str, visibility: str) -> dict[str, Any]:
        self.organizations += 1
        assert visibility == "private"
        created = {"id": 101, "username": username, "visibility": "private"}
        self.existing_organizations[username] = created
        return created

    def find_organization(self, username: str) -> dict[str, Any] | None:
        self.remote_reads += 1
        return self.existing_organizations.get(username)

    def create_repository(self, organization: str, name: str, private: bool) -> dict[str, Any]:
        self.repositories += 1
        assert (organization, name, private) == ("access-lab", "selected-private", True)
        self.existing_repositories[self.repository["full_name"]] = self.repository
        if self.fail_after_repository:
            raise OutcomeUnknownError("accepted create but response was lost")
        return self.repository

    def find_repository(self, repository: str) -> dict[str, Any] | None:
        self.remote_reads += 1
        return self.existing_repositories.get(repository)

    def create_team(self, organization: str, name: str, permission: str) -> dict[str, Any]:
        self.teams += 1
        created = {
            "id": 300 + self.teams,
            "name": name,
            "permission": permission,
            "can_create_org_repo": False,
            "includes_all_repositories": False,
            "units_map": {
                "repo.code": permission,
                "repo.issues": permission,
                "repo.releases": permission,
            },
        }
        self.existing_teams[(organization, name)] = created
        return created

    def find_team(self, organization: str, name: str) -> dict[str, Any] | None:
        self.remote_reads += 1
        return self.existing_teams.get((organization, name))

    def assign_team_repository(self, team_id: str, repository: str) -> dict[str, Any]:
        self.assignments += 1
        assert team_id.isdecimal()
        assert repository == self.repository["full_name"]
        return self.repository

    def get_user(self, username: str) -> dict[str, Any]:
        self.remote_reads += 1
        return self.users[username]

    def get_authenticated_user(self) -> dict[str, Any]:
        self.remote_reads += 1
        return {"id": 1, "login": self.authenticated_login, "is_admin": True}

    def add_team_member(self, team_id: str, username: str) -> dict[str, Any]:
        self.memberships += 1
        assert team_id.isdecimal()
        return self.users[username]


class FakeBundlePusher:
    def __init__(self) -> None:
        self.pushes = 0

    def push(
        self,
        repository: str,
        *,
        bundle_member: str,
        bundle_sha256: str,
        bundle_size: int,
        expected_repository_id: str,
    ) -> TargetConfirmation:
        self.pushes += 1
        assert repository == "access-lab/selected-private"
        assert bundle_member == "repositories/access-lab/selected-private.bundle"
        assert bundle_sha256 == "a" * 64
        assert bundle_size == 1234
        return TargetConfirmation(expected_repository_id, None)


def configured(tmp_path: Path) -> tuple[StateStore, ProvisioningPlan]:
    state = StateStore(tmp_path / "provisioning.sqlite3")
    state.register_instance("instance-a", "A", "http://a.invalid", "install-a")
    state.register_instance("instance-b", "B", "http://b.invalid", "install-b")
    state.register_realm("realm-a", "Directory A", "directory-a")
    for suffix, source_login, target_login, target_id in (
        ("reader", "reader-a", "reader-b", 51),
        ("writer", "writer-a", "writer-b", 52),
    ):
        state.confirm_identity_binding(
            binding_id=f"binding-{suffix}",
            source_realm_key="realm-a",
            source_login=source_login,
            source_external_id=f"external-{suffix}",
            source_ou_path="OU=People,DC=a,DC=invalid",
            target_instance_key="instance-b",
            target_account_id=target_id,
            target_login=target_login,
            mapping_version=1,
        )
    organization = state.register_origin(
        OriginKey("instance-a", "access-lab", "organization", "11")
    )
    repository = state.register_origin(
        OriginKey("instance-a", "access-lab/selected-private", "repository", "21")
    )
    readers = state.register_origin(OriginKey("instance-a", "access-lab", "team", "31"))
    writers = state.register_origin(OriginKey("instance-a", "access-lab", "team", "32"))
    plan = ProvisioningPlan(
        target_instance_key="instance-b",
        organization_origin_id=organization.id,
        organization="access-lab",
        repository_origin_id=repository.id,
        repository="access-lab/selected-private",
        teams=(
            ProvisioningTeam(
                readers.id, "readers", "read", (ProvisioningMember("binding-reader", "read"),)
            ),
            ProvisioningTeam(
                writers.id,
                "writers",
                "write",
                (ProvisioningMember("binding-writer", "write"),),
            ),
        ),
        bundle=BundleIntent("repositories/access-lab/selected-private.bundle", "a" * 64, 1234),
    )
    return state, plan


def test_provisioning_is_journaled_and_restart_replay_has_no_remote_writes(
    tmp_path: Path,
) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    bundle = FakeBundlePusher()
    with OperationExecutor(state, "first") as executor:
        first = provision_selected_repository(state, executor, client, plan, bundle_pusher=bundle)
    assert all(operation.status == "confirmed" for operation in first.operations)
    assert (
        client.organizations,
        client.repositories,
        client.teams,
        client.assignments,
        client.memberships,
        bundle.pushes,
    ) == (1, 1, 2, 2, 2, 1)

    restarted = StateStore(state.path)
    with OperationExecutor(restarted, "restart") as executor:
        repeated = provision_selected_repository(
            restarted, executor, client, plan, bundle_pusher=bundle
        )
    assert all(operation.status == "confirmed" for operation in repeated.operations)
    assert (
        client.organizations,
        client.repositories,
        client.teams,
        client.assignments,
        client.memberships,
        bundle.pushes,
    ) == (1, 1, 2, 2, 2, 1)


def test_unknown_repository_create_blocks_replay_and_dependents(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    client.fail_after_repository = True
    bundle = FakeBundlePusher()

    with OperationExecutor(state, "lost-response") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            provision_selected_repository(state, executor, client, plan, bundle_pusher=bundle)
    assert client.repositories == 1
    assert client.teams == 0
    assert bundle.pushes == 0

    with state._connect() as connection:
        statuses = {
            row["operation_type"]: row["status"]
            for row in connection.execute("SELECT operation_type, status FROM operations")
        }
    assert statuses["create_repository"] == "outcome_unknown"
    assert statuses["assign_team_repository"] == "blocked_parent"
    assert statuses["add_team_member"] == "blocked_parent"
    assert statuses["push_git_bundle"] == "blocked_parent"

    restarted = StateStore(state.path)
    client.fail_after_repository = False
    with OperationExecutor(restarted, "restart") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            provision_selected_repository(restarted, executor, client, plan, bundle_pusher=bundle)
    assert client.repositories == 1
    assert client.teams == 0
    assert bundle.pushes == 0


def test_explicit_repository_reconciliation_resumes_blocked_dependents(
    tmp_path: Path,
) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    client.fail_after_repository = True
    bundle = FakeBundlePusher()

    with OperationExecutor(state, "lost-response") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            provision_selected_repository(state, executor, client, plan, bundle_pusher=bundle)

    with state._connect() as connection:
        operation_id = connection.execute(
            "SELECT operation_id FROM operations WHERE operation_type = 'create_repository'"
        ).fetchone()["operation_id"]
    unknown = state.get_operation(operation_id)
    state.resolve_unknown_operation(
        operation_id,
        target_global_id="201",
        target_issue_index=None,
        observed_intent=unknown.intent,
        evidence="operator verified repository id 201 on the configured target",
    )

    restarted = StateStore(state.path)
    client.fail_after_repository = False
    with OperationExecutor(restarted, "restart-after-reconciliation") as executor:
        result = provision_selected_repository(
            restarted, executor, client, plan, bundle_pusher=bundle
        )

    assert all(operation.status == "confirmed" for operation in result.operations)
    assert client.repositories == 1
    assert (client.teams, client.assignments, client.memberships, bundle.pushes) == (2, 2, 2, 1)


def test_missing_binding_leaves_structure_but_grants_no_member_rights(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    missing = ProvisioningPlan(
        **{
            **plan.__dict__,
            "teams": (
                ProvisioningTeam(
                    plan.teams[0].origin_id,
                    "readers",
                    "read",
                    (ProvisioningMember("missing-binding", "read"),),
                ),
            ),
            "bundle": None,
        }
    )
    client = FakeProvisioningClient()
    with OperationExecutor(state, "missing-binding") as executor:
        with pytest.raises(KeyError, match="identity_bindings"):
            provision_selected_repository(state, executor, client, missing)
    assert client.organizations == 0
    assert client.repositories == 0
    assert client.teams == 0
    assert client.memberships == 0


def test_admin_target_is_rejected_before_team_membership_write(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    client.users["reader-b"]["is_admin"] = True
    no_bundle = ProvisioningPlan(**{**plan.__dict__, "bundle": None})
    with OperationExecutor(state, "admin-collision") as executor:
        with pytest.raises(RuntimeError, match="administrative privileges"):
            provision_selected_repository(state, executor, client, no_bundle)
    assert client.organizations == 0
    assert client.memberships == 0


def test_origin_type_mismatch_is_rejected_before_any_remote_write(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    issue = state.register_origin(
        OriginKey("instance-a", "access-lab", "issue", "wrong-kind", issue_index=3)
    )
    wrong = ProvisioningPlan(**{**plan.__dict__, "organization_origin_id": issue.id})
    client = FakeProvisioningClient()
    with OperationExecutor(state, "wrong-origin") as executor:
        with pytest.raises(OperationBlocked, match="organization origin"):
            provision_selected_repository(
                state, executor, client, wrong, bundle_pusher=FakeBundlePusher()
            )
    assert client.organizations == 0
    assert client.repositories == 0


def test_same_origin_and_intent_can_target_two_instances(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    state.register_instance("instance-c", "C", "http://c.invalid", "install-c")
    second = ProvisioningPlan(
        **{
            **plan.__dict__,
            "target_instance_key": "instance-c",
            "teams": (),
            "bundle": None,
        }
    )
    first = ProvisioningPlan(**{**plan.__dict__, "teams": (), "bundle": None})
    client_b = FakeProvisioningClient()
    with OperationExecutor(state, "target-b") as executor:
        result_b = provision_selected_repository(state, executor, client_b, first)
    client_c = FakeProvisioningClient()
    with OperationExecutor(state, "target-c") as executor:
        result_c = provision_selected_repository(state, executor, client_c, second)
    assert {operation.target_instance_key for operation in result_b.operations} == {"instance-b"}
    assert {operation.target_instance_key for operation in result_c.operations} == {"instance-c"}
    assert {operation.operation_id for operation in result_b.operations}.isdisjoint(
        operation.operation_id for operation in result_c.operations
    )


def test_plan_rejects_role_mismatch_and_invalid_bundle_digest(tmp_path: Path) -> None:
    _, plan = configured(tmp_path)
    with pytest.raises(ValueError, match="member role"):
        ProvisioningPlan(
            **{
                **plan.__dict__,
                "teams": (
                    ProvisioningTeam(
                        plan.teams[0].origin_id,
                        "readers",
                        "read",
                        (ProvisioningMember("binding-reader", "write"),),
                    ),
                ),
            }
        )
    with pytest.raises(ValueError, match="SHA-256"):
        ProvisioningPlan(**{**plan.__dict__, "bundle": BundleIntent("bundle", "x", 1)})


def test_all_planned_writes_are_durable_before_the_first_remote_read(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)

    class InspectingClient(FakeProvisioningClient):
        def get_authenticated_user(self) -> dict[str, Any]:
            with state._connect() as connection:
                operations = list(connection.execute("SELECT status FROM operations"))
            assert len(operations) == 9
            assert {row["status"] for row in operations} == {"prepared"}
            return super().get_authenticated_user()

    with OperationExecutor(state, "prepared-first") as executor:
        provision_selected_repository(
            state,
            executor,
            InspectingClient(),
            plan,
            bundle_pusher=FakeBundlePusher(),
        )


def test_same_name_unbound_target_is_a_durable_conflict_without_post(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    client.existing_organizations[plan.organization] = {
        "id": 999,
        "username": plan.organization,
        "visibility": "private",
    }

    with OperationExecutor(state, "collision") as executor:
        with pytest.raises(OperationBlocked, match="not bound"):
            provision_selected_repository(
                state, executor, client, plan, bundle_pusher=FakeBundlePusher()
            )

    assert client.organizations == 0
    with state._connect() as connection:
        rows = list(
            connection.execute(
                "SELECT operation_type, status FROM operations ORDER BY operation_type"
            )
        )
    organization = next(row for row in rows if row["operation_type"] == "create_organization")
    assert organization["status"] == "conflict"
    assert any(row["status"] == "blocked_parent" for row in rows)


def test_replay_rejects_same_name_repository_with_a_different_target_id(
    tmp_path: Path,
) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    bundle = FakeBundlePusher()
    structure_only = ProvisioningPlan(
        **{
            **plan.__dict__,
            "teams": (),
            "bundle": None,
        }
    )
    with OperationExecutor(state, "first") as executor:
        provision_selected_repository(state, executor, client, structure_only)

    client.existing_repositories[plan.repository] = {
        **client.repository,
        "id": 999,
    }
    with OperationExecutor(state, "replacement-replay") as executor:
        with pytest.raises(OperationBlocked, match="confirmed identity"):
            provision_selected_repository(state, executor, client, plan, bundle_pusher=bundle)

    assert (client.assignments, client.memberships, bundle.pushes) == (0, 0, 0)
    with state._connect() as connection:
        operation_id = connection.execute(
            "SELECT operation_id FROM operations WHERE operation_type = 'create_repository'"
        ).fetchone()["operation_id"]
    evidence = state.operation_evidence(operation_id)
    assert evidence["conflicts"][-1]["type"] == "mapped_target_mismatch"
    assert evidence["conflicts"][-1]["evidence"] == {
        "kind": "repository",
        "name": plan.repository,
        "expected_target_id": "201",
        "observed_target_id": "999",
    }


def test_remote_success_before_local_confirmation_leaves_no_mapping(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()

    def interrupt_after_remote() -> None:
        raise SystemExit(91)

    with pytest.raises(SystemExit, match="91"):
        with OperationExecutor(
            state, "interrupted", after_remote_hook=interrupt_after_remote
        ) as executor:
            provision_selected_repository(
                state, executor, client, plan, bundle_pusher=FakeBundlePusher()
            )

    with pytest.raises(KeyError, match="object_mappings"):
        state.get_object_mapping(plan.organization_origin_id, plan.target_instance_key)
    assert client.organizations == 1

    restarted = StateStore(state.path)
    with OperationExecutor(restarted, "restart"):
        pass
    with restarted._connect() as connection:
        operation = connection.execute(
            "SELECT status FROM operations WHERE operation_type = 'create_organization'"
        ).fetchone()
    assert operation["status"] == "outcome_unknown"


def test_repository_collision_is_found_before_organization_creation(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    client.existing_repositories[plan.repository] = client.repository
    with OperationExecutor(state, "preflight") as executor, pytest.raises(OperationBlocked):
        provision_selected_repository(
            state, executor, client, plan, bundle_pusher=FakeBundlePusher()
        )
    assert (client.organizations, client.repositories, client.teams, client.memberships) == (
        0,
        0,
        0,
        0,
    )
    operations, graph = prepare_provisioning_operations(state, plan)
    repository = next(item for item in operations if item.operation_type == "create_repository")
    organization = next(item for item in operations if item.operation_type == "create_organization")
    assert repository.status == "conflict"
    assert graph[repository.operation_id] == [organization.operation_id]


def test_structure_preflight_is_read_only_and_reuses_journal_ids(tmp_path: Path) -> None:
    state, plan = configured(tmp_path)
    client = FakeProvisioningClient()
    first, graph = prepare_provisioning_operations(state, plan)
    preflight_provisioning_operations(state, client, plan)
    second, repeated = prepare_provisioning_operations(state, plan)
    assert [item.operation_id for item in first] == [item.operation_id for item in second]
    assert graph == repeated
    assert (client.organizations, client.repositories, client.teams, client.assignments) == (
        0,
        0,
        0,
        0,
    )
