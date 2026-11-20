"""Security primitives shared by future package import implementations."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class UnsafePackagePath(ValueError):
    """An archive or manifest member would escape its owned package root."""


def resolve_package_member(owned_root: Path, member: str) -> Path:
    """Resolve a portable member name strictly below an owned root.

    This function does not extract anything. A future extractor must also reject
    symlink/hardlink entries and re-check the parent immediately before writing.
    """

    portable = member.replace("\\", "/")
    relative = PurePosixPath(portable)
    has_unsafe_part = any(part in {"", ".", ".."} for part in relative.parts)
    if relative.is_absolute() or not portable or has_unsafe_part:
        raise UnsafePackagePath(f"unsafe package member: {member!r}")

    root = owned_root.resolve()
    target = root.joinpath(*relative.parts).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UnsafePackagePath(f"package member escapes owned root: {member!r}") from exc
    if target == root:
        raise UnsafePackagePath("package member must be below the owned root")
    return target
