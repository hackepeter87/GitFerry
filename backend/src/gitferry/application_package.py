"""Portable first-import selections; no local approvals or execution state."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from gitferry.state_codec import canonical_json
from gitferry.transport import (
    MAX_MANIFEST_BYTES,
    ManifestValidationError,
    TransportArtifact,
    _parse,
    load_transport,
)

PACKAGE_NAME = "package.json"
DIGEST_NAME = "package.sha256"


class PackageError(ValueError):
    """The package cannot be safely received or used as a selection."""


@dataclass(frozen=True)
class PackageArtifact:
    directory: Path
    digest_sha256: str
    document: dict[str, Any]
    files: dict[str, tuple[int, str]]


def load_package_metadata(artifact: PackageArtifact) -> TransportArtifact:
    """Reload metadata only when the read bytes match the verified descriptor."""
    reference = artifact.document["metadata"]
    if reference is None:
        raise PackageError("package has no metadata selection")
    _file(artifact.directory, "metadata/manifest.json")
    _file(artifact.directory, "metadata/manifest.json.sha256")
    manifest = load_transport(artifact.directory / "metadata")
    if (
        manifest.digest_sha256 != reference["digest_sha256"]
        or manifest.document["manifest_id"] != reference["manifest_id"]
    ):
        raise PackageError("embedded metadata changed after package inspection")
    return manifest


def package_path(value: str) -> PurePosixPath:
    """Require a canonical portable relative name, without aliases or host paths."""
    parts = value.split("/")
    if (
        not value
        or len(value) > 256
        or any(part in ("", ".", "..") for part in parts)
        or any(character in value for character in ("\\", ":", "\x00"))
        or any(ord(character) < 32 for character in value)
    ):
        raise PackageError("invalid package member path")
    return PurePosixPath(value)


def _file(directory: Path, member: str) -> Path:
    relative = package_path(member)
    path = directory
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise PackageError("package members must not contain symbolic links")
    if not path.is_file():
        raise PackageError("package member is missing or not a regular file")
    return path


def _digest(path: Path, *, limit: int | None = None) -> tuple[int, str]:
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise PackageError("package member is not a regular file")
        if limit is not None and details.st_size > limit:
            raise PackageError("package member exceeds its size limit")
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if limit is not None and size > limit:
                raise PackageError("package member exceeds its size limit")
            digest.update(chunk)
    return size, digest.hexdigest()


def _read_bounded(path: Path, limit: int) -> bytes:
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise PackageError("package member is not a regular file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise PackageError("package member exceeds its size limit")
    return raw


def _schema(version: int) -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[3] / f"schemas/v{version}/application-package.schema.json"
    )
    if not path.is_file():
        path = Path.cwd() / f"schemas/v{version}/application-package.schema.json"
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


def _selector(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return value["realm_key"], value["login"], value["external_id"], value["ou_path"]


def _issue_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return value["instance_key"], value["repository"], value["global_id"]


def _validate(document: dict[str, Any]) -> None:
    version = document.get("schema_version")
    if type(version) is not int or version not in (1, 2, 3, 4):
        raise PackageError("unsupported application package version")
    errors = Draft202012Validator(_schema(version), format_checker=FormatChecker()).iter_errors(
        document
    )
    if next(errors, None) is not None:
        # Do not reflect untrusted values (possibly credentials) into a public report.
        raise PackageError("invalid or unsupported application package descriptor")
    if document["sender"]["instance_key"] == document["recipient"]["instance_key"]:
        raise PackageError("package sender and recipient must be different instances")
    realms = {item["realm_key"] for item in document["realms"]}
    if len(realms) != len(document["realms"]):
        raise PackageError("duplicate package realm")
    scopes: set[str] = set()
    repositories: set[tuple[str, str]] = set()
    organizations: dict[tuple[str, str], str] = {}
    organization_names: dict[str, tuple[str, str]] = {}
    teams: dict[tuple[str, str], str] = {}
    team_names: dict[tuple[str, str], tuple[str, str]] = {}
    members: set[str] = set()
    for repository in document["repositories"]:
        scope = repository["repository"]
        segments = scope.split("/")
        if len(segments) != 2 or any(
            not segment or segment in (".", "..") or "\\" in segment or ":" in segment
            for segment in segments
        ):
            raise PackageError("invalid repository scope")
        if scope in scopes:
            raise PackageError("duplicate selected repository")
        scopes.add(scope)
        identity = (repository["origin"]["instance_key"], repository["origin"]["global_id"])
        if identity in repositories:
            raise PackageError("one repository origin cannot select different copies")
        repositories.add(identity)
        organization = repository["organization"]
        if organization["name"] != segments[0]:
            raise PackageError("repository and organization scope disagree")
        org_key = (organization["origin"]["instance_key"], organization["origin"]["global_id"])
        if organizations.setdefault(org_key, organization["name"]) != organization["name"]:
            raise PackageError("organization origin has conflicting names")
        if organization_names.setdefault(organization["name"], org_key) != org_key:
            raise PackageError("organization name has conflicting origins")
        seen_teams: set[tuple[str, str]] = set()
        for team in repository["teams"]:
            key = (team["origin"]["instance_key"], team["origin"]["global_id"])
            description = canonical_json({"organization": organization, "team": team})
            if key in seen_teams or teams.setdefault(key, description) != description:
                raise PackageError("duplicate or conflicting team origin")
            seen_teams.add(key)
            name_key = (organization["name"], team["name"])
            if team_names.setdefault(name_key, key) != key:
                raise PackageError("team name has conflicting origins")
            selectors = [_selector(value) for value in team["members"]]
            if len(set(selectors)) != len(selectors):
                raise PackageError("duplicate team member claim")
            if any(value[0] not in realms for value in selectors):
                raise PackageError("team member references an undeclared realm")
        if repository["bundle"] is not None:
            member = repository["bundle"]["path"]
            package_path(member)
            if not member.startswith("bundles/") or member in members:
                raise PackageError("bundles need distinct paths below bundles/")
            members.add(member)
    from gitferry.package_native import validate_native_descriptors

    validate_native_descriptors(document)
    from gitferry.package_metadata import validate_metadata_descriptors

    validate_metadata_descriptors(document)
    if document["schema_version"] == 4 and any(
        item["kind"] == "release" and item["author"]["realm_key"] not in realms
        for item in document["content"]
    ):
        raise PackageError("release author references an undeclared realm")
    metadata = document["metadata"]
    if metadata:
        authors = metadata["issue_authors"]
        keys = [_issue_key(item["origin"]) for item in authors]
        if len(set(keys)) != len(keys):
            raise PackageError("duplicate issue author relation")
        if any(item["author"]["realm_key"] not in realms for item in authors):
            raise PackageError("issue author references an undeclared realm")


def _contents(directory: Path, document: dict[str, Any]) -> dict[str, tuple[int, str]]:
    from gitferry.package_inventory import validate_inventory_contents

    files: dict[str, tuple[int, str]] = {}
    if document["schema_version"] >= 2:
        reference = document["inventory"]
        files[reference["path"]] = (reference["size"], reference["sha256"])
    for repository in document["repositories"]:
        bundle = repository["bundle"]
        if bundle is not None:
            member = bundle["path"]
            facts = _digest(_file(directory, member), limit=bundle["size"])
            if facts != (bundle["size"], bundle["sha256"]):
                raise PackageError("bundle size or digest mismatch")
            files[member] = facts
    from gitferry.package_native import native_contents

    files.update(native_contents(directory, document))
    from gitferry.package_metadata import metadata_contents

    for member, facts in metadata_contents(directory, document).items():
        if member in files and files[member] != facts:
            raise PackageError("package payload path has conflicting facts")
        files[member] = facts
    reference = document["metadata"]
    if reference is None:
        validate_inventory_contents(directory, document, None)
        return files
    try:
        for name in ("manifest.json", "manifest.json.sha256"):
            member = "metadata/" + name
            files[member] = _digest(_file(directory, member), limit=MAX_MANIFEST_BYTES)
        manifest = load_transport(directory / "metadata")
    except (OSError, ManifestValidationError) as exc:
        raise PackageError("embedded metadata manifest validation failed") from exc
    data = manifest.document
    if (
        manifest.digest_sha256 != reference["digest_sha256"]
        or data["manifest_id"] != reference["manifest_id"]
        or data["transfer_round_id"] != document["transfer_round_id"]
        or data["mode"] != "forward"
        or data["sender"] != document["sender"]
        or any(data["recipient"][key] != value for key, value in document["recipient"].items())
    ):
        raise PackageError("metadata transfer identity or direction disagrees with package")
    scopes = {item["repository"] for item in document["repositories"]}
    if not {item["repository"] for item in data["selected_repositories"]}.issubset(scopes):
        raise PackageError("metadata selects a repository outside the package")
    if any(item["origin"]["repository"] not in scopes for item in data["objects"]):
        raise PackageError("metadata object origin is outside the selected repositories")
    authors = {_issue_key(item["origin"]): item["author"] for item in reference["issue_authors"]}
    issues = {
        _issue_key(item["origin"])
        for item in data["objects"]
        if item["origin"]["object_type"] == "issue"
    }
    if set(authors) != issues:
        raise PackageError("every included issue needs exactly one explicit author relation")
    realms = {item["realm_key"] for item in document["realms"]}
    for item in data["objects"]:
        if (
            item["origin"]["object_type"] == "issue_comment"
            and item["author"]["realm_key"] not in realms
        ):
            raise PackageError("comment author references an undeclared realm")
    validate_inventory_contents(directory, document, data)
    for payload in data["payloads"]:
        member = "metadata/" + payload["path"]
        package_path(member)
        if member in files:
            raise PackageError("metadata payload shadows a manifest file")
        files[member] = (payload["size"], payload["sha256"])
    return files


def inspect_package(directory: Path) -> PackageArtifact:
    """Validate immutable files and relationships, without network or state writes."""
    if directory.is_symlink() or not directory.is_dir():
        raise PackageError("package root must be a regular directory")
    path = _file(directory, PACKAGE_NAME)
    raw = _read_bounded(path, MAX_MANIFEST_BYTES)
    facts = (len(raw), hashlib.sha256(raw).hexdigest())
    digest_path = _file(directory, DIGEST_NAME)
    digest_raw = _read_bounded(digest_path, 65)
    digest_facts = (len(digest_raw), hashlib.sha256(digest_raw).hexdigest())
    try:
        if digest_raw != (facts[1] + "\n").encode("ascii"):
            raise PackageError("package byte digest mismatch")
        document = _parse(raw)
    except ManifestValidationError as exc:
        raise PackageError("package descriptor is not valid bounded JSON") from exc
    _validate(document)
    files = _contents(directory, document)
    files[PACKAGE_NAME] = facts
    files[DIGEST_NAME] = digest_facts
    actual: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise PackageError("package contains a symbolic link")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackageError("package contains a nonregular member")
        actual.add(path.relative_to(directory).as_posix())
    if actual != set(files):
        raise PackageError("package contains undeclared or missing files")
    return PackageArtifact(directory, facts[1], document, files)


def write_package(directory: Path, document: dict[str, Any]) -> PackageArtifact:
    """Seal an application-owned staging directory containing completed payloads."""
    _validate(document)
    if directory.is_symlink() or not directory.is_dir():
        raise PackageError("package staging root must be a regular directory")
    raw = (canonical_json(document) + "\n").encode("utf-8")
    if len(raw) > MAX_MANIFEST_BYTES:
        raise PackageError("package descriptor exceeds the size limit")
    # Existing packages are immutable, including when called with another package ID.
    if (directory / PACKAGE_NAME).exists() or (directory / DIGEST_NAME).exists():
        artifact = inspect_package(directory)
        if artifact.digest_sha256 != hashlib.sha256(raw).hexdigest():
            raise PackageError("existing package descriptor differs")
        return artifact
    _contents(directory, document)
    created: list[Path] = []
    try:
        for name, content in (
            (PACKAGE_NAME, raw),
            (DIGEST_NAME, (hashlib.sha256(raw).hexdigest() + "\n").encode("ascii")),
        ):
            path = directory / name
            with path.open("xb") as stream:
                created.append(path)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        return inspect_package(directory)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
