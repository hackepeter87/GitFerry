"""Real target-only execution and interruption evidence for the durable application plan."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
from connection_profile_probe import certificate
from gitferry.application_package import write_package
from gitferry.content_inventory import CONTENT_CLASSES, parse_inventory
from gitferry.git_bundle import create_git_bundle
from gitferry.package_archive import write_package_archive
from gitferry.package_inventory import attach_inventory
from support import (
    ADMIN,
    PACKAGE,
    ROOT,
    TARGET_URL,
    USER_PASSWORD,
    WORK,
    ProbeFailure,
    api,
    docker_copy,
    docker_service_exec,
)

REPOSITORY = "application-plan-lab/selected"
REMOTE_ROOT = "/state/application-plans"
ASSERTIONS = frozenset(
    {
        "structure_bundle_and_metadata_confirmed",
        "distinct_native_authors",
        "parent_mapping_confirmed",
        "target_only_context",
        "application_package_bundle_received_and_used",
        "portable_metadata_confirmed",
        "label_assignment_uses_mapped_label",
        "attachment_parent_and_bytes_confirmed",
        "release_author_and_asset_confirmed",
        "metadata_update_same_ids_and_timestamps",
        "metadata_update_replay_no_writes",
        "label_and_release_collisions_blocked",
        "metadata_omission_no_deletion",
        "metadata_drift_blocks_overwrite",
        "confirmed_restart_no_duplicates",
        "crash_remote_post_accepted",
        "crash_restart_outcome_unknown",
        "crash_no_second_post",
        "lost_remote_post_accepted",
        "lost_restart_outcome_unknown",
        "lost_no_second_post",
        "asset_lost_remote_post_accepted",
        "asset_lost_restart_outcome_unknown",
        "asset_lost_no_second_post",
        "asset_lost_repackage_still_blocked",
        "new_packaging_cannot_bypass_unknown",
        "explicit_resolution_rejects_wrong_parent_author_and_content",
        "explicit_resolution_then_restart_confirms_without_post",
        "control_repository_unchanged",
        "credentials_absent_from_state_and_report",
        "source_catalog_lists_real_organization_and_repository_without_writes",
    }
)

ISSUE_ASSET = b"portable issue attachment\n"
RELEASE_ASSET = b"portable release asset\n"


def _observation(content_class: str) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    if content_class == "git_refs":
        objects = [
            {
                "key": "refs/heads/main",
                "namespace": "git-ref",
                "facts": {"ref": "refs/heads/main", "sha": "0" * 40},
            }
        ]
    elif content_class == "organizations":
        objects = [
            {
                "key": "50",
                "native_id": "50",
                "namespace": "organization-id",
                "facts": {"name": "application-plan-lab"},
            }
        ]
    elif content_class == "issues":
        objects = [
            {
                "key": "53",
                "native_id": "53",
                "namespace": "global-issue-id",
                "issue_index": "1",
                "author": {"account_id": "11", "login": "issue-author"},
                "facts": {
                    "title": "Application plan",
                    "body": "Issue body",
                    "state": "open",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
            }
        ]
    elif content_class == "labels":
        objects = [
            {
                "key": "57",
                "native_id": "57",
                "namespace": "label-id",
                "facts": {
                    "name": "portable-priority",
                    "color": "336699",
                    "description": "Portable synthetic label",
                },
            }
        ]
    elif content_class == "attachments":
        objects = [
            {
                "key": "59",
                "native_id": "59",
                "namespace": "attachment-id",
                "parent": {"content_class": "issues", "key": "53"},
                "facts": {
                    "uuid": "source-issue-asset",
                    "name": "issue-proof.txt",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                "size_bytes": len(ISSUE_ASSET),
            }
        ]
    elif content_class == "releases":
        objects = [
            {
                "key": "58",
                "native_id": "58",
                "namespace": "release-id",
                "author": {"account_id": "11", "login": "issue-author"},
                "facts": {
                    "tag_name": "portable-v1",
                    "target_commitish": "main",
                    "name": "Portable release",
                    "body": "Portable release notes",
                    "draft": False,
                    "prerelease": False,
                    "created_at": "2026-01-01T00:00:00Z",
                    "published_at": "2026-01-01T00:00:00Z",
                },
            }
        ]
    elif content_class == "release_assets":
        objects = [
            {
                "key": "60",
                "native_id": "60",
                "namespace": "release-asset-id",
                "parent": {"content_class": "releases", "key": "58"},
                "facts": {
                    "uuid": "source-release-asset",
                    "name": "release-proof.txt",
                    "created_at": "2026-01-01T00:00:00Z",
                },
                "size_bytes": len(RELEASE_ASSET),
            }
        ]
    requested = content_class in {
        "git_refs",
        "organizations",
        "teams",
        "permissions",
        "issues",
        "comments",
        "labels",
        "attachments",
        "releases",
        "release_assets",
    }
    return {
        "content_class": content_class,
        "status": "observed" if requested else "excluded",
        "count": len(objects) if requested else None,
        "objects": objects if requested else [],
        "reason": "" if requested else "Not selected for this bounded package",
        "limitations": [],
    }


def _portable_content() -> list[dict[str, Any]]:
    def origin(kind: str, identifier: str) -> dict[str, str]:
        return {
            "instance_key": "source",
            "repository": REPOSITORY,
            "object_type": kind,
            "global_id": identifier,
        }

    return [
        {
            "kind": "label",
            "origin": origin("label", "57"),
            "repository": REPOSITORY,
            "source_native_id": "57",
            "values": {
                "name": "portable-priority",
                "color": "336699",
                "description": "Portable synthetic label",
            },
        },
        {
            "kind": "release",
            "origin": origin("release", "58"),
            "repository": REPOSITORY,
            "source_native_id": "58",
            "author": {
                "realm_key": "synthetic-source",
                "login": "issue-author",
                "external_id": "source-issue-author",
                "ou_path": "OU=Source",
            },
            "values": {
                "tag_name": "portable-v1",
                "target_commitish": "main",
                "name": "Portable release",
                "body": "Portable release notes",
                "draft": False,
                "prerelease": False,
            },
            "source_created_at": "2026-01-01T00:00:00Z",
            "source_published_at": "2026-01-01T00:00:00Z",
        },
        {
            "kind": "attachment",
            "origin": origin("attachment", "59"),
            "repository": REPOSITORY,
            "source_native_id": "59",
            "parent_origin": origin("issue", "53"),
            "name": "issue-proof.txt",
            "media_type": "text/plain",
            "source_created_at": "2026-01-01T00:00:00Z",
            "file": {
                "path": "assets/issue-proof.txt",
                "size": len(ISSUE_ASSET),
                "sha256": hashlib.sha256(ISSUE_ASSET).hexdigest(),
            },
        },
        {
            "kind": "release_asset",
            "origin": origin("release_asset", "60"),
            "repository": REPOSITORY,
            "source_native_id": "60",
            "parent_origin": origin("release", "58"),
            "name": "release-proof.txt",
            "media_type": "text/plain",
            "source_created_at": "2026-01-01T00:00:00Z",
            "file": {
                "path": "assets/release-proof.txt",
                "size": len(RELEASE_ASSET),
                "sha256": hashlib.sha256(RELEASE_ASSET).hexdigest(),
            },
        },
    ]


def worker(
    mode: str, case: str = "initial", expected: int = 0, *, target_comment_id: str | None = None
) -> dict[str, Any]:
    command = ["python", "/probe/plan_worker.py", mode, "--case", case]
    if target_comment_id is not None:
        command.extend(["--target-comment-id", target_comment_id])
    result = docker_service_exec(
        "gitferry-b",
        command,
        check=False,
        timeout=90,
    )
    if result.returncode != expected:
        raise ProbeFailure(
            f"Application plan worker {mode} exited {result.returncode}; expected {expected}"
        )
    if expected:
        return {}
    return json.loads(result.stdout)


def events() -> list[dict[str, Any]]:
    value = docker_service_exec("gitferry-b", ["cat", REMOTE_ROOT + "/remote-events.jsonl"])
    return [json.loads(line) for line in value.stdout.splitlines()]


def comments() -> list[dict[str, Any]]:
    return api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/comments")


def run_application_plan_probe() -> dict[str, Any]:
    package_root = WORK / "application-package"
    package_root.mkdir()
    (package_root / "assets").mkdir()
    (package_root / "assets/issue-proof.txt").write_bytes(ISSUE_ASSET)
    (package_root / "assets/release-proof.txt").write_bytes(RELEASE_ASSET)
    bundle = create_git_bundle(WORK / "permission-seed", package_root, "bundles/selected.bundle")
    package_document = json.loads((ROOT / "examples/application-package.v1.json").read_text())
    package_document["sender"] = {
        "instance_key": "source",
        "installation_marker": "source-plan-install",
    }
    package_document["recipient"] = {
        "instance_key": "target",
        "installation_marker": "target-plan-install",
    }
    selected = package_document["repositories"][0]
    selected["repository"] = REPOSITORY
    selected["organization"]["name"] = "application-plan-lab"
    selected["bundle"] = {"path": bundle.member, "size": bundle.size, "sha256": bundle.sha256}
    selected["lfs"] = None
    selected["wiki"] = None
    package_document["realms"] = [
        {
            "realm_key": "synthetic-source",
            "kind": "external",
            "description": "Synthetic source directory",
        }
    ]
    requested_classes = {
        "git_refs",
        "organizations",
        "teams",
        "permissions",
        "issues",
        "comments",
        "labels",
        "attachments",
        "releases",
        "release_assets",
    }
    inventory = parse_inventory(
        {
            "format": "gitferry.content-inventory",
            "schema_version": 3,
            "comparison_version": 3,
            "snapshot_id": "application-plan-v4",
            "created_at": "2026-01-01T00:00:00Z",
            "source_instance_key": "source",
            "source_installation_marker": "source-plan-install",
            "gitea_version": "28.4.0",
            "deletion_semantics": "absence-does-not-delete",
            "repositories": [
                {
                    "repository": REPOSITORY,
                    "native_id": "51",
                    "requested_classes": sorted(requested_classes),
                    "classes": [_observation(name) for name in CONTENT_CLASSES],
                    "git_size_estimate_bytes": bundle.size,
                    "size_assumptions": [],
                }
            ],
        }
    )
    attach_inventory(
        package_root,
        package_document,
        inventory,
        {
            "repositories": [
                {
                    "repository": REPOSITORY,
                    "global_id": "51",
                    "organization": {"global_id": "50"},
                    "teams": [],
                }
            ]
        },
        include_members=False,
        include_labels=True,
        include_attachments=True,
        include_releases=True,
        include_release_assets=True,
    )
    package_document["content"] = _portable_content()
    package_artifact = write_package(package_root, package_document)
    write_package_archive(package_root, PACKAGE / "application-plan.zip")
    directory = WORK / "application-plan-tls"
    directory.mkdir()
    cert, key = certificate(directory, "trusted")
    docker_service_exec("gitferry-b", ["mkdir", "-p", REMOTE_ROOT])
    docker_copy(cert, "gitferry-b:" + REMOTE_ROOT + "/trusted.pem")
    docker_copy(key, "gitferry-b:" + REMOTE_ROOT + "/trusted.key")
    control = api(TARGET_URL, "GET", "/repos/access-lab/control-private")
    first = worker("initial")
    plan = first["plan"]
    if plan["status"] != "confirmed":
        # The plan stores only non-secret intents; retain useful bounded diagnostics.
        (WORK / "application-plan-failure.json").write_text(json.dumps(plan, indent=2))
        raise ProbeFailure(
            "Application plan did not confirm; inspect application-plan-failure.json"
        )
    issue = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1")
    initial_comments = comments()
    labels = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/labels")
    target_label = next(value for value in labels if value["name"] == "portable-priority")
    release = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases/tags/portable-v1")
    issue_assets = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/assets")
    release_assets = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases/{release['id']}/assets")
    assertions = {
        "structure_bundle_and_metadata_confirmed": all(
            step["status"] == "confirmed" for step in plan["operations"]
        )
        and any(step["operation_type"] == "push_git_bundle" for step in plan["operations"]),
        "distinct_native_authors": issue["user"]["login"] == "issue-author"
        and len(initial_comments) == 1
        and initial_comments[0]["user"]["login"] == "comment-author",
        "parent_mapping_confirmed": len(initial_comments) == 1
        and initial_comments[0]["issue_url"].endswith("/1"),
        "target_only_context": first["isolated"] is True,
        "application_package_bundle_received_and_used": (
            first["package_digest"] == package_artifact.digest_sha256
            and first["isolated"] is True
            and any(
                step["operation_type"] == "push_git_bundle" and step["status"] == "confirmed"
                for step in plan["operations"]
            )
        ),
        "portable_metadata_confirmed": all(
            any(
                step["operation_type"] == kind and step["status"] == "confirmed"
                for step in plan["operations"]
            )
            for kind in (
                "create_label",
                "create_release",
                "upload_attachment",
                "upload_release_asset",
            )
        ),
        "label_assignment_uses_mapped_label": len(issue["labels"]) == 1
        and issue["labels"][0]["name"] == "portable-priority"
        and any(value["id"] == issue["labels"][0]["id"] for value in labels),
        "attachment_parent_and_bytes_confirmed": len(issue_assets) == 1
        and issue_assets[0]["name"] == "issue-proof.txt"
        and httpx.get(
            f"{TARGET_URL}/attachments/{issue_assets[0]['uuid']}", auth=ADMIN, timeout=15
        ).content
        == ISSUE_ASSET,
        "release_author_and_asset_confirmed": release["author"]["login"] == "issue-author"
        and len(release_assets) == 1
        and release_assets[0]["name"] == "release-proof.txt"
        and httpx.get(
            f"{TARGET_URL}/attachments/{release_assets[0]['uuid']}", auth=ADMIN, timeout=15
        ).content
        == RELEASE_ASSET,
    }
    before = events()
    catalog = worker("browse")
    assertions["source_catalog_lists_real_organization_and_repository_without_writes"] = (
        any(item["name"] == "application-plan-lab" for item in catalog["organizations"]["entries"])
        and any(item["repository"] == REPOSITORY for item in catalog["repositories"]["entries"])
        and catalog["repositories"]["inventory_complete"] is False
        and events() == before
    )
    replay = worker("replay")
    assertions["confirmed_restart_no_duplicates"] = (
        replay["plan"]["status"] == "confirmed"
        and events() == before
        and comments() == initial_comments
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/labels")) == len(labels)
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases")) == 1
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/assets")) == 1
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases/{release['id']}/assets")) == 1
    )
    before_update = events()
    update = worker("metadata-update", "metadata-update")
    updated_labels = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/labels")
    updated_label = next(value for value in updated_labels if value["name"] == "portable-priority")
    updated_release = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases/tags/portable-v1")
    updated_issue = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1")
    assertions["metadata_update_same_ids_and_timestamps"] = (
        update["plan"]["status"] == "confirmed"
        and updated_label["id"] == target_label["id"]
        and updated_label["color"].lower() == "224466"
        and updated_label["description"] == "Portable label updated on target"
        and updated_release["id"] == release["id"]
        and updated_release["name"] == "Portable release updated"
        and updated_release["body"] == "Updated release notes"
        and updated_release["author"] == release["author"]
        and updated_release["created_at"] == release["created_at"]
        and updated_issue["labels"][0]["id"] == target_label["id"]
        and len(events()) == len(before_update) + 2
    )
    after_update = events()
    update_replay = worker("resume", "metadata-update")
    assertions["metadata_update_replay_no_writes"] = (
        update_replay["plan"]["status"] == "confirmed" and events() == after_update
    )
    before_collisions = events()
    label_collision = worker("label-collision", "label-collision")
    release_collision = worker("release-collision", "release-collision")
    assertions["label_and_release_collisions_blocked"] = (
        label_collision["plan"]["status"] == "blocked"
        and release_collision["plan"]["status"] == "blocked"
        and events() == before_collisions
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/labels")) == len(labels)
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases")) == 1
    )
    omission = worker("metadata-omission", "metadata-omission")
    assertions["metadata_omission_no_deletion"] = (
        omission["plan"]["status"] == "confirmed"
        and events() == before_collisions
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/labels")) == len(labels)
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/releases")) == 1
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/assets")) == 1
        and len(
            api(
                TARGET_URL,
                "GET",
                f"/repos/{REPOSITORY}/releases/{release['id']}/assets",
            )
        )
        == 1
    )
    before_drift = events()
    drift = worker("metadata-drift", "metadata-drift")
    drift_events = events()[len(before_drift) :]
    drifted_label = next(
        value
        for value in api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/labels")
        if value["id"] == target_label["id"]
    )
    assertions["metadata_drift_blocks_overwrite"] = (
        drift["plan"]["status"] == "blocked"
        and not drift_events
        and drifted_label["description"] == "Foreign drift"
        and drifted_label["color"].lower() == "778899"
    )
    if not assertions["metadata_drift_blocks_overwrite"]:
        raise ProbeFailure(
            "Metadata drift evidence differed: "
            + json.dumps(
                {
                    "plan_status": drift["plan"]["status"],
                    "plan_reason": drift["plan"].get("reason"),
                    "writes": drift_events,
                    "label": {
                        "id": drifted_label.get("id"),
                        "color": drifted_label.get("color"),
                        "description": drifted_label.get("description"),
                    },
                },
                sort_keys=True,
            )
        )
    details: dict[str, Any] = {}
    for case, expected in (("crash", 91), ("lost", 0)):
        before = events()
        count = len(comments())
        interrupted = worker(case, case, expected)
        accepted = events()[len(before) :]
        assertions[case + "_remote_post_accepted"] = (
            len(accepted) == 1
            and accepted[0]["method"] == "POST"
            and accepted[0]["status"] == 201
            and len(comments()) == count + 1
        )
        after = events()
        resumed = worker("resume", case)
        resumed_operations = resumed["plan"]["operations"]
        states = [step["status"] for step in resumed_operations]
        unknown_steps = [step for step in resumed_operations if step["status"] == "outcome_unknown"]
        assertions[case + "_restart_outcome_unknown"] = (
            resumed["plan"]["status"] == "blocked"
            and len(unknown_steps) == 1
            and unknown_steps[0]["operation_type"] == "create_comment"
            and all(status in {"confirmed", "outcome_unknown"} for status in states)
        )
        assertions[case + "_no_second_post"] = events() == after and len(comments()) == count + 1
        repackaged = worker("repackage", case)
        assertions["new_packaging_cannot_bypass_unknown"] = (
            assertions.get("new_packaging_cannot_bypass_unknown", True)
            and repackaged["blocked"]
            and events() == after
        )
        details[case] = {
            "remote": accepted,
            "worker_exit": expected,
            "restart_plan_status": resumed["plan"]["status"],
            "operation_statuses": states,
            "interrupted_status": interrupted.get("plan", {}).get(
                "status", "process exited before confirmation"
            ),
        }
    before = events()
    issue_asset_count = len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/assets"))
    interrupted_asset = worker("asset-lost", "asset-lost")
    accepted_asset = events()[len(before) :]
    live_assets = api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/assets")
    assertions["asset_lost_remote_post_accepted"] = (
        interrupted_asset["plan"]["status"] == "blocked"
        and len(accepted_asset) == 1
        and accepted_asset[0]["method"] == "POST"
        and accepted_asset[0]["path"].endswith("/issues/1/assets")
        and accepted_asset[0]["status"] == 201
        and len(live_assets) == issue_asset_count + 1
        and any(asset["name"] == "lost-response-proof.txt" for asset in live_assets)
    )
    after_asset = events()
    resumed_asset = worker("resume", "asset-lost")
    unknown_assets = [
        step for step in resumed_asset["plan"]["operations"] if step["status"] == "outcome_unknown"
    ]
    assertions["asset_lost_restart_outcome_unknown"] = (
        resumed_asset["plan"]["status"] == "blocked"
        and len(unknown_assets) == 1
        and unknown_assets[0]["operation_type"] == "upload_attachment"
        and unknown_assets[0]["target_global_id"] is None
    )
    assertions["asset_lost_no_second_post"] = (
        events() == after_asset
        and len(api(TARGET_URL, "GET", f"/repos/{REPOSITORY}/issues/1/assets"))
        == issue_asset_count + 1
    )
    repackaged_asset = worker("repackage", "asset-lost")
    assertions["asset_lost_repackage_still_blocked"] = (
        repackaged_asset["blocked"] and events() == after_asset
    )
    details["asset_lost"] = {
        "remote": accepted_asset,
        "restart_plan_status": resumed_asset["plan"]["status"],
        "operation_statuses": [step["status"] for step in resumed_asset["plan"]["operations"]],
        "target_id_known_locally": unknown_assets[0]["target_global_id"]
        if unknown_assets
        else None,
    }
    # Only after restart/repackaging has proved the unresolved halt, simulate an
    # explicit operator selection. IDs are never fed into automatic recovery.
    other_issue = api(
        TARGET_URL,
        "POST",
        f"/repos/{REPOSITORY}/issues",
        json_body={"title": "Resolution decoy", "body": "synthetic"},
    )
    rejected = []
    for parent, username, body in (
        (other_issue["number"], "comment-author", "crash comment"),
        (1, "issue-author", "crash comment"),
        (1, "comment-author", "different content"),
    ):
        decoy = api(
            TARGET_URL,
            "POST",
            f"/repos/{REPOSITORY}/issues/{parent}/comments",
            auth=(username, USER_PASSWORD),
            json_body={"body": body},
        )
        result = worker("resolve", "crash", target_comment_id=str(decoy["id"]))
        rejected.append(
            result.get("rejected") is True
            and sum(step["status"] == "outcome_unknown" for step in result["plan"]["operations"])
            == 1
        )
    assertions["explicit_resolution_rejects_wrong_parent_author_and_content"] = all(rejected)
    accepted_comment = next(
        value
        for value in comments()
        if value["body"] == "crash comment" and value["user"]["login"] == "comment-author"
    )
    before_resolution = events()
    comment_count = len(comments())
    resolved = worker("resolve", "crash", target_comment_id=str(accepted_comment["id"]))
    restarted = worker("resume", "crash")
    resolved_comment = next(
        step
        for step in restarted["plan"]["operations"]
        if step["operation_type"] == "create_comment"
    )
    assertions["explicit_resolution_then_restart_confirms_without_post"] = (
        resolved["plan"]["status"] == restarted["plan"]["status"] == "confirmed"
        and resolved_comment["target_global_id"] == str(accepted_comment["id"])
        and events() == before_resolution
        and len(comments()) == comment_count
    )
    details["explicit_resolution"] = {
        "selection": "operator-selected target comment ID, supplied only after blocked recovery",
        "rejected_decoys": rejected,
        "target_comment_id": str(accepted_comment["id"]),
        "final_plan_status": restarted["plan"]["status"],
    }
    assertions["control_repository_unchanged"] = (
        api(TARGET_URL, "GET", "/repos/access-lab/control-private") == control
    )
    scan = """from pathlib import Path
root=Path('/state/application-plans')
secrets=[p.read_bytes().strip() for p in (root/'secrets').iterdir()]
files=list(root.glob('*.sqlite*'))+[root/'remote-events.jsonl']
clean = all(secret not in path.read_bytes() for path in files for secret in secrets)
raise SystemExit(0 if clean else 1)
"""
    assertions["credentials_absent_from_state_and_report"] = (
        docker_service_exec("gitferry-b", ["python", "-c", scan], check=False).returncode == 0
    )
    if set(assertions) != ASSERTIONS or not all(assertions.values()):
        raise ProbeFailure(f"Application plan assertions failed: {assertions}")
    return {
        "status": "passed",
        "assertions": assertions,
        "interruptions": details,
        "environment": (
            "target-only Linux application container with local HTTPS proxy to pinned Gitea"
        ),
        "limits": [
            "Bounded initial provisioning and metadata plan, not the complete import wizard",
            "Existing independent manifest roundtrip probes remain separate evidence",
            "No general exactly-once guarantee; interrupted creates remain blocked",
        ],
    }
