"""Real primary-Gitea qualification for the selected-content inventory collector."""

from __future__ import annotations

import base64
import json
import ssl
import time
from typing import Any

import httpx
from gitferry.content_inventory import CONTENT_CLASSES, inventory_summary
from gitferry.source_inventory import collect_inventory
from support import (
    ADMIN,
    SOURCE_URL,
    USER_PASSWORD,
    WORK,
    ProbeFailure,
    api,
    flush_fixture_queues,
    request,
)

ORG = "inventory-qualification"
SELECTED = f"{ORG}/selected"
CONTROL = f"{ORG}/control"


def _get(
    base: str, path: str, *, auth: tuple[str, str] = ADMIN, params: dict[str, Any] | None = None
) -> Any | None:
    if params:
        response = httpx.get(f"{base}/api/v1{path}", params=params, auth=auth, timeout=15)
    else:
        response = request(base, "GET", path, auth=auth)
    return response.json() if response.status_code == 200 and response.content else None


def _ensure_fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if _get(SOURCE_URL, f"/orgs/{ORG}") is None:
        api(SOURCE_URL, "POST", "/orgs", json_body={"username": ORG, "visibility": "private"})
    selected = _get(SOURCE_URL, f"/repos/{SELECTED}")
    if selected is None:
        selected = api(
            SOURCE_URL,
            "POST",
            f"/orgs/{ORG}/repos",
            json_body={"name": "selected", "private": True, "auto_init": True},
        )
    control = _get(SOURCE_URL, f"/repos/{CONTROL}")
    if control is None:
        control = api(
            SOURCE_URL,
            "POST",
            f"/orgs/{ORG}/repos",
            json_body={"name": "control", "private": True, "auto_init": True},
        )
    teams = _get(SOURCE_URL, f"/orgs/{ORG}/teams") or []
    team = next((item for item in teams if item.get("name") == "inventory-readers"), None)
    if team is None:
        team = api(
            SOURCE_URL,
            "POST",
            f"/orgs/{ORG}/teams",
            json_body={
                "name": "inventory-readers",
                "permission": "read",
                "units_map": {"repo.code": "read", "repo.issues": "read"},
            },
        )
    api(SOURCE_URL, "PUT", f"/teams/{team['id']}/repos/{ORG}/selected")
    fixture = _seed_metadata(selected)
    return selected, control, fixture


