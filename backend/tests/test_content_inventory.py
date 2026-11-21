from __future__ import annotations

from copy import deepcopy

import pytest
from gitferry.content_inventory import (
    CONTENT_CLASSES,
    TRANSFER_CLASSES,
    InventoryError,
    comparison_facts,
    inventory_digest,
    inventory_summary,
    parse_inventory,
)


def _observation(content_class: str, *, status: str = "observed", reason: str = "") -> dict:
    value: dict = {
        "content_class": content_class,
        "status": status,
        "count": 1 if status == "observed" else None,
        "objects": (
            [{"key": f"{content_class}-1", "namespace": "repo", "facts": {"kind": content_class}}]
            if status == "observed"
            else []
        ),
        "reason": reason,
        "limitations": [],
    }
    return value


def _document(*, requested_classes: list[str] | None = None) -> dict:
    return {
        "format": "gitferry.content-inventory",
        "schema_version": 1,
        "comparison_version": 1,
        "snapshot_id": "snapshot-a",
        "created_at": "2026-11-21T10:00:00Z",
        "source_instance_key": "source.synthetic",
        "source_installation_marker": "installation-2026-11",
        "gitea_version": "1.27.2",
        "repositories": [
            {
                "repository": "acme/one",
                "native_id": "90071992547409931234567890",
                "requested_classes": requested_classes or list(CONTENT_CLASSES),
                "classes": [_observation(name) for name in CONTENT_CLASSES],
                "git_size_estimate_bytes": 64,
                "size_assumptions": [],
            }
        ],
        "deletion_semantics": "absence-does-not-delete",
    }


def test_inventory_requires_exact_catalog_and_observed_counts() -> None:
    value = _document()
    value["repositories"][0]["classes"] = value["repositories"][0]["classes"][:-1]
    with pytest.raises(InventoryError):
        parse_inventory(value)

    value = _document()
    value["repositories"][0]["classes"][0]["count"] = 2
    with pytest.raises(InventoryError):
        parse_inventory(value)


def test_unobserved_classes_cannot_claim_zero_or_omit_reason() -> None:
    for count, reason in ((0, "temporarily unavailable"), (None, "")):
        value = _document()
        observation = value["repositories"][0]["classes"][0]
        observation.update(status="not-supported", count=count, objects=[], reason=reason)
        with pytest.raises(InventoryError):
            parse_inventory(value)


def test_duplicate_object_identity_and_selected_repository_identity_are_rejected() -> None:
    value = _document()
    objects = value["repositories"][0]["classes"][0]["objects"]
    objects.append(deepcopy(objects[0]))
    value["repositories"][0]["classes"][0]["count"] = 2
    with pytest.raises(InventoryError):
        parse_inventory(value)

    value = _document()
    value["repositories"].append(deepcopy(value["repositories"][0]))
    with pytest.raises(InventoryError):
        parse_inventory(value)

    value = _document()
    value["repositories"].append(deepcopy(value["repositories"][0]))
    value["repositories"][1]["repository"] = "acme/two"
    with pytest.raises(InventoryError):
        parse_inventory(value)


def test_native_ids_are_strict_precise_strings_and_repository_scoped() -> None:
    document = parse_inventory(_document())
    assert document.repositories[0].native_id == "90071992547409931234567890"

    value = _document()
    value["repositories"][0]["native_id"] = 90071992547409931234567890
    with pytest.raises(InventoryError):
        parse_inventory(value)

    value = _document()
    second = deepcopy(value["repositories"][0])
    second["repository"] = "acme/two"
    second["native_id"] = value["repositories"][0]["native_id"]
    value["repositories"].append(second)
    with pytest.raises(InventoryError):
        parse_inventory(value)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("format", "other.inventory"),
        ("comparison_version", 2),
        ("unexpected", True),
    ],
)
def test_unknown_format_versions_and_fields_are_rejected(field: str, invalid: object) -> None:
    value = _document()
    if field == "unexpected":
        value[field] = invalid
    else:
        value[field] = invalid
    with pytest.raises(InventoryError):
        parse_inventory(value)


def test_boolean_versions_are_not_accepted_as_numeric_versions() -> None:
    value = _document()
    value["schema_version"] = True
    with pytest.raises(InventoryError):
        parse_inventory(value)


def test_summary_distinguishes_complete_ready_full_transfer_and_exclusions() -> None:
    complete = parse_inventory(_document())
    summary = inventory_summary(complete)
    assert summary["inventory_complete"] is True
    assert summary["transfer_ready"] is False
    assert summary["full_transfer"] is False
    assert {item["content_class"] for item in summary["blockers"]} == set(CONTENT_CLASSES) - set(
        TRANSFER_CLASSES
    )

    requested = list(TRANSFER_CLASSES)
    excluded = parse_inventory(_document(requested_classes=requested))
    summary = inventory_summary(excluded)
    assert summary["inventory_complete"] is True
    assert summary["transfer_ready"] is True
    assert summary["full_transfer"] is False
    assert {item["content_class"] for item in summary["excluded_classes"]} == set(
        CONTENT_CLASSES
    ) - set(TRANSFER_CLASSES)


@pytest.mark.parametrize("status", ["not-supported", "failed", "blocked"])
def test_requested_unsupported_or_failed_content_blocks_transfer(status: str) -> None:
    value = _document(requested_classes=["issues"])
    observation = next(
        item for item in value["repositories"][0]["classes"] if item["content_class"] == "issues"
    )
    observation.update(status=status, count=None, objects=[], reason=f"synthetic {status}")
    document = parse_inventory(value)
    summary = inventory_summary(document)
    assert summary["inventory_complete"] is False
    assert summary["transfer_ready"] is False
    assert summary["full_transfer"] is False
    assert summary["blockers"] == [
        {"repository": "acme/one", "content_class": "issues", "reason": f"synthetic {status}"}
    ]


def test_digest_tracks_facts_but_comparison_ignores_only_snapshot_and_time() -> None:
    value = _document()
    document = parse_inventory(value)
    changed_observation = deepcopy(value)
    changed_observation["repositories"][0]["classes"][0]["objects"][0]["facts"]["kind"] = "changed"
    changed = parse_inventory(changed_observation)
    assert inventory_digest(changed) != inventory_digest(document)
    assert comparison_facts(changed) != comparison_facts(document)

    changed_observation["snapshot_id"] = "snapshot-b"
    changed_observation["created_at"] = "2026-11-22T10:00:00Z"
    observed = parse_inventory(changed_observation)
    assert inventory_digest(observed) != inventory_digest(changed)
    assert comparison_facts(observed) == comparison_facts(changed)
    assert comparison_facts(observed)["source_installation_marker"] == "installation-2026-11"
