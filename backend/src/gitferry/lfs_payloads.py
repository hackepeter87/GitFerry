"""Bounded local Git LFS pointer/object payload primitives."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .packages import UnsafePackagePath, resolve_package_member

_VERSION = "version https://git-lfs.github.com/spec/v1"
_MAX_POINTER_BYTES = 1024
_CHUNK = 1024 * 1024
_DEFAULT_MAX_BYTES = 1024 * 1024 * 1024


class LFSPayloadError(ValueError):
    """An LFS pointer or payload failed the local safety contract."""


@dataclass(frozen=True)
class LFSPointer:
    oid: str
    size: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.oid, str)
            or len(self.oid) != 64
            or any(char not in "0123456789abcdef" for char in self.oid)
        ):
            raise LFSPayloadError("LFS pointer oid must be a lowercase SHA-256")
        if type(self.size) is not int or not 0 <= self.size <= 2**63 - 1:
            raise LFSPayloadError("LFS pointer size is out of range")


def parse_lfs_pointer(data: bytes) -> LFSPointer | None:
    """Parse only the exact three-line Git LFS pointer format."""
    if not isinstance(data, bytes):
        return None
    if len(data) > _MAX_POINTER_BYTES and data.startswith(b"version "):
        raise LFSPayloadError("LFS pointer exceeds the supported size")
    if not data.startswith(_VERSION.encode()):
        if data.startswith(b"version https://git-lfs.github.com/spec/"):
            raise LFSPayloadError("LFS pointer has an unsupported version")
        return None
    if not data.endswith(b"\n"):
        raise LFSPayloadError("LFS pointer must end with a newline")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise LFSPayloadError("LFS pointer is not UTF-8") from None
    lines = text.split("\n")[:-1]
    if len(lines) != 3 or lines[0] != _VERSION:
        raise LFSPayloadError("LFS pointer has unsupported fields or version")
    oid_name, separator, oid = lines[1].partition(" ")
    size_name, size_separator, size_text = lines[2].partition(" ")
    if oid_name != "oid" or separator != " " or size_name != "size" or size_separator != " ":
        raise LFSPayloadError("LFS pointer fields are malformed")
    if len(oid) != 71 or not oid.startswith("sha256:") or any(
        char not in "0123456789abcdef" for char in oid[7:]
    ):
        raise LFSPayloadError("LFS pointer oid is not a lowercase SHA-256")
    if not size_text.isascii() or not size_text.isdigit() or (
        len(size_text) > 1 and size_text[0] == "0"
    ):
        raise LFSPayloadError("LFS pointer size is not canonical")
    return LFSPointer(oid=oid[7:], size=int(size_text))


def _open_unique_file(path: Path, label: str) -> int:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
    except OSError:
        raise LFSPayloadError(f"{label} is unavailable") from None
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        os.close(descriptor)
        raise LFSPayloadError(f"{label} must be a unique regular file")
    return descriptor


def _digest(path: Path, max_bytes: int | None) -> tuple[int, str]:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise LFSPayloadError("max_bytes must be positive")
    descriptor = _open_unique_file(path, "LFS object")
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "rb") as stream:
            while chunk := stream.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise LFSPayloadError("LFS object exceeds max_bytes")
                digest.update(chunk)
    except OSError:
        raise LFSPayloadError("LFS object cannot be read") from None
    return size, digest.hexdigest()


def verify_lfs_object(
    path: Path, pointer: LFSPointer, max_bytes: int = _DEFAULT_MAX_BYTES
) -> tuple[int, str]:
    """Stream-verify one local object against its pointer."""
    if not isinstance(pointer, LFSPointer):
        raise LFSPayloadError("LFS pointer is invalid")
    size, digest = _digest(Path(path), max_bytes)
    if size != pointer.size or digest != pointer.oid:
        raise LFSPayloadError("LFS object does not match its pointer")
    return size, digest


def _reject_symlink_components(root: Path, member: str) -> None:
    candidate = root
    for part in member.replace("\\", "/").split("/"):
        candidate /= part
        if candidate.is_symlink():
            raise LFSPayloadError("LFS package member uses a symlink")


def publish_lfs_object(
    source: Path,
    package_root: Path,
    member: str,
    pointer: LFSPointer,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> tuple[int, str]:
    """Atomically publish a verified object below an owned package root."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise LFSPayloadError("max_bytes must be positive")
    source = Path(source)
    package_root = Path(package_root)
    if package_root.is_symlink() or not package_root.is_dir():
        raise LFSPayloadError("LFS package root must be a regular directory")
    _reject_symlink_components(package_root, member)
    try:
        target = resolve_package_member(package_root, member)
    except UnsafePackagePath:
        raise LFSPayloadError("LFS package member path is unsafe") from None
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise LFSPayloadError("LFS package destination already exists")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            descriptor = _open_unique_file(source, "LFS source object")
            digest_builder = hashlib.sha256()
            size = 0
            with os.fdopen(descriptor, "rb") as input_stream:
                while chunk := input_stream.read(_CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise LFSPayloadError("LFS object exceeds max_bytes")
                    digest_builder.update(chunk)
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        digest = digest_builder.hexdigest()
        if size != pointer.size or digest != pointer.oid:
            raise LFSPayloadError("LFS object does not match its pointer")
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise LFSPayloadError("LFS package destination already exists") from None
        temporary.unlink()
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except LFSPayloadError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    except (OSError, UnsafePackagePath):
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise LFSPayloadError("LFS object publication failed") from None
    return size, digest

