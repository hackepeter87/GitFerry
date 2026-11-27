from __future__ import annotations

import pytest
from gitferry.git_return import RefConflict, compare_return_refs

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def test_fast_forward_change_is_planned_only_when_a_matches_baseline() -> None:
    plan = compare_return_refs(
        original_a={"refs/heads/main": SHA_A},
        confirmed_b={"refs/heads/main": SHA_A},
        current_b={"refs/heads/main": SHA_B},
        current_a={"refs/heads/main": SHA_A},
        is_ancestor=lambda old, new: (old, new) == (SHA_A, SHA_B),
    )

    assert plan.changes == (
        {"ref": "refs/heads/main", "old_oid": SHA_A, "new_oid": SHA_B},
    )
    assert plan.conflicts == ()


def test_a_drift_blocks_a_desired_b_change() -> None:
    plan = compare_return_refs(
        original_a={"refs/heads/main": SHA_A},
        confirmed_b={"refs/heads/main": SHA_A},
        current_b={"refs/heads/main": SHA_B},
        current_a={"refs/heads/main": SHA_C},
        is_ancestor=lambda _old, _new: True,
    )

    assert plan.changes == ()
    assert plan.conflicts == (
        RefConflict("refs/heads/main", "a-drift", "selected A ref changed since export"),
    )


def test_new_branch_is_allowed_but_deletion_and_tag_rewrite_are_not() -> None:
    plan = compare_return_refs(
        original_a={},
        confirmed_b={"refs/heads/old": SHA_A, "refs/tags/v1": SHA_A},
        current_b={"refs/heads/new": SHA_B, "refs/tags/v1": SHA_B},
        current_a={},
        is_ancestor=lambda _old, _new: True,
    )

    assert plan.changes == ({"ref": "refs/heads/new", "old_oid": None, "new_oid": SHA_B},)
    assert plan.conflicts == (
        RefConflict("refs/heads/old", "deletion-unsupported", "ref deletion is not supported"),
        RefConflict(
            "refs/tags/v1", "tag-rewrite-unsupported", "existing tag changes are not supported"
        ),
    )


def test_non_fast_forward_protected_and_unselected_refs_are_reported_deterministically() -> None:
    plan = compare_return_refs(
        original_a={"refs/heads/main": SHA_A, "refs/heads/protected": SHA_A},
        confirmed_b={"refs/heads/main": SHA_A, "refs/heads/protected": SHA_A},
        current_b={
            "refs/heads/main": SHA_B,
            "refs/heads/protected": SHA_B,
            "refs/heads/other": SHA_C,
        },
        current_a={"refs/heads/main": SHA_A, "refs/heads/protected": SHA_A},
        selected_refs={"refs/heads/main", "refs/heads/protected"},
        protected_refs={"refs/heads/protected"},
        is_ancestor=lambda _old, _new: False,
    )

    assert plan.changes == ()
    assert plan.conflicts == (
        RefConflict("refs/heads/main", "non-fast-forward", "B change is not a fast-forward"),
        RefConflict("refs/heads/protected", "protected-ref", "selected ref is protected"),
    )
    assert plan.unselected_refs == ("refs/heads/other",)


def test_symbolic_or_internal_selected_ref_is_a_visible_limitation() -> None:
    plan = compare_return_refs(
        original_a={"refs/pull/1/head": SHA_A},
        confirmed_b={"refs/pull/1/head": SHA_A},
        current_b={"refs/pull/1/head": SHA_B},
        current_a={"refs/pull/1/head": SHA_A},
    )

    assert plan.changes == ()
    assert plan.conflicts == (
        RefConflict(
            "refs/pull/1/head",
            "unsupported-ref",
            "only branch and tag refs can be returned",
        ),
    )


@pytest.mark.parametrize("value", [{"main": SHA_A}, {"refs/heads/main": "not-an-oid"}])
def test_ref_maps_are_strict(value: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="invalid ref map"):
        compare_return_refs(value, {}, {}, {})

