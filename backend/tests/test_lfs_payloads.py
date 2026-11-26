from __future__ import annotations

import hashlib
import os
from pathlib import Path

import gitferry.lfs_payloads as lfs_payloads
import pytest
from gitferry.lfs_payloads import (
    LFSPayloadError,
    LFSPointer,
    parse_lfs_pointer,
    publish_lfs_object,
    verify_lfs_object,
)


def _pointer(data: bytes) -> bytes:
    return (
        b"version https://git-lfs.github.com/spec/v1\n"
        + f"oid sha256:{hashlib.sha256(data).hexdigest()}\nsize {len(data)}\n".encode()
    )


def test_parse_valid_zero_and_unicode_nonpointer() -> None:
    assert parse_lfs_pointer(_pointer(b"")) == LFSPointer(hashlib.sha256(b"").hexdigest(), 0)
    assert parse_lfs_pointer("unicode 🚢".encode()) is None
    assert parse_lfs_pointer(b"version 2.0\nordinary text\n") is None


@pytest.mark.parametrize(
    "value",
    [
        b"version https://git-lfs.github.com/spec/v2\noid sha256:" + b"a" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"A" * 64 + b"\nsize 1\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 01\n",
        b"version https://git-lfs.github.com/spec/v1\noid sha256:"
        + b"a" * 64
        + b"\nsize 1\nextra\n",
        b"version https://git-lfs.github.com/spec/v1\nbad\n",
    ],
)
def test_parse_recognized_malformed_or_extension_is_rejected(value: bytes) -> None:
    with pytest.raises(LFSPayloadError):
        parse_lfs_pointer(value)


def test_verify_rejects_size_hash_max_and_nonregular(tmp_path: Path) -> None:
    source = tmp_path / "object"
    payload = b"payload"
    source.write_bytes(payload)
    pointer = LFSPointer(hashlib.sha256(payload).hexdigest(), len(payload))
    assert verify_lfs_object(source, pointer) == (len(payload), pointer.oid)
    with pytest.raises(LFSPayloadError):
        verify_lfs_object(source, LFSPointer(pointer.oid, pointer.size + 1))
    with pytest.raises(LFSPayloadError):
        verify_lfs_object(source, LFSPointer("0" * 64, pointer.size))
    with pytest.raises(LFSPayloadError):
        verify_lfs_object(source, pointer, max_bytes=1)
    source.unlink()
    source.mkdir()
    with pytest.raises(LFSPayloadError):
        verify_lfs_object(source, pointer)


def test_publish_rejects_traversal_symlink_hardlink_and_never_overwrites(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "source"
    source.write_bytes(b"object")
    pointer = LFSPointer(hashlib.sha256(b"object").hexdigest(), 6)
    with pytest.raises(LFSPayloadError):
        publish_lfs_object(source, package, "../escape", pointer)
    outside = tmp_path / "outside"
    outside.mkdir()
    (package / "objects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(LFSPayloadError):
        publish_lfs_object(source, package, "objects/object", pointer)
    (package / "objects").unlink()
    hardlink = tmp_path / "hardlink"
    os.link(source, hardlink)
    with pytest.raises(LFSPayloadError):
        verify_lfs_object(hardlink, pointer)
    hardlink.unlink()
    assert publish_lfs_object(source, package, "objects/object", pointer) == (
        6,
        pointer.oid,
    )
    with pytest.raises(LFSPayloadError):
        publish_lfs_object(source, package, "objects/object", pointer)


def test_publish_failure_does_not_leave_partial_destination(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "source"
    source.write_bytes(b"object")
    pointer = LFSPointer("0" * 64, 6)
    with pytest.raises(LFSPayloadError):
        publish_lfs_object(source, package, "objects/object", pointer)
    assert not (package / "objects" / "object").exists()
    assert list((package / "objects").glob(".*")) == [] if (package / "objects").exists() else True


@pytest.mark.parametrize(
    "oid,size",
    [("a" * 63, 0), ("A" * 64, 0), ("a" * 64, -1), ("a" * 64, 2**63)],
)
def test_pointer_constructor_rejects_invalid_identity_or_size(oid: str, size: int) -> None:
    with pytest.raises(LFSPayloadError):
        LFSPointer(oid, size)


def test_publish_rejects_pointer_mismatch_without_destination(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "source"
    source.write_bytes(b"object")
    pointer = LFSPointer(hashlib.sha256(b"other").hexdigest(), 5)
    with pytest.raises(LFSPayloadError):
        publish_lfs_object(source, package, "objects/object", pointer)
    assert not (package / "objects" / "object").exists()


def test_publish_detects_source_mutation_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "source"
    original = b"object"
    source.write_bytes(original)
    pointer = LFSPointer(hashlib.sha256(original).hexdigest(), len(original))
    open_unique = lfs_payloads._open_unique_file

    def mutate_after_open(path: Path, label: str) -> int:
        descriptor = open_unique(path, label)
        source.write_bytes(b"mutated")
        return descriptor

    monkeypatch.setattr(lfs_payloads, "_open_unique_file", mutate_after_open)
    with pytest.raises(LFSPayloadError):
        publish_lfs_object(source, package, "objects/object", pointer)
    assert not (package / "objects" / "object").exists()
