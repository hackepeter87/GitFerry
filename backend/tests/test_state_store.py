from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from gitferry.state_migrations import MIGRATIONS, SCHEMA_VERSION
from gitferry.state_store import (
    BindingConflict,
    MappingConflict,
    OperationConflict,
    OriginKey,
    StateStore,
    TargetConfirmation,
    UnsupportedSchemaVersion,
)


def store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "gitferry-state.sqlite3")


def register_instances(state: StateStore) -> None:
    state.register_instance("instance-a", "Gitea A", "http://a.invalid", "install-a-2026")
    state.register_instance("instance-b", "Gitea B", "http://b.invalid", "install-b-2026")
    state.register_instance("instance-c", "Gitea C", "http://b.invalid", "install-c-2026")


def test_instances_realms_and_bindings_survive_reopening(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    first = StateStore(database)
    register_instances(first)
    first.register_realm("realm-a", "Directory A", "forest-a")
    first.confirm_identity_binding(
        binding_id="binding-a-sam",
        source_realm_key="realm-a",
        source_login="sam",
        source_external_id="S-1-5-21-A-1001",
        source_ou_path="OU=People,DC=a,DC=invalid",
        target_instance_key="instance-b",
        target_account_id=41,
        target_login="sam-target",
        mapping_version=1,
    )

    reopened = StateStore(database)

    assert reopened.get_instance("instance-b").installation_marker == "install-b-2026"
    assert reopened.get_realm("realm-a").external_marker == "forest-a"
    assert reopened.get_identity_binding("binding-a-sam").target_account_id == 41
    assert reopened.connection_settings() == {
        "foreign_keys": 1,
        "journal_mode": "wal",
        "synchronous": 2,
    }


def test_same_url_with_another_installation_is_a_distinct_instance(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)

    assert state.get_instance("instance-b").base_url == state.get_instance("instance-c").base_url
    assert (
        state.get_instance("instance-b").installation_marker
        != state.get_instance("instance-c").installation_marker
    )


def test_identity_binding_cannot_be_redirected_or_reuse_target_account(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    state.register_realm("realm-a", "Directory A", "forest-a")
    arguments = {
        "binding_id": "binding-a-sam",
        "source_realm_key": "realm-a",
        "source_login": "sam",
        "source_external_id": "S-1-5-21-A-1001",
        "source_ou_path": "OU=People,DC=a,DC=invalid",
        "target_instance_key": "instance-b",
        "target_account_id": 41,
        "target_login": "sam-target",
        "mapping_version": 1,
    }
    state.confirm_identity_binding(**arguments)

    with pytest.raises(BindingConflict, match="must not be redirected"):
        state.confirm_identity_binding(**{**arguments, "target_account_id": 42})

    with pytest.raises(BindingConflict, match="already bound"):
        state.confirm_identity_binding(
            **{
                **arguments,
                "binding_id": "binding-a-alex",
                "source_login": "alex",
                "source_external_id": "S-1-5-21-A-1002",
            }
        )


def test_same_numeric_account_id_on_two_instances_is_not_confused(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    state.register_realm("realm-a", "Directory A", "forest-a")
    common = {
        "source_realm_key": "realm-a",
        "source_login": "sam",
        "source_external_id": "S-1-5-21-A-1001",
        "source_ou_path": "OU=People,DC=a,DC=invalid",
        "target_account_id": 41,
        "target_login": "sam-target",
        "mapping_version": 1,
    }
    state.confirm_identity_binding(
        binding_id="binding-to-b", target_instance_key="instance-b", **common
    )
    state.confirm_identity_binding(
        binding_id="binding-to-c", target_instance_key="instance-c", **common
    )

    assert state.get_identity_binding("binding-to-b").target_instance_key == "instance-b"
    assert state.get_identity_binding("binding-to-c").target_instance_key == "instance-c"


def test_origin_namespaces_and_target_specific_mappings_are_distinct(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    issue_a = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    issue_b = state.register_origin(
        OriginKey("instance-b", "org/repo", "issue", "17", issue_index=3)
    )
    state.add_object_mapping(issue_a.id, "instance-b", "org/repo", "71", target_issue_index=8)
    state.add_object_mapping(issue_a.id, "instance-c", "org/repo", "91", target_issue_index=12)

    assert issue_a.id != issue_b.id
    assert state.get_object_mapping(issue_a.id, "instance-b").target_global_id == "71"
    assert state.get_object_mapping(issue_a.id, "instance-c").target_global_id == "91"


def test_a_to_b_to_a_keeps_original_origin_and_conflicting_mapping_fails(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    state.add_object_mapping(origin.id, "instance-b", "org/repo", "71", target_issue_index=8)
    state.add_object_mapping(origin.id, "instance-a", "org/repo", "17", target_issue_index=3)

    assert state.get_origin(origin.id).key.origin_instance_key == "instance-a"
    with pytest.raises(MappingConflict, match="must not be redirected"):
        state.add_object_mapping(origin.id, "instance-b", "org/repo", "72", target_issue_index=9)


def test_baseline_is_immutable_while_desired_and_observed_are_separate(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    state.create_transfer_round("round-1", "instance-a", "instance-b", "org/repo#17")
    state.create_baseline(
        baseline_id="baseline-1",
        transfer_round_id="round-1",
        origin_id=origin.id,
        ruleset_name="gitea-issue-selected-fields",
        ruleset_version=1,
        baseline={"title": "Original", "body": "Before"},
        desired={"title": "Original", "body": "After"},
        observed={"title": "Original", "body": "Before", "id": 71},
        origin_created_at="2026-11-20T10:00:00Z",
        target_native_created_at="2026-11-20T10:05:00Z",
    )
    state.update_baseline_observation(
        "baseline-1", {"title": "Original", "body": "Drifted", "id": 71}
    )

    record = state.get_baseline("baseline-1")
    assert record.baseline == {"title": "Original", "body": "Before"}
    assert record.desired == {"title": "Original", "body": "After"}
    assert record.observed == {"title": "Original", "body": "Drifted", "id": 71}
    assert record.origin_created_at != record.target_native_created_at


def test_operation_id_cannot_be_reused_for_changed_intent_or_secrets(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    state.prepare_operation(
        operation_id="op-create-17",
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org/repo",
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "Synthetic", "body": "Body"},
    )

    with pytest.raises(OperationConflict, match="different intent"):
        state.prepare_operation(
            operation_id="op-create-17",
            origin_id=origin.id,
            target_instance_key="instance-b",
            target_repository_scope="org/repo",
            operation_type="create_issue",
            mapping_version=1,
            baseline_id=None,
            intent={"title": "Changed", "body": "Body"},
        )
    with pytest.raises(ValueError, match="secret"):
        state.prepare_operation(
            operation_id="op-secret",
            origin_id=origin.id,
            target_instance_key="instance-b",
            target_repository_scope="org/repo",
            operation_type="create_issue",
            mapping_version=1,
            baseline_id=None,
            intent={"title": "Synthetic", "api_token": "must-not-persist"},
        )


def test_unknown_newer_schema_is_rejected_without_replacing_database(tmp_path: Path) -> None:
    database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE future_data (value TEXT NOT NULL)")
    connection.execute("INSERT INTO future_data VALUES ('keep-me')")
    connection.execute("PRAGMA user_version = 999")
    connection.commit()
    connection.close()

    with pytest.raises(UnsupportedSchemaVersion, match="newer schema version 999"):
        StateStore(database)

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT value FROM future_data").fetchone() == ("keep-me",)
    connection.close()


def test_version_one_database_migrates_without_losing_existing_state(tmp_path: Path) -> None:
    database = tmp_path / "version-one.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(MIGRATIONS[1])
    connection.execute("INSERT INTO schema_migrations VALUES (1, '2026-11-20T00:00:00Z')")
    connection.execute("PRAGMA user_version = 1")
    connection.execute(
        "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
        ("instance-a", "A", "http://a.invalid", "install-a", "2026-11-20T00:00:00Z"),
    )
    connection.commit()
    connection.close()

    migrated = StateStore(database)

    assert migrated.get_instance("instance-a").installation_marker == "install-a"
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'transport_manifests'"
    ).fetchone() == ("transport_manifests",)
    connection.close()


def test_version_two_operations_migrate_to_comment_journal_without_lost_events(
    tmp_path: Path,
) -> None:
    database = tmp_path / "version-two.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(MIGRATIONS[1])
    connection.execute("INSERT INTO schema_migrations VALUES (1, '2026-11-20T00:00:00Z')")
    connection.executescript(MIGRATIONS[2])
    connection.execute("INSERT INTO schema_migrations VALUES (2, '2026-11-20T00:01:00Z')")
    connection.execute("PRAGMA user_version = 2")
    connection.execute(
        "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
        ("instance-a", "A", "http://a.invalid", "install-a", "2026-11-20T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO object_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "origin-a",
            "instance-a",
            "org/repo",
            "issue",
            "17",
            3,
            None,
            None,
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO operations "
        "(operation_id, origin_id, target_instance_key, target_repository_scope, "
        "operation_type, mapping_version, intent_json, intent_hash, status, created_at, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "existing-create",
            "origin-a",
            "instance-a",
            "org/repo",
            "create_issue",
            1,
            '{"body":"before"}',
            "a" * 64,
            "prepared",
            "2026-11-20T00:00:00Z",
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO operation_events "
        "(operation_id, from_status, to_status, created_at) VALUES (?, NULL, ?, ?)",
        ("existing-create", "prepared", "2026-11-20T00:00:00Z"),
    )
    connection.commit()
    connection.close()

    migrated = StateStore(database)

    assert migrated.get_operation("existing-create").status == "prepared"
    assert migrated.operation_evidence("existing-create")["events"][0]["to"] == "prepared"
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_failed_v4_migration_rolls_back_the_populated_v3_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "failed-v4.sqlite3"
    connection = sqlite3.connect(database)
    for version in (1, 2, 3):
        connection.executescript(MIGRATIONS[version])
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?)",
            (version, f"2026-11-20T00:0{version}:00Z"),
        )
    connection.execute("PRAGMA user_version = 3")
    connection.execute(
        "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
        ("instance-a", "A", "http://a.invalid", "install-a", "2026-11-20T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO object_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "origin-a",
            "instance-a",
            "org/repo",
            "issue",
            "17",
            3,
            None,
            None,
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    failing = MIGRATIONS[4].replace(
        "DROP TABLE object_origins;",
        "DROP TABLE object_origins;\nTHIS IS DELIBERATELY INVALID SQL;",
        1,
    )
    monkeypatch.setitem(MIGRATIONS, 4, failing)

    with pytest.raises(sqlite3.OperationalError):
        StateStore(database)

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone() == (3,)
    assert connection.execute("SELECT origin_id, object_type FROM object_origins").fetchall() == [
        ("origin-a", "issue")
    ]
    assert connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall() == [
        (1,),
        (2,),
        (3,),
    ]
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'object_origins_backup'"
        ).fetchone()
        is None
    )
    connection.close()


def test_version_four_database_adds_auth_schema_without_losing_state(tmp_path: Path) -> None:
    database = tmp_path / "version-four.sqlite3"
    connection = sqlite3.connect(database)
    for version in (1, 2, 3, 4):
        connection.executescript(MIGRATIONS[version])
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?)",
            (version, f"2026-11-20T00:0{version}:00Z"),
        )
    connection.execute("PRAGMA user_version = 4")
    connection.execute(
        "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
        ("instance-a", "A", "http://a.invalid", "install-a", "2026-11-20T00:00:00Z"),
    )
    connection.commit()
    connection.close()

    migrated = StateStore(database)

    assert migrated.get_instance("instance-a").installation_marker == "install-a"
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'auth_sessions'"
    ).fetchone() == ("auth_sessions",)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_failed_v5_migration_leaves_version_four_database_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "failed-v5.sqlite3"
    connection = sqlite3.connect(database)
    for version in (1, 2, 3, 4):
        connection.executescript(MIGRATIONS[version])
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?)",
            (version, f"2026-11-20T00:0{version}:00Z"),
        )
    connection.execute("PRAGMA user_version = 4")
    connection.commit()
    connection.close()

    failing = MIGRATIONS[5].replace(
        "CREATE TABLE auth_sessions (",
        "THIS IS DELIBERATELY INVALID SQL;\nCREATE TABLE auth_sessions (",
        1,
    )
    monkeypatch.setitem(MIGRATIONS, 5, failing)

    with pytest.raises(sqlite3.OperationalError):
        StateStore(database)

    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA user_version").fetchone() == (4,)
    assert connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall() == [(1,), (2,), (3,), (4,)]
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'local_accounts'"
        ).fetchone()
        is None
    )
    connection.close()


