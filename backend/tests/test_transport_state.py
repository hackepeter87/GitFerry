from __future__ import annotations

import json
from pathlib import Path

import pytest
from gitferry.state_store import OriginKey, StateStore
from gitferry.transport import ManifestConflict, write_transport
from gitferry.transport_state import admit_transport, confirm_import_observation, record_export
from test_transport_manifest import manifest
from test_transport_manifest_v2 import manifest_v2


def configured_state(path: Path) -> StateStore:
    state = StateStore(path)
    state.register_instance("instance-a", "Gitea A", "http://a.invalid", "install-a")
    state.register_instance("instance-b", "Gitea B", "http://b.invalid", "install-b")
    return state


def establish_a_baseline(state: StateStore) -> None:
    origin = state.register_origin(
        OriginKey(
            "instance-a",
            "synthetic/selected",
            "issue",
            "9007199254740993",
            issue_index=3,
            origin_created_at="2026-11-20T09:00:00Z",
        )
    )
    state.add_object_mapping(
        origin.id,
        "instance-a",
        "synthetic/selected",
        "9007199254740993",
        target_issue_index=3,
        target_native_created_at="2026-11-20T09:00:00Z",
    )
    state.create_transfer_round(
        "round-synthetic-1", "instance-a", "instance-b", "synthetic/selected"
    )
    values = {
        "title": "Synthetic original",
        "body": "Before",
        "state": "open",
        "labels": [],
    }
    state.create_baseline(
        baseline_id="baseline-a-1",
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


def test_received_facts_survive_restart_without_approving_identity(tmp_path: Path) -> None:
    database = tmp_path / "b.sqlite3"
    state = configured_state(database)
    artifact = write_transport(tmp_path / "inbox", manifest())

    report = admit_transport(state, artifact, local_instance_key="instance-b")
    reopened = configured_state(database)
    replay = admit_transport(reopened, artifact, local_instance_key="instance-b")

    assert report.status == "admitted"
    assert replay.status == "replay"
    assert (
        reopened.get_received_manifest("manifest-forward-1")["digest_sha256"]
        == artifact.digest_sha256
    )
    assert reopened.get_origin(report.origin_ids[0]).key.origin_instance_key == "instance-a"
    assert (
        reopened.transport_identity_claims("manifest-forward-1")[0]["status"]
        == "observed-not-approved"
    )
    with pytest.raises(KeyError):
        reopened.get_identity_binding("binding-from-manifest")


def test_same_manifest_id_with_other_digest_is_a_visible_conflict(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    first = write_transport(tmp_path / "one", manifest())
    admit_transport(state, first, local_instance_key="instance-b")
    changed = manifest()
    changed["created_at"] = "2026-11-20T10:00:01Z"
    second = write_transport(tmp_path / "two", changed)

    with pytest.raises(ManifestConflict, match="manifest id already has another digest"):
        admit_transport(state, second, local_instance_key="instance-b")

    assert state.get_received_manifest("manifest-forward-1")["digest_sha256"] == first.digest_sha256


def test_wrong_recipient_and_conflicting_replica_leave_no_partial_origins(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    wrong = manifest(manifest_id="manifest-wrong-recipient")
    wrong["recipient"] = {
        "instance_key": "instance-a",
        "installation_marker": "install-a",
        "context": "wrong direction",
    }
    artifact = write_transport(tmp_path / "wrong", wrong)
    with pytest.raises(ManifestConflict, match="recipient"):
        admit_transport(state, artifact, local_instance_key="instance-b")

    correct = write_transport(tmp_path / "correct", manifest())
    admit_transport(state, correct, local_instance_key="instance-b")
    conflicting = manifest(manifest_id="manifest-conflicting-replica")
    conflicting["objects"][0]["replicas"][0]["global_id"] = "999"
    changed = write_transport(tmp_path / "conflicting", conflicting)
    with pytest.raises(ManifestConflict, match="replica"):
        admit_transport(state, changed, local_instance_key="instance-b")

    assert (
        state.get_object_mapping(
            state.register_origin(
                OriginKey(
                    "instance-a",
                    "synthetic/selected",
                    "issue",
                    "9007199254740993",
                    issue_index=3,
                    origin_created_at="2026-11-20T09:00:00Z",
                )
            ).id,
            "instance-a",
        ).target_global_id
        == "9007199254740993"
    )


def test_return_requires_known_forward_digest_and_cannot_replace_a_baseline(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "a.sqlite3")
    establish_a_baseline(state)
    forward = write_transport(tmp_path / "forward", manifest())
    record_export(state, forward, local_instance_key="instance-a")

    returned = manifest(manifest_id="manifest-return-1")
    returned["mode"] = "return"
    returned["sender"] = {"instance_key": "instance-b", "installation_marker": "install-b"}
    returned["recipient"] = {
        "instance_key": "instance-a",
        "installation_marker": "install-a",
        "context": "isolated synthetic return target",
    }
    returned["forward_reference"] = {
        "manifest_id": "manifest-forward-1",
        "digest_sha256": forward.digest_sha256,
    }
    returned["objects"][0]["comparison"]["confirmed_import"] = returned["objects"][0]["comparison"][
        "current"
    ]
    return_artifact = write_transport(tmp_path / "return", returned)
    assert (
        admit_transport(state, return_artifact, local_instance_key="instance-a").status
        == "admitted"
    )

    altered = manifest(manifest_id="manifest-return-altered")
    altered["mode"] = "return"
    altered["sender"] = returned["sender"]
    altered["recipient"] = returned["recipient"]
    altered["forward_reference"] = returned["forward_reference"]
    altered["objects"][0]["comparison"]["original_baseline"]["body"] = "Forged baseline"
    altered_artifact = write_transport(tmp_path / "altered", altered)
    with pytest.raises(ManifestConflict, match="baseline"):
        admit_transport(state, altered_artifact, local_instance_key="instance-a")

    assert state.get_baseline("baseline-a-1").baseline["body"] == "Before"


def test_new_packaging_cannot_bypass_outcome_unknown(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    first = write_transport(tmp_path / "first", manifest())
    report = admit_transport(state, first, local_instance_key="instance-b")
    state.prepare_operation(
        operation_id="op-unknown",
        origin_id=report.origin_ids[0],
        target_instance_key="instance-b",
        target_repository_scope="synthetic/selected",
        operation_type="create_issue",
        mapping_version=1,
        baseline_id=None,
        intent={"title": "Synthetic original", "body": "Before"},
    )
    state.start_executor_session("crashed")
    state.claim_operation("op-unknown", "crashed")
    state.start_executor_session("restart", recover_abandoned=True)

    repackaged = write_transport(
        tmp_path / "repackaged", manifest(manifest_id="manifest-forward-repackaged")
    )
    blocked = admit_transport(state, repackaged, local_instance_key="instance-b")

    assert blocked.status == "admitted-with-blocks"
    assert blocked.blocked_operations == ("op-unknown",)
    assert state.get_operation("op-unknown").status == "outcome_unknown"


def test_return_export_uses_frozen_confirmed_import_observation(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    forward = write_transport(tmp_path / "forward", manifest())
    admitted = admit_transport(state, forward, local_instance_key="instance-b")
    values = manifest()["objects"][0]["comparison"]["current"]
    confirm_import_observation(
        state,
        forward_manifest_id="manifest-forward-1",
        origin_identifier=admitted.origin_ids[0],
        observed=values,
        target_native_created_at="2026-11-20T10:01:00Z",
    )
    returned = manifest(manifest_id="manifest-return-observed")
    returned["mode"] = "return"
    returned["sender"] = {"instance_key": "instance-b", "installation_marker": "install-b"}
    returned["recipient"] = {
        "instance_key": "instance-a",
        "installation_marker": "install-a",
        "context": "return",
    }
    returned["forward_reference"] = {
        "manifest_id": "manifest-forward-1",
        "digest_sha256": forward.digest_sha256,
    }
    returned["objects"][0]["comparison"]["confirmed_import"] = values
    artifact = write_transport(tmp_path / "returned", returned)
    assert record_export(state, artifact, local_instance_key="instance-b").status == "recorded"

    changed = manifest(manifest_id="manifest-return-forged-observation")
    changed["mode"] = "return"
    changed["sender"] = returned["sender"]
    changed["recipient"] = returned["recipient"]
    changed["forward_reference"] = returned["forward_reference"]
    changed["objects"][0]["comparison"]["confirmed_import"] = {
        **values,
        "body": "not the confirmed import",
    }
    forged = write_transport(tmp_path / "forged", changed)
    with pytest.raises(ManifestConflict, match="confirmed import"):
        record_export(state, forged, local_instance_key="instance-b")


def test_unknown_forward_reference_is_rejected_without_admission(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "a.sqlite3")
    returned = manifest(manifest_id="return-with-unknown-forward")
    returned["mode"] = "return"
    returned["sender"] = {"instance_key": "instance-b", "installation_marker": "install-b"}
    returned["recipient"] = {
        "instance_key": "instance-a",
        "installation_marker": "install-a",
        "context": "return",
    }
    returned["forward_reference"] = {"manifest_id": "missing-forward", "digest_sha256": "a" * 64}
    returned["objects"][0]["comparison"]["confirmed_import"] = returned["objects"][0]["comparison"][
        "current"
    ]
    artifact = write_transport(tmp_path / "return", returned)

    with pytest.raises(ManifestConflict, match="unknown or mismatched forward"):
        admit_transport(state, artifact, local_instance_key="instance-a")
    with pytest.raises(KeyError):
        state.get_received_manifest("return-with-unknown-forward")


def test_new_manifest_same_origin_is_not_a_new_object_identity(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    first = write_transport(tmp_path / "first", manifest())
    original = admit_transport(state, first, local_instance_key="instance-b")
    second = write_transport(
        tmp_path / "second", manifest(manifest_id="manifest-forward-another-envelope")
    )
    repeated = admit_transport(state, second, local_instance_key="instance-b")

    assert repeated.origin_ids == original.origin_ids
    assert state.get_object_mapping(original.origin_ids[0], "instance-a").target_global_id == (
        "9007199254740993"
    )


def test_multi_object_replay_preserves_manifest_order(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    document = manifest(manifest_id="manifest-two-objects")
    second = json.loads(json.dumps(document["objects"][0]))
    second["origin"]["instance_key"] = "instance-b"
    second["origin"]["global_id"] = "2"
    second["origin"]["issue_index"] = "2"
    second["replicas"][0]["instance_key"] = "instance-b"
    second["replicas"][0]["global_id"] = "2"
    second["replicas"][0]["issue_index"] = "2"
    document["objects"].append(second)
    artifact = write_transport(tmp_path / "two", document)
    first = admit_transport(state, artifact, local_instance_key="instance-b")

    replay = admit_transport(StateStore(state.path), artifact, local_instance_key="instance-b")

    assert replay.origin_ids == first.origin_ids


def test_v2_comment_before_parent_is_admitted_with_stable_parent_origin(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    artifact = write_transport(tmp_path / "v2", manifest_v2(comment_first=True))

    report = admit_transport(state, artifact, local_instance_key="instance-b")

    comment = state.get_origin(report.origin_ids[0])
    parent = state.get_origin(report.origin_ids[1])
    assert comment.key.object_type == "comment"
    assert comment.key.parent_origin_id == parent.id
    assert state.get_object_mapping(comment.id, "instance-a").target_issue_index == 3


def test_v2_repackaging_reports_existing_unknown_comment_operation(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    first = write_transport(tmp_path / "v2-first", manifest_v2())
    report = admit_transport(state, first, local_instance_key="instance-b")
    comment_origin = report.origin_ids[1]
    state.prepare_operation(
        operation_id="comment-post-unknown",
        origin_id=comment_origin,
        target_instance_key="instance-b",
        target_repository_scope="synthetic/selected",
        operation_type="create_comment",
        mapping_version=1,
        baseline_id=None,
        intent={"body": "Unicode: Fähre 🚢\n\n**Markdown**"},
    )
    state.start_executor_session("comment-crashed")
    state.claim_operation("comment-post-unknown", "comment-crashed")
    state.start_executor_session("comment-restart", recover_abandoned=True)

    repackaged = manifest_v2()
    repackaged["manifest_id"] = "manifest-v2-repackaged-unknown-comment"
    artifact = write_transport(tmp_path / "v2-repackaged", repackaged)
    blocked = admit_transport(state, artifact, local_instance_key="instance-b")

    assert blocked.status == "admitted-with-blocks"
    assert blocked.blocked_operations == ("comment-post-unknown",)
    assert state.get_operation("comment-post-unknown").status == "outcome_unknown"


def test_v2_unresolved_or_changed_comment_parent_is_atomic_conflict(tmp_path: Path) -> None:
    state = configured_state(tmp_path / "b.sqlite3")
    first = write_transport(tmp_path / "first-v2", manifest_v2())
    original = admit_transport(state, first, local_instance_key="instance-b")

    changed = manifest_v2()
    changed["manifest_id"] = "manifest-v2-changed-parent"
    changed["objects"][1]["origin"]["parent_issue_origin"]["global_id"] = "77"
    changed_artifact = write_transport(tmp_path / "changed-v2", changed)
    with pytest.raises(ManifestConflict, match="parent issue origin cannot be resolved"):
        admit_transport(state, changed_artifact, local_instance_key="instance-b")

    assert state.get_origin(original.origin_ids[1]).key.parent_origin_id == original.origin_ids[0]
    with pytest.raises(KeyError):
        state.get_received_manifest("manifest-v2-changed-parent")

    redirected = manifest_v2()
    redirected["manifest_id"] = "manifest-v2-redirected-parent"
    second_parent = json.loads(json.dumps(redirected["objects"][0]))
    second_parent["origin"]["global_id"] = "77"
    second_parent["origin"]["issue_index"] = "7"
    second_parent["replicas"][0]["global_id"] = "77"
    second_parent["replicas"][0]["issue_index"] = "7"
    redirected["objects"][1]["origin"]["parent_issue_origin"]["global_id"] = "77"
    redirected["objects"].append(second_parent)
    redirected_artifact = write_transport(tmp_path / "redirected-v2", redirected)
    with pytest.raises(ManifestConflict, match="immutable origin"):
        admit_transport(state, redirected_artifact, local_instance_key="instance-b")

    with pytest.raises(KeyError):
        state.get_received_manifest("manifest-v2-redirected-parent")
