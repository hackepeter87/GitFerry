from __future__ import annotations

import ssl

import httpx
import pytest
from gitferry.content_inventory import CONTENT_CLASSES, ClassObservation, InventoryObject
from gitferry.source_inventory import (
    InventoryCollectionError,
    _comments,
    _InventoryReader,
    _issues,
    _object,
    _reactions,
    _review_comments,
    _reviews,
    collect_inventory,
)
from gitferry.source_snapshot import SourceSnapshotError


def _reader(handler):
    return _InventoryReader(
        "https://source.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        httpx.MockTransport(handler),
    )


def test_paginated_inventory_requires_total_and_finishes_at_exact_total() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        values = [{"id": i} for i in range(1, 51)] if page == "1" else [{"id": 51}]
        return httpx.Response(200, headers={"X-Total-Count": "51"}, json=values)

    reader = _reader(handle)
    try:
        assert [item["id"] for item in reader.page("/repos/acme/one/labels")] == list(range(1, 52))
    finally:
        reader.close()


def test_paginated_inventory_rejects_duplicate_ids_and_missing_total() -> None:
    def duplicate(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"X-Total-Count": "2"}, json=[{"id": 1}, {"id": 1}])

    reader = _reader(duplicate)
    try:
        with pytest.raises(SourceSnapshotError, match="duplicate"):
            reader.page("/repos/acme/one/labels")
    finally:
        reader.close()


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(
            200,
            headers={"X-Total-Count": "51" if request.url.params["page"] == "1" else "52"},
            json=[{"id": i} for i in range(1, 51)]
            if request.url.params["page"] == "1"
            else [{"id": 51}, {"id": 52}],
        ),
        lambda request: httpx.Response(
            200,
            headers={"X-Total-Count": "51"},
            json=[{"id": i} for i in range(1, 50)]
            if request.url.params["page"] == "1"
            else [{"id": 50}, {"id": 51}],
        ),
    ],
)
def test_paginated_inventory_rejects_changing_total_or_short_page(handler) -> None:
    reader = _reader(handler)
    try:
        with pytest.raises(SourceSnapshotError, match="incomplete or changed"):
            reader.page("/repos/acme/one/labels")
    finally:
        reader.close()


def test_issue_and_pull_request_objects_and_children_keep_distinct_parents() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/issues"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "1"},
                json=[{"id": 3, "number": 3, "title": "issue", "pull_request": None}],
            )
        if path.endswith("/pulls"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "1"},
                json=[{"id": 4, "number": 4, "title": "pull"}],
            )
        if path.endswith("/issues/3/comments"):
            return httpx.Response(200, headers={"X-Total-Count": "1"}, json=[{"id": 31}])
        if path.endswith("/pulls/4/reviews"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "1"},
                json=[{"id": 41, "state": "APPROVED"}],
            )
        if path.endswith("/pulls/4/reviews/41/comments"):
            return httpx.Response(
                200, json=[{"id": 411, "pull_request_review_id": 41, "body": "review note"}]
            )
        raise AssertionError(path)

    reader = _reader(handle)
    try:
        issue = _issues(
            reader,
            "/repos/acme/one/issues",
            "issues",
            {},
        )
        pull_request = _issues(
            reader,
            "/repos/acme/one/pulls",
            "pull_requests",
            {},
        )
    finally:
        reader.close()

    assert issue.status == "observed" and issue.objects[0].namespace == "global-issue-id"
    assert (
        pull_request.status == "observed" and pull_request.objects[0].namespace == "global-pull-id"
    )


def test_comments_reviews_and_review_comments_reference_their_native_parent_class() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/issues/3/comments") or path.endswith("/issues/4/comments"):
            return httpx.Response(200, headers={"X-Total-Count": "1"}, json=[{"id": 31}])
        if path.endswith("/pulls/4/reviews"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "1"},
                json=[{"id": 41, "state": "APPROVED"}],
            )
        if path.endswith("/pulls/4/reviews/41/comments"):
            return httpx.Response(
                200, json=[{"id": 411, "pull_request_review_id": 41, "body": "review note"}]
            )
        raise AssertionError(path)

    issue = ClassObservation(
        content_class="issues",
        status="observed",
        count=1,
        objects=[InventoryObject(key="3", namespace="global-issue-id", issue_index="3")],
    )
    pull_request = ClassObservation(
        content_class="pull_requests",
        status="observed",
        count=1,
        objects=[InventoryObject(key="4", namespace="global-pull-id", issue_index="4")],
    )
    observations = {"issues": issue, "pull_requests": pull_request}
    reader = _reader(handle)
    try:
        comments = _comments(reader, "/repos/acme/one", observations, pulls=False)
        pull_comments = _comments(reader, "/repos/acme/one", observations, pulls=True)
        reviews = _reviews(reader, "/repos/acme/one", observations)
        review_comments = _review_comments(
            reader,
            "/repos/acme/one",
            {**observations, "reviews": reviews},
        )
    finally:
        reader.close()

    assert comments.objects[0].parent == {"content_class": "issues", "key": "3"}
    assert pull_comments.objects[0].parent == {"content_class": "pull_requests", "key": "4"}
    assert reviews.objects[0].parent == {"content_class": "pull_requests", "key": "4"}
    assert review_comments.objects[0].parent == {"content_class": "reviews", "key": "41"}


