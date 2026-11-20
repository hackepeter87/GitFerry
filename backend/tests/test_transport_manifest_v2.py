from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from gitferry.transport import (
    ManifestValidationError,
    load_transport,
    read_document,
    write_transport,
)
from test_transport_manifest import manifest


def manifest_v2(*, comment_first: bool = False) -> dict[str, object]:
    document = manifest()
    document["schema_version"] = 2
    document["manifest_id"] = "manifest-v2-forward-1"
    document["selected_repositories"][0].update(
        {
            "object_types": ["issue", "issue_comment"],
            "fields": ["title", "body", "state", "labels", "comment.body"],
            "capabilities": [
                "issue-create",
                "issue-update",
                "issue-comment-create",
                "issue-comment-update",
            ],
        }
    )
    comment = {
        "origin": {
            "instance_key": "instance-a",
            "repository": "synthetic/selected",
            "object_type": "issue_comment",
            "global_id": "9007199254740995",
            "parent_issue_origin": {
                "instance_key": "instance-a",
                "repository": "synthetic/selected",
                "object_type": "issue",
                "global_id": "9007199254740993",
            },
            "created_at": "2026-11-20T09:01:00Z",
        },
        "replicas": [
            {
                "instance_key": "instance-a",
                "repository": "synthetic/selected",
                "global_id": "9007199254740995",
                "parent_issue_index": "3",
                "native_created_at": "2026-11-20T09:01:00Z",
            }
        ],
        "author": {
            "realm_key": "realm-a",
            "login": "comment-author",
            "external_id": "SYNTHETIC-A-1002",
            "ou_path": "OU=People,DC=a,DC=invalid",
        },
        "comparison": {
            "ruleset": "gitea-issue-comment-body",
            "ruleset_version": 1,
            "original_baseline": {"body": "Unicode: Fähre 🚢\n\n**Markdown**"},
            "confirmed_import": None,
            "current": {"body": "Unicode: Fähre 🚢\n\n**Markdown**"},
            "baseline_origin": {
                "instance_key": "instance-a",
                "transfer_round_id": "round-synthetic-1",
            },
        },
    }
    if comment_first:
        document["objects"].insert(0, comment)
    else:
        document["objects"].append(comment)
    return document


def test_v2_accepts_comment_before_its_parent_and_preserves_content(tmp_path: Path) -> None:
    artifact = write_transport(tmp_path / "v2", manifest_v2(comment_first=True))
    loaded = load_transport(tmp_path / "v2")

    assert loaded.document["objects"][0]["origin"]["object_type"] == "issue_comment"
    assert loaded.document["objects"][0]["comparison"]["current"]["body"].endswith("**Markdown**")
    assert artifact.digest_sha256 == hashlib.sha256(artifact.manifest_path.read_bytes()).hexdigest()


def test_v2_requires_typed_parent_and_qualified_author(tmp_path: Path) -> None:
    missing_parent = manifest_v2()
    del missing_parent["objects"][1]["origin"]["parent_issue_origin"]
    with pytest.raises(ManifestValidationError):
        write_transport(tmp_path / "missing-parent", missing_parent)

    missing_author = manifest_v2()
    del missing_author["objects"][1]["author"]["external_id"]
    with pytest.raises(ManifestValidationError):
        write_transport(tmp_path / "missing-author", missing_author)


def test_v1_serialized_bytes_and_digest_are_unchanged_by_v2_support(tmp_path: Path) -> None:
    document = manifest()
    expected = (
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    artifact = write_transport(tmp_path / "v1", document)

    assert artifact.manifest_path.read_bytes() == expected
    assert artifact.digest_sha256 == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize(
    ("name", "digest"),
    (
        (
            "transport-forward.v1.json",
            "0db8e8afc0c35a4471425c7567673a16be09030379482a5878192e12a8bd9b78",
        ),
        (
            "transport-return.v1.json",
            "aa6ddbf288215a8f3a1dd3a1b6dd591a76ba842ea6d211f9c40c75831f87d7e7",
        ),
    ),
)
def test_existing_v1_examples_validate_without_byte_or_digest_change(
    name: str, digest: str
) -> None:
    path = Path(__file__).parents[2] / "examples" / name
    before = path.read_bytes()

    assert read_document(path)["schema_version"] == 1
    assert path.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == digest
