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
    "git_refs": {"first_import": "supported-with-limits", "return": "supported-with-limits"},
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


def _teams_verified(report: dict[str, Any]) -> bool:
    configuration = _value(report, "identity_and_permissions.team_configuration")
    if not isinstance(configuration, dict):
        return False
    required = {
        "permission",
        "repo.code",
        "repo.issues",
        "selected_repository_assigned",
        "control_repository_not_assigned",
    }
    return all(
        isinstance(configuration.get(name), dict)
        and all(configuration[name].get(field) is True for field in required)
        for name in ("readers", "writers")
    )


def _permissions_verified(report: dict[str, Any]) -> bool:
    access = _value(report, "identity_and_permissions.effective_git_access")
    return isinstance(access, dict) and all(
        access.get(name) is True
        for name in (
            "reader_can_read",
            "reader_push_denied",
            "reader_branch_absent",
            "writer_can_read",
            "writer_push_allowed",
            "writer_branch_present",
            "unmapped_same_login_account_denied",
            "late_mapping_denied_before_assignment",
            "late_mapping_allowed_after_assignment",
        )
    )


def _identities_verified(report: dict[str, Any]) -> bool:
    mappings = _value(report, "identity_and_permissions.explicit_mappings")
    differences = _value(report, "identity_and_permissions.source_target_id_differences")
    admin_flags = _value(report, "identity_and_permissions.functional_user_admin_flags")
    if not all(isinstance(value, dict) and value for value in (mappings, differences, admin_flags)):
        return False
    mappings_ok = all(
        isinstance(item, dict)
        and item.get("resolution") == "resolved"
        and isinstance(item.get("source"), dict)
        and bool(item["source"].get("realm"))
        and bool(item["source"].get("external_id"))
        and bool(item["source"].get("ou_path"))
        and isinstance(item.get("target"), dict)
        and type(item["target"].get("gitea_id")) is int
        and bool(item["target"].get("login"))
        for item in mappings.values()
    )
    differences_ok = all(
        isinstance(item, dict)
        and type(item.get("source")) is int
        and type(item.get("target")) is int
        and item["source"] != item["target"]
        for item in differences.values()
    )
    return mappings_ok and differences_ok and all(value is False for value in admin_flags.values())


def _first_import_verified(report: dict[str, Any], content_class: str) -> bool:
    common = "application_plans.assertions."
    paths = {
        "git_refs": (
            "source_exports.assertions.actual_git_bundle_captured",
            "durable_provisioning.assertions.bundle_refs_match_source",
        ),
        "organizations": ("durable_provisioning.assertions.expected_private_structure_created",),
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
    if content_class == "teams":
        return _teams_verified(report)
    if content_class == "permissions":
        return _permissions_verified(report)
    if content_class == "identities":
        return _identities_verified(report)
    return _all_true(report, paths[content_class])


def _return_verified(report: dict[str, Any], content_class: str) -> bool:
    if content_class == "git_refs":
        return (
            _value(report, "source_exports.assertions.git_return_ref_object_exact") is True
            and _value(report, "source_exports.assertions.git_return_clone_reachable_object")
            is True
        )
    if content_class == "issues":
        return _value(report, "metadata_roundtrip.status") == "passed"
    if content_class == "comments":
        return _value(report, "comment_transport_roundtrip.status") == "passed"
    return False


def _payload_integrity(content_class: str) -> str:
    if content_class in {"git_refs", "attachments", "release_assets", "lfs", "wiki"}:
        return "verified-binary-or-ref"
    if content_class in SUPPORTED_FIRST_IMPORT:
        return "verified-structured-data"
    return "not-applicable-no-payload"


def _evidence_sections(content_class: str) -> list[str]:
    return {
        "git_refs": ["content_inventory", "source_exports", "durable_provisioning"],
        "organizations": ["content_inventory", "durable_provisioning"],
        "teams": ["content_inventory", "identity_and_permissions"],
        "permissions": ["content_inventory", "identity_and_permissions"],
        "identities": ["content_inventory", "identity_and_permissions"],
        "issues": ["content_inventory", "application_plans", "metadata_roundtrip"],
        "labels": ["content_inventory", "application_plans"],
        "comments": ["content_inventory", "application_plans", "comment_transport_roundtrip"],
        "attachments": ["content_inventory", "application_plans"],
        "releases": ["content_inventory", "application_plans"],
        "release_assets": ["content_inventory", "application_plans"],
        "lfs": ["content_inventory", "native_payloads"],
        "wiki": ["content_inventory", "native_payloads"],
    }.get(content_class, ["content_inventory"])


def _limitation(content_class: str) -> str:
    return {
        "git_refs": (
            "Bounded ordinary branch/tag refs only; deletion, force updates, LFS/wiki and "
            "metadata return remain outside this contract."
        ),
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
    blockers_value = summary.get("blockers") if isinstance(summary, dict) else None
    blockers = blockers_value if isinstance(blockers_value, list) else []
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
                "return_verified": _return_verified(report, content_class),
                "payload_integrity": _payload_integrity(content_class),
                "limitation": _limitation(content_class),
                "evidence": _evidence_sections(content_class),
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