def test_equal_review_text_keeps_distinct_native_origins_and_body_is_not_identity() -> None:
    changed = False

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = "changed" if changed else "same"
        if path.endswith("/pulls/4/reviews"):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "2"},
                json=[
                    {"id": 41, "state": "COMMENT", "body": body},
                    {"id": 42, "state": "COMMENT", "body": "same"},
                ],
            )
        if path.endswith("/pulls/4/reviews/41/comments"):
            return httpx.Response(
                200,
                json=[
                    {"id": 411, "pull_request_review_id": 41, "body": body},
                    {"id": 412, "pull_request_review_id": 41, "body": "same"},
                ],
            )
        if path.endswith("/pulls/4/reviews/42/comments"):
            return httpx.Response(200, json=[])
        raise AssertionError(path)

    pull_request = ClassObservation(
        content_class="pull_requests",
        status="observed",
        count=1,
        objects=[InventoryObject(key="4", namespace="global-pull-id", issue_index="4")],
    )
    reader = _reader(handle)
    try:
        before_reviews = _reviews(reader, "/repos/acme/one", {"pull_requests": pull_request})
        before_comments = _review_comments(
            reader,
            "/repos/acme/one",
            {"reviews": before_reviews},
        )
        changed = True
        after_reviews = _reviews(reader, "/repos/acme/one", {"pull_requests": pull_request})
        after_comments = _review_comments(
            reader,
            "/repos/acme/one",
            {"reviews": after_reviews},
        )
    finally:
        reader.close()

    assert [item.key for item in before_reviews.objects] == ["41", "42"]
    assert [item.key for item in before_comments.objects] == ["411", "412"]
    assert [item.key for item in after_reviews.objects] == ["41", "42"]
    assert [item.key for item in after_comments.objects] == ["411", "412"]
    assert before_reviews.objects[0].facts["body"] == "same"
    assert after_reviews.objects[0].facts["body"] == "changed"
    assert before_comments.objects[0].facts["body"] == "same"
    assert after_comments.objects[0].facts["body"] == "changed"


def test_empty_reaction_null_is_only_accepted_on_qualified_endpoints() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/3/reactions"):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, content=b"null")
        if request.url.path.endswith("/issues/comments/31/reactions"):
            return httpx.Response(200, content=b"null")
        if request.url.path.endswith("/unqualified-list"):
            return httpx.Response(200, content=b"null")
        raise AssertionError(request.url.path)

    issue = ClassObservation(
        content_class="issues",
        status="observed",
        count=1,
        objects=[InventoryObject(key="3", namespace="global-issue-id", issue_index="3")],
    )
    comment = ClassObservation(
        content_class="comments",
        status="observed",
        count=1,
        objects=[InventoryObject(key="31", namespace="global-comment-id", native_id="31")],
    )
    reader = _reader(handle)
    try:
        reactions = _reactions(
            reader,
            "/repos/acme/one",
            {
                "issues": issue,
                "pull_requests": ClassObservation(
                    content_class="pull_requests", status="observed", count=0, objects=[]
                ),
                "comments": comment,
                "pull_comments": ClassObservation(
                    content_class="pull_comments", status="observed", count=0, objects=[]
                ),
            },
        )
        assert reactions.status == "observed" and reactions.count == 0
        with pytest.raises(SourceSnapshotError, match="malformed"):
            reader.full("/repos/acme/one/unqualified-list")
    finally:
        reader.close()


def test_payload_size_is_selected_only_for_payload_objects() -> None:
    value = {"id": 7, "name": "artifact", "size": 123}
    metadata = _object(value, "release", ("name",))
    payload = _object(value, "attachment", ("name",), payload=True, author_key=None)
    assert metadata.size_bytes is None
    assert payload.size_bytes == 123

    def missing_total(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    reader = _reader(missing_total)
    try:
        with pytest.raises(SourceSnapshotError, match="total"):
            reader.page("/repos/acme/one/labels")
    finally:
        reader.close()


def test_refs_are_bounded_nonpaged_and_do_not_require_total_header() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"url": "ref/heads/main", "ref": "refs/heads/main", "object": {"sha": "a"}}]
        )

    reader = _reader(handle)
    try:
        value = reader.full("/repos/acme/one/git/refs")
        assert value[0]["ref"] == "refs/heads/main"
    finally:
        reader.close()