def test_v4_origin_types_and_mapping_preserving_side_effects(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    origins = [
        state.register_origin(OriginKey("instance-a", "org", kind, kind))
        for kind in ("organization", "team", "repository")
    ]
    assert [origin.key.object_type for origin in origins] == [
        "organization",
        "team",
        "repository",
    ]

    origin = origins[1]
    state.prepare_operation(
        operation_id="create-team",
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org",
        operation_type="create_team",
        mapping_version=1,
        baseline_id=None,
        intent={"name": "Synthetic Team"},
    )
    state.start_executor_session("executor")
    state.claim_operation("create-team", "executor")
    state.confirm_operation("create-team", TargetConfirmation("team-7", None))

    for operation_type in ("add_team_member", "assign_team_repository", "push_git_bundle"):
        operation_id = f"{operation_type}-1"
        state.prepare_operation(
            operation_id=operation_id,
            origin_id=origin.id,
            target_instance_key="instance-b",
            target_repository_scope="org",
            operation_type=operation_type,
            mapping_version=1,
            baseline_id=None,
            intent={"operation": operation_type},
        )
        state.claim_operation(operation_id, "executor")
        state.confirm_operation(operation_id, TargetConfirmation("team-7", None))

    connection = sqlite3.connect(state.path)
    assert connection.execute(
        "SELECT count(*) FROM object_mappings WHERE origin_id = ?", (origin.id,)
    ).fetchone() == (1,)
    connection.close()


def test_mapping_preserving_side_effect_rejects_mismatched_target(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(OriginKey("instance-a", "org", "team", "team-1"))
    state.prepare_operation(
        operation_id="create-team",
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org",
        operation_type="create_team",
        mapping_version=1,
        baseline_id=None,
        intent={"name": "Synthetic Team"},
    )
    state.start_executor_session("executor")
    state.claim_operation("create-team", "executor")
    state.confirm_operation("create-team", TargetConfirmation("team-7", None))
    state.prepare_operation(
        operation_id="add-member",
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org",
        operation_type="add_team_member",
        mapping_version=1,
        baseline_id=None,
        intent={"login": "synthetic-user"},
    )
    state.claim_operation("add-member", "executor")
    with pytest.raises(MappingConflict, match="immutable target mapping"):
        state.confirm_operation("add-member", TargetConfirmation("team-8", None))


def test_unknown_mapping_preserving_side_effects_reconcile_without_duplicate_mapping(
    tmp_path: Path,
) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(OriginKey("instance-a", "org", "team", "team-1"))
    state.prepare_operation(
        operation_id="create-team",
        origin_id=origin.id,
        target_instance_key="instance-b",
        target_repository_scope="org",
        operation_type="create_team",
        mapping_version=1,
        baseline_id=None,
        intent={"name": "Synthetic Team"},
    )
    state.start_executor_session("executor")
    state.claim_operation("create-team", "executor")
    state.confirm_operation("create-team", TargetConfirmation("team-7", None))

    for operation_type in ("assign_team_repository", "add_team_member", "push_git_bundle"):
        operation_id = f"unknown-{operation_type}"
        intent = {"operation": operation_type}
        state.prepare_operation(
            operation_id=operation_id,
            origin_id=origin.id,
            target_instance_key="instance-b",
            target_repository_scope="org",
            operation_type=operation_type,
            mapping_version=1,
            baseline_id=None,
            intent=intent,
        )
        state.claim_operation(operation_id, "executor")
        state.mark_outcome_unknown(operation_id, "synthetic lost response")
        reconciled = state.resolve_unknown_operation(
            operation_id,
            target_global_id="team-7",
            target_issue_index=None,
            observed_intent=intent,
            evidence="synthetic target lookup",
        )
        assert reconciled.status == "confirmed"

    issue = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "issue-1", issue_index=1)
    )
    state.add_object_mapping(
        issue.id,
        "instance-b",
        "org/repo",
        "71",
        target_issue_index=8,
    )
    comment = state.register_origin(
        OriginKey(
            "instance-a",
            "org/repo",
            "comment",
            "comment-1",
            issue_index=1,
            parent_origin_id=issue.id,
        )
    )
    state.add_object_mapping(
        comment.id,
        "instance-b",
        "org/repo",
        "101",
        target_issue_index=8,
    )

    for operation_type, mapped_origin, target_global_id in (
        ("update_issue", issue, "71"),
        ("update_comment", comment, "101"),
    ):
        operation_id = f"unknown-{operation_type}"
        intent = {"operation": operation_type}
        state.prepare_operation(
            operation_id=operation_id,
            origin_id=mapped_origin.id,
            target_instance_key="instance-b",
            target_repository_scope="org/repo",
            operation_type=operation_type,
            mapping_version=1,
            baseline_id=None,
            intent=intent,
        )
        state.claim_operation(operation_id, "executor")
        state.mark_outcome_unknown(operation_id, "synthetic lost response")
        reconciled = state.resolve_unknown_operation(
            operation_id,
            target_global_id=target_global_id,
            target_issue_index=8,
            observed_intent=intent,
            evidence="synthetic target lookup",
        )
        assert reconciled.status == "confirmed"

    connection = sqlite3.connect(state.path)
    assert connection.execute(
        "SELECT count(*) FROM object_mappings WHERE origin_id = ?", (origin.id,)
    ).fetchone() == (1,)
    connection.close()


