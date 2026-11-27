"""Reconcile the complete 0.4 content catalog with executed integration evidence."""

from __future__ import annotations

from typing import Any

from gitea_compat import PRIMARY_GITEA_VERSION

SUPPORTED_FIRST_IMPORT = frozenset(
    {
        "git_refs",
        "organizations",
        "teams",
        "permissions",
        "identities",
        "issues",
        "labels",
        "comments",
        "attachments",
        "releases",
        "release_assets",
        "lfs",
        "wiki",
    }
)

CLASS_POLICIES: dict[str, dict[str, str]] = {
    "git_refs": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "organizations": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "teams": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "permissions": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "identities": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "issues": {"first_import": "supported-with-limits", "return": "supported-with-limits"},
    "labels": {"first_import": "supported-with-limits", "return": "not-checked"},
    "comments": {"first_import": "supported-with-limits", "return": "supported-with-limits"},
    "attachments": {"first_import": "supported-with-limits", "return": "not-checked"},
    "releases": {"first_import": "supported-with-limits", "return": "not-checked"},
    "release_assets": {"first_import": "supported-with-limits", "return": "not-checked"},
    "lfs": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "wiki": {"first_import": "supported-with-limits", "return": "not-implemented"},
    "milestones": {"first_import": "not-supported", "return": "not-supported"},
    "pull_comments": {"first_import": "not-supported", "return": "not-supported"},
    "pull_requests": {"first_import": "not-supported", "return": "not-supported"},
    "reviews": {"first_import": "not-supported", "return": "not-supported"},
    "review_comments": {"first_import": "not-supported", "return": "not-supported"},
    "timelines": {"first_import": "not-supported", "return": "not-supported"},
    "reactions": {"first_import": "not-supported", "return": "not-supported"},
    "other_content": {"first_import": "not-supported", "return": "not-supported"},
}


def _value(report: dict[str, Any], path: str) -> Any:
    value: Any = report
    for segment in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _all_true(report: dict[str, Any], paths: tuple[str, ...]) -> bool:
    return all(_value(report, path) is True for path in paths)


def _first_import_verified(report: dict[str, Any], content_class: str) -> bool:
    common = "application_plans.assertions."
    paths = {
        "git_refs": (
            "source_exports.assertions.actual_git_bundle_captured",
            common + "structure_bundle_and_metadata_confirmed",
        ),
        "organizations": (common + "structure_bundle_and_metadata_confirmed",),
        "teams": (common + "structure_bundle_and_metadata_confirmed",),
        "permissions": (common + "structure_bundle_and_metadata_confirmed",),
        "identities": (
            "source_exports.assertions.issue_comment_distinct_author_claims",
            common + "distinct_native_authors",
        ),
        "issues": (common + "portable_metadata_confirmed",),
        "labels": (common + "label_assignment_uses_mapped_label",),
        "comments": (common + "portable_metadata_confirmed",),
        "attachments": (common + "attachment_parent_and_bytes_confirmed",),
        "releases": (common + "release_author_and_asset_confirmed",),
        "release_assets": (common + "release_author_and_asset_confirmed",),
        "lfs": (
            "native_payloads.cases.normal.applied.lfs_remote_bytes_verified",
            "native_payloads.assertions.independent_state_file_only_transfer",
        ),
        "wiki": (
            "native_payloads.cases.normal.applied.wiki_refs_and_content_match",
            "native_payloads.assertions.native_lfs_digest_and_wiki_history",
        ),
    }
    return _all_true(report, paths[content_class])


def _limitation(content_class: str) -> str:
    return {
        "git_refs": "Selected refs only; deletion and a guided return plan are not implemented.",
        "organizations": (
            "Technical ownership remains local; organization deletion is not transferred."
        ),
        "teams": "Only qualified team units and repository relations are transferred.",
        "permissions": "Only qualified repository code and issue units are claimed.",
        "identities": "Explicit realm-qualified local bindings remain mandatory.",
        "issues": "Native historical creation timestamps are not restored by regular create APIs.",
        "labels": "Assignment replacement and complete return are not qualified.",
        "comments": "Native historical creation timestamps remain separate origin facts.",
        "attachments": (
            "External URLs, deletion, native author and timestamp fidelity are unsupported."
        ),
        "releases": "Deletion and complete return are not qualified.",
        "release_assets": "Replacement, deletion and complete return are not qualified.",
        "lfs": "Standard v1 pointers and same-origin basic actions only; no guided return plan.",
        "wiki": "Native wiki refs only; no deletion, conversion or guided return plan.",
        "milestones": "Inventory-only; no portable operation adapter.",
        "pull_comments": "Inventory-only; no portable pull-comment adapter.",
        "pull_requests": "Inventory-only; portable refs, authors and baselines are unresolved.",
        "reviews": "Inventory-only; commit, decision and author fidelity are unresolved.",
        "review_comments": (
            "Inventory-only; parent, diff position and update semantics are unresolved."
        ),
        "timelines": "The Gitea timeline API is read-only.",
        "reactions": "Natural-key API lacks stable native ID and historical timestamp input.",
        "other_content": (
            "Unclassified content remains unknown and blocks a complete transfer claim."
        ),
    }[content_class]