def _seed_metadata(repo: dict[str, Any]) -> dict[str, Any]:
    root = f"/repos/{SELECTED}"
    labels = _get(SOURCE_URL, root + "/labels") or []
    label = (
        labels[0]
        if labels
        else api(
            SOURCE_URL,
            "POST",
            root + "/labels",
            json_body={
                "name": "inventory",
                "color": "336699",
                "description": "Synthetic inventory label",
            },
        )
    )
    issues = _get(SOURCE_URL, root + "/issues", params={"state": "all", "type": "issues"}) or []
    issue = (
        issues[0]
        if issues
        else api(
            SOURCE_URL,
            "POST",
            root + "/issues",
            json_body={
                "title": "Inventory issue",
                "body": "Synthetic Unicode 🚢",
                "labels": [label["id"]],
            },
        )
    )
    idx = issue["number"]
    comments = _get(SOURCE_URL, root + f"/issues/{idx}/comments") or []
    comment = (
        comments[0]
        if comments
        else api(
            SOURCE_URL,
            "POST",
            root + f"/issues/{idx}/comments",
            json_body={"body": "Synthetic issue comment"},
        )
    )
    _upload_if_empty(root + f"/issues/{idx}/assets", "issue.txt", b"synthetic issue asset\n")
    _upload_if_empty(
        root + f"/issues/comments/{comment['id']}/assets",
        "comment.txt",
        b"synthetic comment asset\n",
    )
    if not (_get(SOURCE_URL, root + f"/issues/{idx}/reactions") or []):
        api(SOURCE_URL, "POST", root + f"/issues/{idx}/reactions", json_body={"content": "+1"})
    if request(SOURCE_URL, "GET", root + "/wiki/pages").status_code == 404:
        api(
            SOURCE_URL,
            "POST",
            root + "/wiki/new",
            json_body={
                "title": "Home",
                "content_base64": base64.b64encode(b"Synthetic wiki").decode(),
                "message": "Inventory fixture",
            },
        )
    releases = _get(SOURCE_URL, root + "/releases") or []
    release = (
        releases[0]
        if releases
        else api(
            SOURCE_URL,
            "POST",
            root + "/releases",
            json_body={
                "tag_name": "inventory-v1",
                "target_commitish": "main",
                "name": "Synthetic release",
            },
        )
    )
    _upload_if_empty(
        root + f"/releases/{release['id']}/assets", "release.txt", b"synthetic release asset\n"
    )
    if not (_get(SOURCE_URL, root + "/milestones") or []):
        api(
            SOURCE_URL,
            "POST",
            root + "/milestones",
            json_body={"title": "Synthetic milestone", "description": "Inventory fixture"},
        )
    pulls = _get(SOURCE_URL, root + "/pulls", params={"state": "all"}) or []
    if not pulls:
        if _get(SOURCE_URL, root + "/branches/inventory-work") is None:
            api(
                SOURCE_URL,
                "POST",
                root + "/branches",
                json_body={"new_branch_name": "inventory-work", "old_ref_name": "main"},
            )
        file = _get(SOURCE_URL, root + "/contents/README.md", params={"ref": "inventory-work"})
        if file:
            api(
                SOURCE_URL,
                "PUT",
                root + "/contents/README.md",
                json_body={
                    "branch": "inventory-work",
                    "sha": file["sha"],
                    "message": "Synthetic inventory change",
                    "content": base64.b64encode(b"# Inventory\n").decode(),
                },
            )
        pull = api(
            SOURCE_URL,
            "POST",
            root + "/pulls",
            json_body={
                "title": "Inventory pull",
                "head": "inventory-work",
                "base": "main",
                "body": "Synthetic PR",
            },
        )
    else:
        pull = pulls[0]
    pidx = pull["number"]
    for username, permission in (("developer", "write"), ("viewer", "read")):
        api(
            SOURCE_URL,
            "PUT",
            root + f"/collaborators/{username}",
            json_body={"permission": permission},
        )
    if not (_get(SOURCE_URL, root + f"/issues/{pidx}/comments") or []):
        api(
            SOURCE_URL,
            "POST",
            root + f"/issues/{pidx}/comments",
            json_body={"body": "Synthetic PR conversation"},
        )
    reviews = _get(SOURCE_URL, root + f"/pulls/{pidx}/reviews") or []
    if not reviews:
        for username in ("developer", "viewer"):
            api(
                SOURCE_URL,
                "POST",
                root + f"/pulls/{pidx}/reviews",
                auth=(username, USER_PASSWORD),
                json_body={
                    "event": "COMMENT",
                    "body": "Synthetic equal-body review",
                    "comments": [
                        {
                            "path": "README.md",
                            "body": "Synthetic equal-body inline comment",
                            "new_position": 1,
                        }
                    ],
                },
            )
    reviews = _get(SOURCE_URL, root + f"/pulls/{pidx}/reviews") or []
    review_comments = [
        comment
        for review in reviews
        for comment in (
            _get(SOURCE_URL, root + f"/pulls/{pidx}/reviews/{review['id']}/comments") or []
        )
    ]
    first_inline = next(
        comment
        for comment in review_comments
        if comment.get("body") == "Synthetic equal-body inline comment"
    )
    if not any(comment.get("body") == "Synthetic review reply" for comment in review_comments):
        api(
            SOURCE_URL,
            "POST",
            root + f"/pulls/{pidx}/comments/{first_inline['id']}/replies",
            auth=("developer", USER_PASSWORD),
            json_body={"body": "Synthetic review reply"},
        )
    review_id = first_inline["pull_request_review_id"]

    def current_inline() -> dict[str, Any]:
        comments = (
            _get(
                SOURCE_URL,
                root + f"/pulls/{pidx}/reviews/{review_id}/comments",
            )
            or []
        )
        return next(item for item in comments if item.get("id") == first_inline["id"])

    api(SOURCE_URL, "POST", root + f"/pulls/comments/{first_inline['id']}/resolve")
    resolved_once = (current_inline().get("resolver") or {}).get("login") == ADMIN[0]
    api(SOURCE_URL, "POST", root + f"/pulls/comments/{first_inline['id']}/unresolve")
    unresolved = current_inline().get("resolver") is None
    api(SOURCE_URL, "POST", root + f"/pulls/comments/{first_inline['id']}/resolve")
    resolved_again = (current_inline().get("resolver") or {}).get("login") == ADMIN[0]

    merged_pulls = _get(SOURCE_URL, root + "/pulls", params={"state": "all"}) or []
    merged_pull = next(
        (item for item in merged_pulls if item.get("title") == "Inventory merged pull"),
        None,
    )
    if merged_pull is None:
        if _get(SOURCE_URL, root + "/branches/inventory-merged") is None:
            api(
                SOURCE_URL,
                "POST",
                root + "/branches",
                json_body={"new_branch_name": "inventory-merged", "old_ref_name": "main"},
            )
        api(
            SOURCE_URL,
            "POST",
            root + "/contents/merged-review.txt",
            json_body={
                "branch": "inventory-merged",
                "message": "Synthetic merged review fixture",
                "content": base64.b64encode(b"merged review fixture\n").decode(),
            },
        )
        merged_pull = api(
            SOURCE_URL,
            "POST",
            root + "/pulls",
            json_body={
                "title": "Inventory merged pull",
                "head": "inventory-merged",
                "base": "main",
                "body": "Synthetic merged PR",
            },
        )
    merged_reviews = (
        _get(
            SOURCE_URL,
            root + f"/pulls/{merged_pull['number']}/reviews",
        )
        or []
    )
    if not any(item.get("state") == "APPROVED" for item in merged_reviews):
        approval_review = api(
            SOURCE_URL,
            "POST",
            root + f"/pulls/{merged_pull['number']}/reviews",
            auth=("developer", USER_PASSWORD),
            json_body={
                "event": "PENDING",
                "body": "Synthetic approval decision",
                "commit_id": merged_pull["head"]["sha"],
            },
        )
        if approval_review.get("state") != "APPROVED":
            api(
                SOURCE_URL,
                "POST",
                root + f"/pulls/{merged_pull['number']}/reviews/{approval_review['id']}",
                auth=("developer", USER_PASSWORD),
                json_body={"event": "APPROVED", "body": "Synthetic approval decision"},
            )
    if not merged_pull.get("merged"):
        merge_style = next(
            style
            for flag, style in (
                (repo.get("allow_merge_commits"), "merge"),
                (repo.get("allow_rebase"), "rebase"),
                (repo.get("allow_rebase_explicit"), "rebase-merge"),
                (repo.get("allow_squash_merge"), "squash"),
                (repo.get("allow_fast_forward_only_merge"), "fast-forward-only"),
            )
            if flag
        )
        mergeability_deadline = time.monotonic() + 20
        while merged_pull.get("mergeable") is not True:
            if time.monotonic() >= mergeability_deadline:
                raise ProbeFailure(
                    "synthetic pull did not reach a confirmed mergeable state within 20 seconds"
                )
            time.sleep(0.25)
            current_pull = _get(SOURCE_URL, root + f"/pulls/{merged_pull['number']}")
            if not isinstance(current_pull, dict):
                raise ProbeFailure("synthetic pull disappeared during mergeability check")
            merged_pull = current_pull
        merge_deadline = time.monotonic() + 20
        while True:
            merge = request(
                SOURCE_URL,
                "POST",
                root + f"/pulls/{merged_pull['number']}/merge",
                auth=ADMIN,
                json_body={"do": merge_style, "force_merge": True},
            )
            if merge.status_code in (200, 201):
                break
            retryable_not_ready = (
                merge.status_code == 405 and '"message":"Please try again later"' in merge.text
            )
            if not retryable_not_ready or time.monotonic() >= merge_deadline:
                raise ProbeFailure(
                    f"synthetic pull merge failed: {merge.status_code} {merge.text[:300]}"
                )
            time.sleep(0.25)
    merged_pull = _get(SOURCE_URL, root + f"/pulls/{merged_pull['number']}")
    if not isinstance(merged_pull, dict):
        raise ProbeFailure("merged pull could not be read back")
    return {
        "label": label,
        "issue": issue,
        "comment": comment,
        "release": release,
        "pull": pull,
        "merged_pull": merged_pull,
        "resolution_transitions": {
            "resolved_once": resolved_once,
            "unresolved": unresolved,
            "resolved_again": resolved_again,
        },
    }


