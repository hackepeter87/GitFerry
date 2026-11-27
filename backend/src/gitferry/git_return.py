"""Pure comparison rules for the bounded Git A-to-B-to-A return flow."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from typing import Literal

from .git_refs import is_ref_map

RefKind = Literal["branch", "tag"]


@dataclass(frozen=True)
class RefConflict:
    ref: str
    code: str
    reason: str


@dataclass(frozen=True)
class RefReturnPlan:
    changes: tuple[dict[str, str | None], ...]
    conflicts: tuple[RefConflict, ...]
    unselected_refs: tuple[str, ...]


def _validate_refs(name: str, refs: Mapping[str, str]) -> dict[str, str]:
    value = dict(refs)
    if not is_ref_map(value):
        raise ValueError(f"invalid ref map: {name}")
    return value


def _kind(ref: str) -> RefKind:
    if ref.startswith("refs/heads/"):
        return "branch"
    if ref.startswith("refs/tags/"):
        return "tag"
    raise ValueError(f"unsupported symbolic or non-branch ref: {ref}")


def compare_return_refs(
    original_a: Mapping[str, str],
    confirmed_b: Mapping[str, str],
    current_b: Mapping[str, str],
    current_a: Mapping[str, str],
    *,
    selected_refs: Set[str] | None = None,
    protected_refs: Set[str] = frozenset(),
    is_ancestor: Callable[[str, str], bool] | None = None,
) -> RefReturnPlan:
    """Compare independent A/B observations and return safe ref updates.

    ``is_ancestor`` is deliberately injected because an object-id comparison alone
    cannot establish a fast-forward relationship. Missing ancestry proof blocks a
    branch update instead of permitting an unchecked force update.
    """

    baseline = _validate_refs("original_a", original_a)
    imported = _validate_refs("confirmed_b", confirmed_b)
    desired = _validate_refs("current_b", current_b)
    target = _validate_refs("current_a", current_a)
    selected = set(selected_refs) if selected_refs is not None else set(imported) | set(desired)
    if not selected.issubset(set(baseline) | set(imported) | set(desired) | set(target)):
        raise ValueError("selected refs must be present in one of the ref maps")
    if not set(protected_refs).issubset(selected):
        raise ValueError("protected refs must be selected refs")
    changes: list[dict[str, str | None]] = []
    conflicts: list[RefConflict] = []
    for ref in sorted(selected):
        try:
            kind = _kind(ref)
        except ValueError:
            conflicts.append(
                RefConflict(
                    ref,
                    "unsupported-ref",
                    "only branch and tag refs can be returned",
                )
            )
            continue
        imported_oid = imported.get(ref)
        desired_oid = desired.get(ref)
        baseline_oid = baseline.get(ref)
        target_oid = target.get(ref)
        if desired_oid == imported_oid:
            continue
        if desired_oid is None:
            conflicts.append(
                RefConflict(ref, "deletion-unsupported", "ref deletion is not supported")
            )
            continue
        if ref in protected_refs:
            conflicts.append(RefConflict(ref, "protected-ref", "selected ref is protected"))
            continue
        if kind == "tag" and imported_oid is not None:
            conflicts.append(
                RefConflict(
                    ref, "tag-rewrite-unsupported", "existing tag changes are not supported"
                )
            )
            continue
        if target_oid != baseline_oid:
            conflicts.append(RefConflict(ref, "a-drift", "selected A ref changed since export"))
            continue
        if kind == "branch" and imported_oid is not None:
            if is_ancestor is None or not is_ancestor(imported_oid, desired_oid):
                conflicts.append(
                    RefConflict(ref, "non-fast-forward", "B change is not a fast-forward")
                )
                continue
        changes.append({"ref": ref, "old_oid": target_oid, "new_oid": desired_oid})
    unselected = tuple(sorted((set(imported) | set(desired) | set(target)) - selected))
    return RefReturnPlan(tuple(changes), tuple(conflicts), unselected)