def build_scope_acceptance(report: dict[str, Any]) -> dict[str, Any]:
    """Build a truthful per-class acceptance catalog from already executed probes."""

    counts = _value(report, "content_inventory.counts")
    direct_counts = _value(report, "content_inventory.direct_counts")
    summary = _value(report, "content_inventory.selection_summary")
    counts = counts if isinstance(counts, dict) else {}
    direct_counts = direct_counts if isinstance(direct_counts, dict) else {}
    blockers = summary.get("blockers", []) if isinstance(summary, dict) else []
    blocked_classes = {
        item.get("content_class")
        for item in blockers
        if isinstance(item, dict) and isinstance(item.get("content_class"), str)
    }
    catalog: list[dict[str, Any]] = []
    for content_class, policy in CLASS_POLICIES.items():
        if content_class == "lfs":
            inventory_status = "native-qualified"
            inventory_verified = (
                _value(report, "native_payloads.cases.normal.source.inventory_status") == "ready"
            )
        elif content_class == "other_content":
            inventory_status = "unknown-blocked"
            inventory_verified = (
                counts.get(content_class) is None
                and direct_counts.get(content_class) is None
                and content_class in blocked_classes
            )
        else:
            inventory_status = "observed"
            count = counts.get(content_class)
            inventory_verified = (
                type(count) is int and count >= 0 and count == direct_counts.get(content_class)
            )
        supported = content_class in SUPPORTED_FIRST_IMPORT
        first_import_verified = (
            _first_import_verified(report, content_class)
            if supported
            else content_class in blocked_classes
        )
        catalog.append(
            {
                "content_class": content_class,
                "release_owner": "0.4.0",
                "inventory_status": inventory_status,
                "inventory_count": counts.get(content_class),
                "inventory_verified": inventory_verified,
                "first_import_status": policy["first_import"],
                "first_import_verified": first_import_verified,
                "return_status": policy["return"],
                "payload_integrity": ("verified" if supported else "not-applicable-no-payload"),
                "limitation": _limitation(content_class),
                "evidence": [
                    "content_inventory",
                    "native_payloads" if content_class in {"lfs", "wiki"} else "application_plans",
                ],
            }
        )
    assertions = {
        "exact_gitea_baseline": report.get("gitea")
        == {"source": PRIMARY_GITEA_VERSION, "target": PRIMARY_GITEA_VERSION},
        "all_classes_catalogued": set(CLASS_POLICIES)
        == {entry["content_class"] for entry in catalog},
        "inventory_counts_reconciled": all(entry["inventory_verified"] for entry in catalog),
        "supported_first_imports_reconciled": all(
            entry["first_import_verified"]
            for entry in catalog
            if entry["content_class"] in SUPPORTED_FIRST_IMPORT
        ),
        "unsupported_classes_blocked_before_write": (
            isinstance(summary, dict)
            and summary.get("transfer_ready") is False
            and all(
                entry["first_import_verified"]
                for entry in catalog
                if entry["content_class"] not in SUPPORTED_FIRST_IMPORT
            )
        ),
        "omission_never_deletes": _value(
            report, "application_plans.assertions.metadata_omission_no_deletion"
        )
        is True,
        "control_repository_unchanged": _value(
            report, "application_plans.assertions.control_repository_unchanged"
        )
        is True,
        "desktop_inventory_reviewed": _all_true(
            report,
            (
                "source_exports.assertions.guided_browser_inventory_bound_export",
                "source_exports.source_browser.inventory_reviewed",
            ),
        ),
    }
    return {
        "status": "passed" if all(assertions.values()) else "failed",
        "gitea_version": PRIMARY_GITEA_VERSION,
        "catalog": catalog,
        "assertions": assertions,
        "complete_transfer_claimed": False,
        "limited_scope_required": True,
    }