def _upload_if_empty(path: str, filename: str, content: bytes) -> None:
    if _get(SOURCE_URL, path):
        return
    response = httpx.post(
        f"{SOURCE_URL}/api/v1{path}",
        auth=ADMIN,
        files={"attachment": (filename, content, "text/plain")},
        timeout=15,
    )
    if response.status_code not in (201, 409):
        raise ProbeFailure(f"fixture asset upload failed: {response.status_code}")


def _qualify_pull_content_version() -> dict[str, Any]:
    root = f"/repos/{CONTROL}"
    if _get(SOURCE_URL, root + "/branches/content-version-work") is None:
        api(
            SOURCE_URL,
            "POST",
            root + "/branches",
            json_body={"new_branch_name": "content-version-work", "old_ref_name": "main"},
        )
    if (
        _get(
            SOURCE_URL,
            root + "/contents/content-version.txt",
            params={"ref": "content-version-work"},
        )
        is None
    ):
        api(
            SOURCE_URL,
            "POST",
            root + "/contents/content-version.txt",
            json_body={
                "branch": "content-version-work",
                "message": "Synthetic content-version fixture",
                "content": base64.b64encode(b"content version fixture\n").decode(),
            },
        )
    pulls = _get(SOURCE_URL, root + "/pulls", params={"state": "all"}) or []
    pull = next(
        (item for item in pulls if item.get("head", {}).get("ref") == "content-version-work"),
        None,
    )
    if pull is None:
        pull = api(
            SOURCE_URL,
            "POST",
            root + "/pulls",
            json_body={
                "title": "Content-version qualification",
                "head": "content-version-work",
                "base": "main",
                "body": "Initial guarded body",
            },
        )
    version = pull.get("content_version")
    if type(version) is not int or version < 0:
        raise ProbeFailure("pull response did not expose a non-negative content_version")
    guarded_title = "Content-version guarded update"
    guarded_body = f"Guarded body from version {version}"
    path = root + f"/pulls/{pull['number']}"
    accepted = request(
        SOURCE_URL,
        "PATCH",
        path,
        auth=ADMIN,
        json_body={
            "title": guarded_title,
            "body": guarded_body,
            "content_version": version,
        },
    )
    if accepted.status_code not in (200, 201):
        raise ProbeFailure(f"current content_version PATCH failed: {accepted.status_code}")
    stale = request(
        SOURCE_URL,
        "PATCH",
        path,
        auth=ADMIN,
        json_body={
            "title": "stale title must not apply",
            "body": "stale body must not apply",
            "content_version": version,
        },
    )
    current = _get(SOURCE_URL, path)
    if (
        stale.status_code != 409
        or not isinstance(current, dict)
        or current.get("title") != guarded_title
        or current.get("body") != guarded_body
        or type(current.get("content_version")) is not int
        or current["content_version"] <= version
    ):
        raise ProbeFailure("stale content_version did not fail closed before PR content mutation")
    return {
        "accepted_status": accepted.status_code,
        "stale_status": stale.status_code,
        "before": version,
        "after": current["content_version"],
        "stale_title_and_body_unchanged": True,
        "remaining_limit": "Gitea documents a transaction gap for concurrent multi-field writes",
    }


