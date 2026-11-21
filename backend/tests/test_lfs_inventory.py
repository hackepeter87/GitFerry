from __future__ import annotations

import hashlib
import subprocess

import pytest
from gitferry.lfs_inventory import inventory_lfs_pointers
from gitferry.lfs_payloads import LFSPayloadError, LFSPointer


def git(repo, *args, input=None):
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        input=input,
        check=True,
        capture_output=True,  # noqa: S607
    ).stdout.strip()


def pointer(data):
    return (
        "version https://git-lfs.github.com/spec/v1\n"
        f"oid sha256:{hashlib.sha256(data).hexdigest()}\nsize {len(data)}\n"
    ).encode()


def repository(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Synthetic")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    return tmp_path


def commit(repo, data):
    (repo / "file").write_bytes(data)
    git(repo, "add", "file")
    git(repo, "commit", "-m", "synthetic")


def test_all_reachable_history_and_tags_not_only_checkout(tmp_path):
    repo = repository(tmp_path)
    commit(repo, pointer(b"first"))
    git(repo, "tag", "historical")
    commit(repo, pointer(b"second"))
    commit(repo, b"ordinary replacement")
    # An unreachable pointer blob must not expand the selected scope.
    git(repo, "hash-object", "-w", "--stdin", input=pointer(b"unreachable"))
    expected = sorted(
        [LFSPointer(hashlib.sha256(v).hexdigest(), len(v)) for v in (b"first", b"second")],
        key=lambda p: p.oid,
    )
    assert inventory_lfs_pointers(repo) == tuple(expected)


def test_empty_git_and_large_ordinary_blob_are_not_lfs(tmp_path):
    repo = repository(tmp_path)
    assert inventory_lfs_pointers(repo) == ()
    commit(repo, b"x" * 2000000)
    assert inventory_lfs_pointers(repo) == ()


def test_malformed_pointer_and_explicit_bounds_block(tmp_path):
    repo = repository(tmp_path)
    commit(repo, pointer(b"first"))
    with pytest.raises(LFSPayloadError, match="objects"):
        inventory_lfs_pointers(repo, max_objects=1)
    commit(repo, pointer(b"second"))
    with pytest.raises(LFSPayloadError, match="count"):
        inventory_lfs_pointers(repo, max_pointers=1)
    commit(repo, b"version https://git-lfs.github.com/spec/v1\ninvalid\n")
    with pytest.raises(LFSPayloadError):
        inventory_lfs_pointers(repo)


def test_duplicate_object_with_different_size_is_conflict(tmp_path):
    repo = repository(tmp_path)
    commit(repo, pointer(b"first"))
    commit(repo, pointer(b"first").replace(b"size 5", b"size 6"))
    with pytest.raises(LFSPayloadError, match="contradictory"):
        inventory_lfs_pointers(repo)


def test_invalid_directory_timeout_and_bound(tmp_path):
    with pytest.raises(LFSPayloadError):
        inventory_lfs_pointers(tmp_path / "missing")
    repo = repository(tmp_path)
    commit(repo, pointer(b"first"))
    with pytest.raises(LFSPayloadError, match="timed out|deadline"):
        inventory_lfs_pointers(repo, timeout=0.000001)
    with pytest.raises(LFSPayloadError, match="positive"):
        inventory_lfs_pointers(repo, max_objects=0)
