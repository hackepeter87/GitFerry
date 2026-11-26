"""Real primary-Gitea qualification for the selected-content inventory collector."""

from __future__ import annotations

import base64
import json
import ssl
from typing import Any

import httpx
from gitferry.content_inventory import CONTENT_CLASSES
from gitferry.source_inventory import collect_inventory
from support import ADMIN, SOURCE_URL, WORK, ProbeFailure, api, flush_fixture_queues, request

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
    if not (_get(SOURCE_URL, root + f"/issues/{pidx}/comments") or []):
        api(
            SOURCE_URL,
            "POST",
            root + f"/issues/{pidx}/comments",
            json_body={"body": "Synthetic PR conversation"},
        )
    reviews = _get(SOURCE_URL, root + f"/pulls/{pidx}/reviews") or []
    if not reviews:
        api(
            SOURCE_URL,
            "POST",
            root + f"/pulls/{pidx}/reviews",
            json_body={
                "event": "COMMENT",
                "body": "Synthetic review",
                "comments": [
                    {"path": "README.md", "body": "Synthetic inline comment", "new_position": 1}
                ],
            },
        )
    return {"label": label, "issue": issue, "comment": comment, "release": release, "pull": pull}


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
    teams = values(root + "/teams")
    releases = values(root + "/releases")
    users = {str(item["user"]["id"]) for item in issues + pulls}
    users.update(str(c["user"]["id"]) for _, c in comments + pull_comments)
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
        "review_comments": sum(
            len(values(root + f"/pulls/{p['number']}/reviews/{r['id']}/comments"))
            for p, r in reviews
        ),
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
        "source_writes_during_collection": sum(method != "GET" for method, _ in calls),
        "empty_reaction_endpoint": empty_reactions,
    }
