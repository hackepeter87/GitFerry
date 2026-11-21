from pathlib import Path

import pytest
from gitferry.git_refs import ref_confirmation
from gitferry.native_operations import prepare_native_operations
from gitferry.operations import OperationExecutor
from gitferry.state_codec import find_secret_field
from gitferry.state_store import TargetConfirmation
from test_native_operations import _selected, _setup


def test_valid_sensitive_word_ref_names_are_not_secret_fields(tmp_path: Path) -> None:
    refs = {
        f"refs/heads/{word}-feature": "a" * 40
        for word in ("token", "password", "secret", "authorization", "private-key")
    }
    selected = _selected()
    selected["wiki"]["refs"] = refs
    state, plan = _setup(tmp_path)
    operations, _ = prepare_native_operations(state, plan, selected)
    push = next(o for o in operations if o.operation_type == "push_wiki_bundle")
    assert push.intent["refs"] == refs
    assert find_secret_field({"inventory": {"native_payloads": [selected]}}) is None


@pytest.mark.parametrize(
    "value",
    [
        {"refs": {"token": "a" * 40}},
        {"refs": {"refs/heads/token": {"password": "not-a-ref"}}},
        {"refs": {"refs/heads/token": "not-a-commit"}},
        {"refs": {"refs/heads/token.lock": "a" * 40}},
        {"refs": {"refs/heads/token": "a" * 40}, "password": "credential"},
        {"other": {"refs/heads/token": "a" * 40}},
    ],
)
def test_opaque_refs_exception_cannot_hide_secret_fields(value: dict) -> None:
    assert find_secret_field(value) is not None


@pytest.mark.parametrize("unknown", [False, True])
def test_maximum_ref_confirmation_survives_atomic_completion_and_resolution(
    tmp_path: Path, unknown: bool
) -> None:
    refs = {f"refs/heads/branch-{index:05}": "a" * 40 for index in range(10000)}
    facts = ref_confirmation(refs)
    assert facts == ref_confirmation(dict(reversed(list(refs.items()))))
    assert facts["ref_count"] == 10000
    assert len(str(facts)) < 150
    state, plan = _setup(tmp_path)
    state.add_object_mapping(plan.repository_origin_id, "target", "org/repo", "42")
    selected = _selected()
    selected["wiki"]["refs"] = refs
    operation = next(
        o
        for o in prepare_native_operations(state, plan, selected)[0]
        if o.operation_type == "push_wiki_bundle"
    )
    with OperationExecutor(state, "large-native-result") as executor:
        if unknown:
            state.claim_operation(operation.operation_id, executor.executor_id)
            state.mark_outcome_unknown(operation.operation_id, "synthetic lost native result")
        else:
            result = executor.execute(
                operation.operation_id,
                lambda _: TargetConfirmation("42", None, observed_state=facts),
            )
    if unknown:
        from gitferry.native_application import NativePayloadApplication
        from test_native_application import FakeGit, FakeLFS, FakeWiki

        git = FakeGit(tmp_path)
        git._git_remote = lambda *_: refs
        git._wiki_url = lambda scope: "https://target.invalid/" + scope + ".wiki.git"
        adapter = NativePayloadApplication(
            state, git, FakeWiki(), lambda _: FakeLFS(), max_bytes=1024
        )
        result = adapter.resolve_unknown(operation.operation_id, evidence="synthetic exact refs")
    assert result.status == "confirmed"
    assert (
        state.operation_evidence(operation.operation_id)["events"][-1]["evidence"]["observed_state"]
        == facts
    )
