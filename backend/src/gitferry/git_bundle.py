"""Create and import verified native Git bundles."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .packages import resolve_package_member


class GitBundleError(ValueError):
    """The bundle operation was rejected or Git reported invalid input."""


@dataclass(frozen=True)
class GitBundleResult:
    """Metadata for a bundle written below an owned package root."""

    member: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class GitImportResult:
    """Metadata for a verified bundle imported into a mirror repository."""

    member: str
    bundle_path: Path
    destination: Path
    size: int
    sha256: str


def _regular_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise GitBundleError(f"{label} must be a regular directory: {path}")


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise GitBundleError(f"{label} must be a regular file: {path}")


def _reject_member_symlinks(root: Path, member: str) -> None:
    """Reject symlink components before the resolver canonicalizes the member."""

    candidate = root
    for part in member.replace("\\", "/").split("/"):
        candidate /= part
        if candidate.is_symlink():
            raise GitBundleError(f"package member must not use a symlink: {member}")


def _max_bytes_value(max_bytes: Optional[int]) -> Optional[int]:  # noqa: UP045
    if max_bytes is not None and max_bytes <= 0:
        raise GitBundleError("max_bytes must be positive")
    return max_bytes


def _digest_and_size(path: Path, max_bytes: Optional[int]) -> tuple[int, str]:  # noqa: UP045
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if max_bytes is not None and size > max_bytes:
                raise GitBundleError("bundle exceeds max_bytes")
            digest.update(chunk)
    return size, digest.hexdigest()


def create_git_bundle(
    source_repository: Path,
    package_root: Path,
    member: str,
    *,
    max_bytes: Optional[int] = None,  # noqa: UP045
) -> GitBundleResult:
    """Create an ``--all`` bundle atomically below ``package_root``."""

    _max_bytes_value(max_bytes)
    _regular_directory(source_repository, "source repository")
    _regular_directory(package_root, "package root")
    _reject_member_symlinks(package_root, member)
    target = resolve_package_member(package_root, member)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise GitBundleError(f"bundle destination already exists: {target}")

    temporary_path: Optional[Path] = None  # noqa: UP045
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        subprocess.run(  # noqa: S603
            ["git", "bundle", "create", os.fspath(temporary_path), "--all"],  # noqa: S607
            cwd=source_repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        _regular_file(temporary_path, "created bundle")
        size, sha256 = _digest_and_size(temporary_path, max_bytes)
        subprocess.run(  # noqa: S603
            ["git", "bundle", "verify", os.fspath(temporary_path)],  # noqa: S607
            cwd=source_repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        try:
            # A hard link is an atomic same-filesystem create and fails if a
            # competing writer published the member after our initial check.
            os.link(temporary_path, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise GitBundleError(
                f"bundle destination appeared during publication: {target}"
            ) from exc
        temporary_path.unlink()
        temporary_path = None
        return GitBundleResult(member=member, path=target, size=size, sha256=sha256)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise GitBundleError("git bundle operation failed") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def import_git_bundle(
    package_root: Path,
    member: str,
    expected_sha256: str,
    expected_size: int,
    work_root: Path,
    destination_member: str,
    *,
    max_bytes: Optional[int] = None,  # noqa: UP045
) -> GitImportResult:
    """Verify and clone a bundle into an explicit empty mirror destination."""

    _max_bytes_value(max_bytes)
    if expected_size < 0:
        raise GitBundleError("expected_size must not be negative")
    _regular_directory(package_root, "package root")
    _regular_directory(work_root, "work root")
    _reject_member_symlinks(package_root, member)
    _reject_member_symlinks(work_root, destination_member)
    bundle_path = resolve_package_member(package_root, member)
    _regular_file(bundle_path, "bundle")
    destination = resolve_package_member(work_root, destination_member)
    if destination.exists() or destination.is_symlink():
        raise GitBundleError(f"import destination must not exist: {destination}")
    size, sha256 = _digest_and_size(bundle_path, max_bytes)
    if size != expected_size or sha256 != expected_sha256:
        raise GitBundleError("bundle integrity metadata does not match")
    with tempfile.TemporaryDirectory(prefix="gitferry-bundle-verify-") as verify_directory:
        verify_repository = Path(verify_directory) / "verify.git"
        subprocess.run(  # noqa: S603
            ["git", "init", "--bare", os.fspath(verify_repository)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            subprocess.run(  # noqa: S603
                ["git", "bundle", "verify", os.fspath(bundle_path)],  # noqa: S607
                cwd=verify_repository,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise GitBundleError("bundle is not a valid Git bundle") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(  # noqa: S603
            ["git", "clone", "--mirror", os.fspath(bundle_path), os.fspath(destination)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise GitBundleError("git bundle import failed") from exc
    return GitImportResult(
        member=member,
        bundle_path=bundle_path,
        destination=destination,
        size=size,
        sha256=sha256,
    )