def _direct_counts() -> dict[str, int | None]:
    """Independent finite fixture enumeration; never uses collector result objects."""
    root = "/repos/" + SELECTED

    def values(path: str, **params: str) -> list[dict[str, Any]]:
        result = _get(SOURCE_URL, path, params=params or None)
        if result is None and path.endswith("/reactions"):
            return []  # The separate roundtrip below qualifies this specific API shape.
        if not isinstance(result, list):
            raise ProbeFailure("direct fixture enumeration failed")
        return result

    issues = values(root + "/issues", state="all", type="issues")
    pulls = values(root + "/pulls", state="all")
    comments = [
        (parent, c)
        for parent in issues
        for c in values(root + f"/issues/{parent['number']}/comments")
    ]
    pull_comments = [
        (parent, c)
        for parent in pulls
        for c in values(root + f"/issues/{parent['number']}/comments")
    ]
    reviews = [
        (parent, r) for parent in pulls for r in values(root + f"/pulls/{parent['number']}/reviews")
    ]
    review_comments = [
        (parent, review, comment)
        for parent, review in reviews
        for comment in values(root + f"/pulls/{parent['number']}/reviews/{review['id']}/comments")
    ]
    teams = values(root + "/teams")
    releases = values(root + "/releases")
    users = {str(item["user"]["id"]) for item in issues + pulls}
    users.update(str(c["user"]["id"]) for _, c in comments + pull_comments)
    users.update(str(review["user"]["id"]) for _, review in reviews)
    users.update(str(comment["user"]["id"]) for _, _, comment in review_comments)
    for team in teams:
        users.update(str(u["id"]) for u in values(f"/teams/{team['id']}/members"))
    counts: dict[str, int | None] = {
        "git_refs": len(values(root + "/git/refs")),
        "wiki": len(values(root + "/wiki/pages")),
        "organizations": 1,
        "teams": len(teams),
        "permissions": len(teams) + len(values(root + "/collaborators")),
        "identities": len(users),
        "issues": len(issues),
        "pull_requests": len(pulls),
        "comments": len(comments),
        "pull_comments": len(pull_comments),
        "labels": len(values(root + "/labels")),
        "releases": len(releases),
        "milestones": len(values(root + "/milestones", state="all")),
        "reviews": len(reviews),
        "review_comments": len(review_comments),
        "release_assets": sum(len(values(root + f"/releases/{r['id']}/assets")) for r in releases),
        "attachments": sum(
            len(values(root + f"/issues/{p['number']}/assets")) for p in issues + pulls
        )
        + sum(
            len(values(root + f"/issues/comments/{c['id']}/assets"))
            for _, c in comments + pull_comments
        ),
        "timelines": sum(
            len(values(root + f"/issues/{p['number']}/timeline")) for p in issues + pulls
        ),
        "reactions": sum(
            len(values(root + f"/issues/{p['number']}/reactions")) for p in issues + pulls
        )
        + sum(
            len(values(root + f"/issues/comments/{c['id']}/reactions"))
            for _, c in comments + pull_comments
        ),
        "lfs": None,
        "other_content": None,
    }
    return counts