def test_populated_v3_database_migrates_to_v4_without_losing_references(
    tmp_path: Path,
) -> None:
    database = tmp_path / "populated-v3.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(MIGRATIONS[1])
    connection.execute("INSERT INTO schema_migrations VALUES (1, '2026-11-20T00:00:00Z')")
    connection.executescript(MIGRATIONS[2])
    connection.execute("INSERT INTO schema_migrations VALUES (2, '2026-11-20T00:01:00Z')")
    connection.executescript(MIGRATIONS[3])
    connection.execute("INSERT INTO schema_migrations VALUES (3, '2026-11-20T00:02:00Z')")
    connection.execute("PRAGMA user_version = 3")
    connection.executemany(
        "INSERT INTO instances VALUES (?, ?, ?, ?, ?)",
        [
            ("instance-a", "A", "http://a.invalid", "install-a", "2026-11-20T00:00:00Z"),
            ("instance-b", "B", "http://b.invalid", "install-b", "2026-11-20T00:00:00Z"),
        ],
    )
    connection.execute(
        "INSERT INTO object_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "origin-issue",
            "instance-a",
            "org/repo",
            "issue",
            "17",
            3,
            None,
            None,
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO object_origins VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "origin-comment",
            "instance-a",
            "org/repo",
            "comment",
            "19",
            None,
            "origin-issue",
            None,
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO object_mappings "
        "(origin_id, target_instance_key, target_repository_scope, target_object_type, "
        "target_global_id, target_issue_index, target_native_created_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("origin-issue", "instance-b", "org/repo", "issue", "71", 8, None, "2026-11-20T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO transfer_rounds VALUES (?, ?, ?, ?, ?)",
        ("round-1", "instance-a", "instance-b", "selection", "2026-11-20T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO baselines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "baseline-1",
            "round-1",
            "origin-issue",
            "default",
            1,
            "{}",
            "{}",
            "{}",
            1,
            None,
            None,
            "2026-11-20T00:00:00Z",
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO operations "
        "(operation_id, origin_id, target_instance_key, target_repository_scope, operation_type, "
        "mapping_version, baseline_id, intent_json, intent_hash, status, target_global_id, "
        "target_issue_index, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "op-1",
            "origin-issue",
            "instance-b",
            "org/repo",
            "update_issue",
            1,
            "baseline-1",
            "{}",
            "a" * 64,
            "confirmed",
            "71",
            8,
            "2026-11-20T00:00:00Z",
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO operation_events (operation_id, from_status, to_status, created_at) "
        "VALUES (?, ?, ?, ?)",
        ("op-1", "executing", "confirmed", "2026-11-20T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO conflicts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "conflict-1",
            "op-1",
            "synthetic",
            "retained",
            "{}",
            "resolved",
            "2026-11-20T00:00:00Z",
            "2026-11-20T00:01:00Z",
        ),
    )
    digest = "b" * 64
    connection.execute(
        "INSERT INTO transport_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "manifest-1",
            digest,
            "round-1",
            "exported",
            "forward",
            "instance-a",
            "instance-b",
            None,
            None,
            "{}",
            "2026-11-20T00:00:00Z",
        ),
    )
    connection.execute(
        "INSERT INTO transport_manifest_objects VALUES (?, ?, ?)",
        ("manifest-1", "origin-comment", "{}"),
    )
    connection.commit()
    connection.close()

    migrated = StateStore(database)
    assert migrated.get_origin("origin-comment").key.parent_origin_id == "origin-issue"
    assert migrated.get_object_mapping("origin-issue", "instance-b").target_global_id == "71"
    assert migrated.get_baseline("baseline-1").origin_id == "origin-issue"
    assert migrated.get_operation("op-1").target_global_id == "71"
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT count(*) FROM operation_events WHERE operation_id = 'op-1'"
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT count(*) FROM conflicts WHERE conflict_id = 'conflict-1'"
    ).fetchone() == (1,)
    assert connection.execute(
        "SELECT count(*) FROM transport_manifest_objects WHERE manifest_id = 'manifest-1'"
    ).fetchone() == (1,)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_database_foreign_keys_and_unique_constraints_protect_concurrent_writes(
    tmp_path: Path,
) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    state.add_object_mapping(origin.id, "instance-b", "org/repo", "71", target_issue_index=8)

    connection = sqlite3.connect(state.path)
    connection.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO object_mappings "
            "(origin_id, target_instance_key, target_repository_scope, target_global_id, "
            "target_issue_index, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (origin.id, "instance-b", "org/repo", "72", 9, "2026-11-20T00:00:00Z"),
        )
    connection.close()


def test_concurrent_mapping_writers_cannot_bypass_origin_uniqueness(tmp_path: Path) -> None:
    state = store(tmp_path)
    register_instances(state)
    origin = state.register_origin(
        OriginKey("instance-a", "org/repo", "issue", "17", issue_index=3)
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def write(target_id: str) -> None:
        barrier.wait(timeout=5)
        try:
            StateStore(state.path).add_object_mapping(
                origin.id,
                "instance-b",
                "org/repo",
                target_id,
                target_issue_index=int(target_id),
            )
            outcomes.append("stored")
        except MappingConflict:
            outcomes.append("conflict")

    first = threading.Thread(target=write, args=("71",))
    second = threading.Thread(target=write, args=("72",))
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert sorted(outcomes) == ["conflict", "stored"]