def test_collect_inventory_exposes_unsupported_lfs_and_excluded_classes() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/version"):
            return httpx.Response(200, json={"version": "29.1.0"})
        if path.endswith("/repos/acme/one"):
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "full_name": "acme/one",
                    "owner": {"id": 3, "login": "acme"},
                    "size": 4,
                },
            )
        if path.endswith("/orgs/acme"):
            return httpx.Response(200, json={"id": 3, "name": "acme", "username": "acme"})
        if path.endswith("/git/refs"):
            return httpx.Response(200, json=[])
        if "/wiki/" in path:
            return httpx.Response(404, json={"message": "no wiki"})
        if (
            path.endswith("/issues")
            or path.endswith("/teams")
            or path.endswith("/labels")
            or path.endswith("/releases")
            or path.endswith("/pulls")
            or path.endswith("/milestones")
            or path.endswith("/collaborators")
        ):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, json=[])
        if path.endswith("/issues/comments"):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, json=[])
        if "/issues/" in path and any(
            path.endswith(suffix) for suffix in ("/assets", "/timeline", "/reactions", "/comments")
        ):
            return httpx.Response(
                200,
                headers={"X-Total-Count": "0"} if path.endswith(("/timeline", "/comments")) else {},
                json=[],
            )
        if "/releases/" in path and path.endswith("/assets"):
            return httpx.Response(200, json=[])
        if "/pulls/" in path and (path.endswith("/reviews") or "/reviews/" in path):
            return httpx.Response(200, headers={"X-Total-Count": "0"}, json=[])
        raise AssertionError(path)

    requested = [name for name in CONTENT_CLASSES if name != "labels"]
    document = collect_inventory(
        "https://source.invalid",
        "synthetic-token",
        ssl.create_default_context(),
        [{"repository": "acme/one", "global_id": "7"}],
        requested,
        source_instance_key="source.synthetic",
        installation_marker="install.synthetic",
        snapshot_id="snapshot-a",
        created_at="2026-11-21T10:00:00Z",
        transport=httpx.MockTransport(handle),
    )
    classes = {item.content_class: item for item in document.repositories[0].classes}
    assert document.schema_version == document.comparison_version == 3
    assert document.gitea_version == "29.1.0"
    assert classes["lfs"].status == "not-supported"
    assert classes["lfs"].count is None
    assert classes["labels"].status == "observed" and classes["labels"].count == 0
    assert "labels" not in document.repositories[0].requested_classes
    assert classes["wiki"].status == "blocked" and classes["wiki"].count is None
    assert classes["git_refs"].status == "observed"
    assert set(classes) == set(CONTENT_CLASSES)


def test_collect_inventory_rejects_selected_repository_identity_mismatch() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "28.4.0"})
        return httpx.Response(
            200, json={"id": 8, "full_name": "acme/one", "owner": {"id": 3, "login": "acme"}}
        )

    with pytest.raises(InventoryCollectionError):
        collect_inventory(
            "https://source.invalid",
            "synthetic-token",
            ssl.create_default_context(),
            [{"repository": "acme/one", "global_id": "7"}],
            ["git_refs"],
            source_instance_key="source.synthetic",
            installation_marker="install.synthetic",
            snapshot_id="snapshot-a",
            created_at="2026-11-21T10:00:00Z",
            transport=httpx.MockTransport(handle),
        )


@pytest.mark.parametrize("status", [403, 429, 503])
def test_unavailable_selected_class_is_blocked_not_empty(status: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "28.4.0"})
        if request.url.path.endswith("/repos/acme/one"):
            return httpx.Response(
                200, json={"id": 7, "full_name": "acme/one", "owner": {"id": 2, "login": "acme"}}
            )
        return httpx.Response(status, json={"message": "synthetic unavailable"})

    document = collect_inventory(
        "https://source.invalid",
        "private-test-credential",
        ssl.create_default_context(),
        [{"repository": "acme/one", "global_id": "7"}],
        ["issues", "comments"],
        source_instance_key="a",
        installation_marker="install-a",
        snapshot_id="unavailable",
        created_at="2026-11-21T00:00:00Z",
        transport=httpx.MockTransport(handle),
    )
    selected = [
        c for c in document.repositories[0].classes if c.content_class in {"issues", "comments"}
    ]
    assert all(c.status == "blocked" and c.count is None and not c.objects for c in selected)


def test_oversized_total_is_rejected_before_following_pages() -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, headers={"X-Total-Count": "5001"}, json=[])

    reader = _reader(handle)
    try:
        with pytest.raises(SourceSnapshotError):
            reader.page("/repos/acme/one/issues")
        assert len(calls) == 1
    finally:
        reader.close()


def test_issue_assignments_preserve_native_relationship_ids() -> None:
    value = _object(
        {
            "id": 9,
            "number": 2,
            "labels": [{"id": 11}],
            "milestone": {"id": 12},
            "assignees": [{"id": 13, "login": "synthetic-assignee"}],
            "user": {"id": 14, "login": "synthetic-author"},
        },
        "global-issue-id",
        (),
        issue=True,
    )
    assert value.native_id == "9" and value.issue_index == "2"
    assert value.facts["label_ids"] == ["11"]
    assert value.facts["milestone_id"] == "12"
    assert value.facts["assignees"] == [{"global_id": "13", "login": "synthetic-assignee"}]
    assert value.author == {"account_id": "14", "login": "synthetic-author"}