def _assert_counts(
    direct_counts: dict[str, int | None],
    observed_counts: dict[str, int | None],
    after_counts: dict[str, int | None],
) -> None:
    count_evidence = {
        "expected": direct_counts,
        "observed": observed_counts,
        "independent_after": after_counts,
        "differences": {
            name: {
                "expected": direct_counts[name],
                "observed": observed_counts.get(name),
                "independent_after": after_counts.get(name),
            }
            for name in direct_counts
            if observed_counts.get(name) != direct_counts[name]
            or observed_counts.get(name) != after_counts.get(name)
        },
    }
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "inventory-counts.json").write_text(json.dumps(count_evidence, indent=2) + "\n")
    if observed_counts != direct_counts or observed_counts != after_counts:
        raise ProbeFailure(
            "inventory counts differ from independent fixture enumeration: "
            + json.dumps(count_evidence["differences"], sort_keys=True)
        )


def run_content_inventory_probe() -> dict[str, Any]:
    selected, control, fixture = _ensure_fixture()
    flush_fixture_queues("source", "inventory")
    control_issues_before = (
        _get(SOURCE_URL, f"/repos/{CONTROL}/issues", params={"state": "all", "type": "issues"})
        or []
    )
    calls: list[tuple[str, str]] = []
    upstream = httpx.Client(base_url=SOURCE_URL, auth=ADMIN, trust_env=False, timeout=20)

    def forward(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        response = upstream.request(request.method, request.url.path, params=request.url.params)
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )

    control_issue = (
        control_issues_before[0]
        if control_issues_before
        else api(
            SOURCE_URL,
            "POST",
            f"/repos/{CONTROL}/issues",
            json_body={"title": "Synthetic control issue", "body": "Control fixture"},
        )
    )
    control_index = control_issue["number"]
    control_comments = _get(SOURCE_URL, f"/repos/{CONTROL}/issues/{control_index}/comments") or []
    control_comment = (
        control_comments[0]
        if control_comments
        else api(
            SOURCE_URL,
            "POST",
            f"/repos/{CONTROL}/issues/{control_index}/comments",
            json_body={"body": "Control comment"},
        )
    )
    control_reaction_path = f"/repos/{CONTROL}/issues/{control_index}/reactions"
    control_comment_reaction_path = (
        f"/repos/{CONTROL}/issues/comments/{control_comment['id']}/reactions"
    )
    control_before = _get(SOURCE_URL, f"/repos/{CONTROL}")
    control_issues_before = (
        _get(SOURCE_URL, f"/repos/{CONTROL}/issues", params={"state": "all", "type": "issues"})
        or []
    )
    before_reaction = request(SOURCE_URL, "GET", control_reaction_path, auth=ADMIN)
    if (
        before_reaction.status_code != 200
        or before_reaction.json() is not None
        or before_reaction.headers.get("X-Total-Count") != "0"
    ):
        raise ProbeFailure("empty reaction endpoint did not return HTTP 200 JSON null")
    direct_counts = _direct_counts()
    requested = list(CONTENT_CLASSES)
    document = collect_inventory(
        "https://inventory.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        [{"repository": SELECTED, "global_id": str(selected["id"])}],
        requested,
        source_instance_key="synthetic-source",
        installation_marker="synthetic-installation",
        snapshot_id="live-content-inventory",
        created_at="2026-11-21T17:00:00Z",
        transport=httpx.MockTransport(forward),
    )
    empty_scope = ORG + "/empty"
    empty_repository = _get(SOURCE_URL, "/repos/" + empty_scope)
    if empty_repository is None:
        empty_repository = api(
            SOURCE_URL,
            "POST",
            f"/orgs/{ORG}/repos",
            json_body={"name": "empty", "private": True, "auto_init": False},
        )
    if empty_repository.get("empty") is not True:
        raise ProbeFailure("empty fixture did not report native empty=true")
    empty_document = collect_inventory(
        "https://inventory.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        [{"repository": empty_scope, "global_id": str(empty_repository["id"])}],
        ["git_refs"],
        source_instance_key="synthetic-source",
        installation_marker="synthetic-installation",
        snapshot_id="empty-inventory",
        created_at="2026-11-21T17:00:00Z",
        transport=httpx.MockTransport(forward),
    )
    empty_classes = {c.content_class: c for c in empty_document.repositories[0].classes}
    if any(
        empty_classes[name].status != "observed" or empty_classes[name].count != 0
        for name in ("git_refs", "issues", "comments", "labels")
    ):
        raise ProbeFailure("explicit empty fixture did not preserve observed zero counts")
    upstream.close()
    inventory = document.repositories[0]
    by_class = {item.content_class: item for item in inventory.classes}
    observed_counts = {name: item.count for name, item in by_class.items()}
    after_counts = _direct_counts()
    _assert_counts(direct_counts, observed_counts, after_counts)
    unsupported = {"lfs", "other_content"}
    observed = set(CONTENT_CLASSES) - unsupported
    if {name for name, item in by_class.items() if item.status == "observed"} != observed:
        raise ProbeFailure("live inventory did not observe exactly the qualified classes")
    if any(by_class[name].count == 0 for name in observed):
        raise ProbeFailure(
            "live fixture did not contain a non-empty instance of every available class"
        )
    if any(method != "GET" for method, _ in calls):
        raise ProbeFailure("collector performed a non-GET source request")
    if any(CONTROL in path for _, path in calls):
        raise ProbeFailure("collector traversed the unselected control repository")
    if inventory.repository != SELECTED or inventory.native_id != str(selected["id"]):
        raise ProbeFailure("selected repository identity was not preserved")
    expected_ids = {
        "labels": str(fixture["label"]["id"]),
        "issues": str(fixture["issue"]["id"]),
        "comments": str(fixture["comment"]["id"]),
        "releases": str(fixture["release"]["id"]),
        "pull_requests": str(fixture["pull"]["id"]),
    }
    for class_name, native_id in expected_ids.items():
        if not any(item.native_id == native_id for item in by_class[class_name].objects):
            raise ProbeFailure(f"fixture identity missing from {class_name}")
    pull_object = next(
        item
        for item in by_class["pull_requests"].objects
        if item.native_id == str(fixture["pull"]["id"])
    )
    if (
        pull_object.facts.get("state") != "open"
        or pull_object.facts.get("merged") is not False
        or pull_object.facts.get("head", {}).get("ref") != "inventory-work"
        or pull_object.facts.get("base", {}).get("ref") != "main"
        or not pull_object.facts.get("head", {}).get("sha")
        or not pull_object.facts.get("base", {}).get("sha")
        or pull_object.facts.get("head", {}).get("repository", {}).get("global_id")
        != str(selected["id"])
        or pull_object.facts.get("base", {}).get("repository", {}).get("global_id")
        != str(selected["id"])
        or type(pull_object.facts.get("content_version")) is not int
        or not isinstance(pull_object.facts.get("created_at"), str)
        or not isinstance(pull_object.facts.get("updated_at"), str)
    ):
        raise ProbeFailure("pull fixture refs, state or merge facts were not preserved")
    merged_pull_object = next(
        item
        for item in by_class["pull_requests"].objects
        if item.native_id == str(fixture["merged_pull"]["id"])
    )
    if (
        merged_pull_object.facts.get("state") != "closed"
        or merged_pull_object.facts.get("merged") is not True
        or not isinstance(merged_pull_object.facts.get("closed_at"), str)
        or not isinstance(merged_pull_object.facts.get("merged_at"), str)
        or "merged_by" not in merged_pull_object.facts
        or merged_pull_object.facts["merged_by"] is not None
        or not isinstance(merged_pull_object.facts.get("merge_commit_sha"), str)
        or not merged_pull_object.facts.get("merge_commit_sha")
    ):
        merge_facts = {
            key: merged_pull_object.facts.get(key)
            for key in (
                "state",
                "merged",
                "closed_at",
                "merged_at",
                "merged_by",
                "merge_commit_sha",
            )
        }
        raise ProbeFailure(
            "merged pull state, actor, timestamps or commit were not preserved: "
            + json.dumps(merge_facts, sort_keys=True)
        )
    review_authors = {item.author["login"] for item in by_class["reviews"].objects if item.author}
    if not {"developer", "viewer"} <= review_authors:
        raise ProbeFailure("review fixtures did not retain distinct non-importer native authors")
    if not any(
        item.facts.get("state") == "APPROVED"
        and item.author
        and item.author.get("login") == "developer"
        and item.facts.get("commit_id")
        and isinstance(item.facts.get("submitted_at") or item.facts.get("updated_at"), str)
        for item in by_class["reviews"].objects
    ):
        review_facts = [
            {
                "state": item.facts.get("state"),
                "author": item.author,
                "commit_id": item.facts.get("commit_id"),
                "submitted_at": item.facts.get("submitted_at"),
                "updated_at": item.facts.get("updated_at"),
                "body": item.facts.get("body"),
            }
            for item in by_class["reviews"].objects
        ]
        raise ProbeFailure(
            "approval review decision and native facts were not preserved: "
            + json.dumps(review_facts, sort_keys=True)
        )
    review_bodies = [item.facts.get("body") for item in by_class["reviews"].objects]
    inline_bodies = [item.facts.get("body") for item in by_class["review_comments"].objects]
    if (
        review_bodies.count("Synthetic equal-body review") < 2
        or inline_bodies.count("Synthetic equal-body inline comment") < 2
    ):
        raise ProbeFailure("equal-text reviews/comments were not retained as distinct objects")
    review_by_id = {item.key: item for item in by_class["reviews"].objects}
    inline = [
        item
        for item in by_class["review_comments"].objects
        if item.facts.get("body") == "Synthetic equal-body inline comment"
    ]
    if len(inline) < 2 or any(
        item.parent is None
        or item.facts.get("path") != "README.md"
        or not isinstance(item.facts.get("position"), int)
        or item.facts.get("commit_id") != review_by_id[item.parent["key"]].facts.get("commit_id")
        for item in inline
    ):
        raise ProbeFailure("review commit or inline diff-position relationships were not retained")
    inline_authors = {item.author["login"] for item in inline if item.author}
    reply = next(
        (
            item
            for item in by_class["review_comments"].objects
            if item.facts.get("body") == "Synthetic review reply"
        ),
        None,
    )
    if inline_authors != {"developer", "viewer"} or not (
        reply and reply.author and reply.author.get("login") == "developer"
    ):
        raise ProbeFailure("inline comments and reply did not retain their native authors")
    if fixture["resolution_transitions"] != {
        "resolved_once": True,
        "unresolved": True,
        "resolved_again": True,
    }:
        raise ProbeFailure("resolve, unresolve and re-resolve transitions were not observed live")
    if not any(item.facts.get("resolver", {}).get("login") == ADMIN[0] for item in inline):
        raise ProbeFailure(
            "review resolve transition was not retained as an explicit resolver fact"
        )
    live_timeline = (
        _get(
            SOURCE_URL,
            f"/repos/{SELECTED}/issues/{fixture['pull']['number']}/timeline",
        )
        or []
    )
    captured_timeline_ids = [
        item.key
        for item in by_class["timelines"].objects
        if item.parent == {"content_class": "pull_requests", "key": str(fixture["pull"]["id"])}
    ]
    if captured_timeline_ids != [str(item["id"]) for item in live_timeline]:
        raise ProbeFailure("pull timeline API ordering was not preserved by inventory")
    merged_timeline = [
        item
        for item in by_class["timelines"].objects
        if item.parent
        == {"content_class": "pull_requests", "key": str(fixture["merged_pull"]["id"])}
    ]
    merge_timeline_event = next(
        (
            item
            for item in merged_timeline
            if "merge" in str(item.facts.get("type", "")).lower()
            and item.author
            and item.author.get("login") == ADMIN[0]
        ),
        None,
    )
    if merge_timeline_event is None:
        timeline_facts = [
            {"type": item.facts.get("type"), "author": item.author} for item in merged_timeline
        ]
        raise ProbeFailure(
            "merged pull timeline did not retain the native merge actor: "
            + json.dumps(timeline_facts, sort_keys=True)
        )
    if not by_class["comments"].objects or any(
        item.parent is None for item in by_class["comments"].objects
    ):
        raise ProbeFailure("issue comment parent relationships missing")
    if any(
        item.parent is None
        for item in by_class["attachments"].objects + by_class["release_assets"].objects
    ):
        raise ProbeFailure("binary parent relationships missing")
    for class_name, observation in by_class.items():
        keys = {item.key for item in observation.objects}
        for item in observation.objects:
            if (
                item.parent
                and item.parent["content_class"] == class_name
                and item.parent["key"] not in keys
            ):
                raise ProbeFailure(f"parent relationship missing from {class_name}")
    if not any(
        item.size_bytes and item.size_bytes > 0
        for item in by_class["attachments"].objects + by_class["release_assets"].objects
    ):
        raise ProbeFailure("binary payload size facts missing")
    after = request(SOURCE_URL, "GET", f"/repos/{CONTROL}", auth=ADMIN).json()
    if (
        after != control_before
        or (
            _get(SOURCE_URL, f"/repos/{CONTROL}/issues", params={"state": "all", "type": "issues"})
            or []
        )
        != control_issues_before
    ):
        raise ProbeFailure("control repository changed during collection")
    pull_content_version = _qualify_pull_content_version()
    api(SOURCE_URL, "POST", control_reaction_path, json_body={"content": "+1"})
    reaction_after_post = request(SOURCE_URL, "GET", control_reaction_path, auth=ADMIN).json()
    if not isinstance(reaction_after_post, list) or len(reaction_after_post) != 1:
        raise ProbeFailure("reaction POST did not produce one reaction")
    delete = request(
        SOURCE_URL, "DELETE", control_reaction_path, auth=ADMIN, json_body={"content": "+1"}
    )
    if delete.status_code != 204:
        raise ProbeFailure("reaction DELETE was not accepted")
    empty_reactions = request(SOURCE_URL, "GET", control_reaction_path, auth=ADMIN).json()
    if empty_reactions is not None:
        raise ProbeFailure("empty reaction endpoint did not return JSON null after delete")
    comment_empty = request(SOURCE_URL, "GET", control_comment_reaction_path, auth=ADMIN)
    if comment_empty.status_code != 200 or comment_empty.json() is not None:
        raise ProbeFailure("empty comment reaction endpoint did not return JSON null")
    api(SOURCE_URL, "POST", control_comment_reaction_path, json_body={"content": "+1"})
    comment_added = request(SOURCE_URL, "GET", control_comment_reaction_path, auth=ADMIN)
    if comment_added.status_code != 200 or len(comment_added.json()) != 1:
        raise ProbeFailure("comment reaction was not created")
    removed = request(
        SOURCE_URL, "DELETE", control_comment_reaction_path, auth=ADMIN, json_body={"content": "+1"}
    )
    final = request(SOURCE_URL, "GET", control_comment_reaction_path, auth=ADMIN)
    if removed.status_code != 204 or final.status_code != 200 or final.json() is not None:
        raise ProbeFailure("comment reaction removal did not restore qualified null")
    return {
        "status": "passed",
        "assertions": {
            name: True
            for name in (
                "explicit_empty_repository",
                "all_classes_explicit",
                "direct_counts_match",
                "native_identity_and_parents",
                "payload_sizes_positive",
                "read_only_collection",
                "control_unread_unchanged",
                "qualified_empty_reactions",
                "distinct_review_authors_and_equal_text_origins",
                "pull_refs_state_review_positions_and_timeline_order",
                "review_reply_and_resolution_routes",
                "pull_merged_state_and_approval_decision",
                "inline_reply_authors_and_resolution_transitions",
                "pull_content_version_rejects_stale_without_mutation",
                "unsupported_counts_unknown",
            )
        },
        "direct_counts": direct_counts,
        "gitea_version": document.gitea_version,
        "selected_repository": SELECTED,
        "control_repository": CONTROL,
        "observed_classes": sorted(observed),
        "unsupported_classes": sorted(unsupported),
        "counts": {name: item.count for name, item in by_class.items()},
        "selection_summary": inventory_summary(document),
        "source_writes_during_collection": sum(method != "GET" for method, _ in calls),
        "empty_reaction_endpoint": empty_reactions,
        "pull_content_version": pull_content_version,
        "pull_merge_evidence": {
            "state": merged_pull_object.facts["state"],
            "merged": merged_pull_object.facts["merged"],
            "closed_at": merged_pull_object.facts["closed_at"],
            "merged_at": merged_pull_object.facts["merged_at"],
            "merge_commit_sha": merged_pull_object.facts["merge_commit_sha"],
            "merged_by_response": merged_pull_object.facts["merged_by"],
            "timeline_merge_actor": merge_timeline_event.author["login"],
        },
    }
