"""Runtime OpenAPI and bounded Gitea repository-migration probe."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

import httpx
from permissions_probe import ORG, AccessContext
from support import (
    ADMIN,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    SOURCE_URL,
    TARGET_URL,
    USER_PASSWORD,
    ProbeFailure,
    api,
    request,
)

SOURCE_REPO = f"{ORG}/migration-source"
TARGET_REPO = f"{ORG}/migration-imported"


def swagger(base_url: str) -> dict[str, Any]:
    response = httpx.get(f"{base_url}/swagger.v1.json", timeout=15)
    if response.status_code != 200:
        raise ProbeFailure(f"runtime Swagger returned {response.status_code}")
    value = response.json()
    if not isinstance(value, dict):
        raise ProbeFailure("runtime Swagger is not an object")
    return value


def definition(spec: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    definitions = spec.get("definitions")
    if not isinstance(definitions, Mapping):
        raise ProbeFailure("runtime Swagger has no definitions object")
    candidates = [
        value for key, value in definitions.items() if key == name or str(key).endswith(f".{name}")
    ]
    if len(candidates) != 1 or not isinstance(candidates[0], Mapping):
        raise ProbeFailure(f"runtime Swagger definition {name!r} is missing or ambiguous")
    return candidates[0]


def property_names(spec: Mapping[str, Any], name: str) -> tuple[str, ...]:
    properties = definition(spec, name).get("properties")
    if not isinstance(properties, Mapping):
        raise ProbeFailure(f"runtime Swagger definition {name!r} has no properties")
    return tuple(sorted(str(key) for key in properties))


def path_methods(spec: Mapping[str, Any], path: str) -> tuple[str, ...]:
    paths = spec.get("paths")
    if not isinstance(paths, Mapping) or not isinstance(paths.get(path), Mapping):
        raise ProbeFailure(f"runtime Swagger path {path!r} is missing")
    return tuple(
        sorted(
            str(method).upper()
            for method in paths[path]
            if str(method).lower() in {"get", "post", "patch", "put", "delete"}
        )
    )


def runtime_openapi_probe() -> dict[str, Any]:
    source = swagger(SOURCE_URL)
    target = swagger(TARGET_URL)
    source_digest = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target_digest = hashlib.sha256(
        json.dumps(target, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    create_issue = property_names(source, "CreateIssueOption")
    create_comment = property_names(source, "CreateIssueCommentOption")
    issue_response = property_names(source, "Issue")
    comment_response = property_names(source, "Comment")
    migrate = property_names(source, "MigrateRepoOptions")
    migrate_service = definition(source, "MigrateRepoOptions")["properties"]["service"]
    migrate_service_enum = tuple(migrate_service.get("enum", ()))
    advanced_requests = {
        name: property_names(source, name)
        for name in (
            "CreatePullRequestOption",
            "EditPullRequestOption",
            "CreatePullReviewOptions",
            "CreatePullReviewComment",
            "SubmitPullReviewOptions",
            "DismissPullReviewOptions",
            "EditReactionOption",
        )
    }
    advanced_responses = {
        name: property_names(source, name)
        for name in (
            "PullRequest",
            "PullReview",
            "PullReviewComment",
            "TimelineComment",
            "Reaction",
        )
    }
    advanced_paths = {
        path: path_methods(source, path)
        for path in (
            "/repos/{owner}/{repo}/pulls",
            "/repos/{owner}/{repo}/pulls/{index}",
            "/repos/{owner}/{repo}/pulls/{index}/reviews",
            "/repos/{owner}/{repo}/pulls/{index}/reviews/{id}",
            "/repos/{owner}/{repo}/pulls/{index}/reviews/{id}/comments",
            "/repos/{owner}/{repo}/issues/{index}/timeline",
            "/repos/{owner}/{repo}/issues/{index}/reactions",
            "/repos/{owner}/{repo}/issues/comments/{id}/reactions",
        )
    }

    assertions = {
        "source_target_schema_digest_equal": source_digest == target_digest,
        "issue_create_has_no_created_at": "created_at" not in create_issue,
        "issue_create_has_no_native_author_field": "user" not in create_issue,
        "comment_create_has_no_created_at": "created_at" not in create_comment,
        "comment_create_has_no_native_author_field": "user" not in create_comment,
        "issue_response_exposes_created_at": "created_at" in issue_response,
        "issue_response_exposes_updated_at": "updated_at" in issue_response,
        "issue_response_exposes_native_author": "user" in issue_response,
        "comment_response_exposes_created_at": "created_at" in comment_response,
        "comment_response_exposes_updated_at": "updated_at" in comment_response,
        "comment_response_exposes_native_author": "user" in comment_response,
        "repository_migration_accepts_issues": "issues" in migrate,
        "repository_migration_accepts_gitea_service": "gitea" in migrate_service_enum,
        "pull_create_requires_refs_without_author_or_time": (
            {"base", "head", "title"} <= set(advanced_requests["CreatePullRequestOption"])
            and {"id", "number", "user", "created_at", "updated_at"}.isdisjoint(
                advanced_requests["CreatePullRequestOption"]
            )
        ),
        "pull_response_exposes_refs_identity_author_and_times": (
            {"id", "number", "user", "created_at", "updated_at", "head", "base"}
            <= set(advanced_responses["PullRequest"])
        ),
        "pull_patch_has_no_atomic_version_precondition": (
            {"title", "body", "state", "base"} <= set(advanced_requests["EditPullRequestOption"])
            and {"etag", "if_match", "updated_at"}.isdisjoint(
                advanced_requests["EditPullRequestOption"]
            )
        ),
        "review_create_accepts_nested_positions_without_author_or_time": (
            {"body", "comments", "commit_id", "event"}
            <= set(advanced_requests["CreatePullReviewOptions"])
            and {"body", "path", "new_position", "old_position"}
            <= set(advanced_requests["CreatePullReviewComment"])
            and {"id", "user", "submitted_at", "created_at"}.isdisjoint(
                advanced_requests["CreatePullReviewOptions"]
            )
        ),
        "review_response_exposes_identity_author_state_and_times": (
            {"id", "user", "state", "commit_id", "submitted_at", "updated_at"}
            <= set(advanced_responses["PullReview"])
        ),
        "inline_response_exposes_review_parent_positions_author_and_times": (
            {
                "id",
                "user",
                "pull_request_review_id",
                "path",
                "position",
                "original_position",
                "created_at",
                "updated_at",
            }
            <= set(advanced_responses["PullReviewComment"])
        ),
        "review_comments_have_no_update_or_delete_route": advanced_paths[
            "/repos/{owner}/{repo}/pulls/{index}/reviews/{id}/comments"
        ]
        == ("GET",),
        "timeline_is_read_only": advanced_paths["/repos/{owner}/{repo}/issues/{index}/timeline"]
        == ("GET",),
        "reaction_contract_is_natural_key_without_native_id": (
            advanced_paths["/repos/{owner}/{repo}/issues/{index}/reactions"]
            == ("DELETE", "GET", "POST")
            and advanced_paths["/repos/{owner}/{repo}/issues/comments/{id}/reactions"]
            == ("DELETE", "GET", "POST")
            and set(advanced_requests["EditReactionOption"]) == {"content"}
            and {"content", "created_at", "user"} <= set(advanced_responses["Reaction"])
            and {"id", "updated_at"}.isdisjoint(advanced_responses["Reaction"])
        ),
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"runtime OpenAPI assertion failed: {assertions}")
    return {
        "status": "passed",
        "endpoint": "/swagger.v1.json",
        "source_sha256": source_digest,
        "target_sha256": target_digest,
        "request_fields": {
            "CreateIssueOption": create_issue,
            "CreateIssueCommentOption": create_comment,
        },
        "response_fields_relevant_to_probe": {
            "Issue": tuple(
                field
                for field in ("id", "number", "user", "original_author", "created_at", "updated_at")
                if field in issue_response
            ),
            "Comment": tuple(
                field
                for field in ("id", "user", "original_author", "created_at", "updated_at")
                if field in comment_response
            ),
        },
        "advanced_metadata": {
            "request_fields": advanced_requests,
            "response_fields": advanced_responses,
            "path_methods": advanced_paths,
            "classification": {
                "pull_requests": {
                    "inventory": "supported",
                    "first_import": "blocked-required-refs-and-author-fidelity",
                    "return_update": "blocked-no-atomic-version-precondition",
                    "deletion": "not-supported",
                },
                "reviews": {
                    "inventory": "supported",
                    "first_import": "blocked-commit-and-author-fidelity",
                    "return_update": "not-supported",
                    "deletion": "draft-review-only-not-portable",
                },
                "inline_review_comments": {
                    "inventory": "supported",
                    "first_import": "blocked-review-parent-and-diff-position",
                    "return_update": "not-supported",
                    "deletion": "not-supported",
                },
                "timelines": {
                    "inventory": "supported",
                    "first_import": "not-supported-read-only-api",
                    "return_update": "not-supported-read-only-api",
                    "deletion": "not-supported-read-only-api",
                },
                "reactions": {
                    "inventory": "supported-with-natural-key",
                    "first_import": "blocked-no-native-id-or-historical-time",
                    "return_update": "not-applicable-create-delete-set",
                    "deletion": "available-but-not-enabled",
                },
            },
        },
        "assertions": assertions,
    }


def wait_for_migrated_issues() -> list[dict[str, Any]]:
    path = f"/repos/{TARGET_REPO}/issues?state=all&limit=50"
    for _ in range(30):
        response = request(TARGET_URL, "GET", path)
        if response.status_code == 200:
            issues = response.json()
            if issues:
                return issues
        time.sleep(1)
    raise ProbeFailure("repository migration did not expose imported issues within 30 seconds")


def repository_migration_probe(context: AccessContext) -> dict[str, Any]:
    api(
        SOURCE_URL,
        "POST",
        f"/orgs/{ORG}/repos",
        json_body={"name": "migration-source", "private": True, "auto_init": True},
    )
    for username in ("issue-author", "comment-author"):
        api(
            SOURCE_URL,
            "PUT",
            f"/repos/{SOURCE_REPO}/collaborators/{username}",
            json_body={"permission": "write"},
        )
    source_issue = api(
        SOURCE_URL,
        "POST",
        f"/repos/{SOURCE_REPO}/issues",
        auth=("issue-author", USER_PASSWORD),
        json_body={"title": "Server migration issue", "body": "Migration route body"},
    )
    source_comment = api(
        SOURCE_URL,
        "POST",
        f"/repos/{SOURCE_REPO}/issues/{source_issue['number']}/comments",
        auth=("comment-author", USER_PASSWORD),
        json_body={"body": "Server migration comment"},
    )

    migrated_repo = api(
        TARGET_URL,
        "POST",
        "/repos/migrate",
        auth=ADMIN,
        json_body={
            "clone_addr": f"http://source:3000/{SOURCE_REPO}.git",
            "auth_username": ADMIN_USERNAME,
            "auth_password": ADMIN_PASSWORD,
            "service": "gitea",
            "issues": True,
            "labels": True,
            "private": True,
            "repo_owner": ORG,
            "repo_name": "migration-imported",
        },
    )
    issues = wait_for_migrated_issues()
    migrated_issue = next(
        (issue for issue in issues if issue["title"] == source_issue["title"]), None
    )
    if migrated_issue is None:
        raise ProbeFailure(f"migrated issue was not found: {issues}")
    comments = api(
        TARGET_URL,
        "GET",
        f"/repos/{TARGET_REPO}/issues/{migrated_issue['number']}/comments",
    )
    migrated_comment = next(
        (comment for comment in comments if comment["body"] == source_comment["body"]), None
    )
    if migrated_comment is None:
        raise ProbeFailure(f"migrated comment was not found: {comments}")

    duplicate = request(
        TARGET_URL,
        "POST",
        "/repos/migrate",
        auth=ADMIN,
        json_body={
            "clone_addr": f"http://source:3000/{SOURCE_REPO}.git",
            "auth_username": ADMIN_USERNAME,
            "auth_password": ADMIN_PASSWORD,
            "service": "gitea",
            "issues": True,
            "private": True,
            "repo_owner": ORG,
            "repo_name": "migration-imported",
        },
    )
    issue_native_author = {
        "id": migrated_issue["user"]["id"],
        "login": migrated_issue["user"]["login"],
        "mapped_target_id": context.target_users["issue-author"]["id"],
    }
    comment_native_author = {
        "id": migrated_comment["user"]["id"],
        "login": migrated_comment["user"]["login"],
        "mapped_target_id": context.target_users["comment-author"]["id"],
    }
    assertions = {
        "repository_created": migrated_repo["full_name"] == TARGET_REPO,
        "issue_content_preserved": migrated_issue["body"] == source_issue["body"],
        "comment_content_preserved": migrated_comment["body"] == source_comment["body"],
        "issue_created_at_preserved": migrated_issue["created_at"] == source_issue["created_at"],
        "comment_created_at_preserved": migrated_comment["created_at"]
        == source_comment["created_at"],
        "second_same_name_import_rejected": duplicate.status_code == 409,
    }
    if not all(assertions.values()):
        raise ProbeFailure(f"repository migration assertion failed: {assertions}")
    return {
        "status": "passed",
        "interface": "POST /api/v1/repos/migrate with service=gitea",
        "assertions": assertions,
        "native_authors": {
            "issue": issue_native_author,
            "comment": comment_native_author,
            "issue_matches_explicit_target_account": issue_native_author["id"]
            == issue_native_author["mapped_target_id"],
            "comment_matches_explicit_target_account": comment_native_author["id"]
            == comment_native_author["mapped_target_id"],
        },
        "origin_fields": {
            "issue_original_author": migrated_issue.get("original_author"),
            "issue_original_author_id": migrated_issue.get("original_author_id"),
            "comment_original_author": migrated_comment.get("original_author"),
            "comment_original_author_id": migrated_comment.get("original_author_id"),
        },
        "assessment": {
            "historical_timestamps": assertions["issue_created_at_preserved"]
            and assertions["comment_created_at_preserved"],
            "historical_native_authors": issue_native_author["id"]
            == issue_native_author["mapped_target_id"]
            and comment_native_author["id"] == comment_native_author["mapped_target_id"],
            "suitable_for_initial_repository_import": True,
            "suitable_for_existing_object_updates": False,
            "offline_package_usable": False,
            "required_server_access": (
                "target Gitea server required live HTTP/Git reachability and credentials "
                "to source Gitea"
            ),
            "architecture_impact": (
                "candidate initial-import adapter only; it cannot replace object mappings, "
                "selected-field baselines, or roundtrip conflict handling"
            ),
        },
    }


def run_openapi_migration_probe(context: AccessContext) -> dict[str, Any]:
    return {
        "status": "passed",
        "runtime_openapi": runtime_openapi_probe(),
        "repository_migration": repository_migration_probe(context),
    }
