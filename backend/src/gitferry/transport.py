"""Portable, byte-integrity checked GitFerry transport manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})
MANIFEST_NAME = "manifest.json"
DIGEST_NAME = "manifest.json.sha256"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class ManifestValidationError(ValueError):
    """A transport is malformed, unsupported, or fails integrity checks."""


class ManifestConflict(ManifestValidationError):
    """An immutable manifest identity or portable fact conflicts."""


@dataclass(frozen=True)
class TransportArtifact:
    manifest_path: Path
    digest_path: Path
    digest_sha256: str
    document: dict[str, Any]


def _schema_path(version: int) -> Path:
    filename = (
        "transport-manifest.schema.json" if version == 1 else "transport-manifest-v2.schema.json"
    )
    candidates = (
        Path.cwd() / "schemas" / f"v{version}" / filename,
        Path(__file__).resolve().parents[3] / "schemas" / f"v{version}" / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("GitFerry transport manifest schema is unavailable")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestValidationError("manifest exceeds the 1 MiB input limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("manifest must be UTF-8 JSON") from exc
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"invalid JSON: {exc}") from exc
    except RecursionError as exc:
        raise ManifestValidationError("manifest exceeds the JSON nesting limit") from exc
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ManifestValidationError("manifest exceeds the JSON nesting limit")
        if isinstance(current, dict):
            pending.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            pending.extend((child, depth + 1) for child in current)
    if not isinstance(value, dict):
        raise ManifestValidationError("manifest root must be an object")
    return cast(dict[str, Any], value)


def _relative_payload_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ManifestValidationError(
            f"payload path must be a safe relative transport path: {value}"
        )
    if "\\" in value or ":" in path.parts[0]:
        raise ManifestValidationError(
            f"payload path must be a safe relative transport path: {value}"
        )
    return path


def validate_document(document: dict[str, Any]) -> None:
    if document.get("format") != "gitferry.transport":
        raise ManifestValidationError(f"unsupported transport format: {document.get('format')!r}")
    version = document.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestValidationError(f"unsupported transport schema version: {version!r}")
    schema = json.loads(_schema_path(cast(int, version)).read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise ManifestValidationError(f"{location}: {error.message}")

    repositories = [selection["repository"] for selection in document["selected_repositories"]]
    if len(repositories) != len(set(repositories)):
        raise ManifestValidationError("selected repository references must be unique")
    origin_keys: set[tuple[str, str, str, str]] = set()
    issue_origins: set[tuple[str, str, str, str]] = set()
    for item in document["objects"]:
        origin = item["origin"]
        key = (
            origin["instance_key"],
            origin["repository"],
            origin["object_type"],
            origin["global_id"],
        )
        if key in origin_keys:
            raise ManifestValidationError("object origin references must be unique")
        origin_keys.add(key)
        if origin["object_type"] == "issue":
            issue_origins.add(key)
        replica_instances = [replica["instance_key"] for replica in item["replicas"]]
        if len(replica_instances) != len(set(replica_instances)):
            raise ManifestValidationError("replica references must be unique per instance")
    if version == 2:
        for item in document["objects"]:
            origin = item["origin"]
            if origin["object_type"] != "issue_comment":
                continue
            parent = origin["parent_issue_origin"]
            parent_key = (
                parent["instance_key"],
                parent["repository"],
                parent["object_type"],
                parent["global_id"],
            )
            if parent_key not in issue_origins:
                # A locally known parent is permitted during admission. Structural validation
                # only rejects malformed/self-typed references; it does not require package order.
                continue
    paths: set[str] = set()
    for payload in document["payloads"]:
        normalized = str(_relative_payload_path(payload["path"]))
        if normalized in paths:
            raise ManifestValidationError("payload paths must be unique")
        paths.add(normalized)


def _serialize(document: dict[str, Any]) -> bytes:
    validate_document(document)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (encoded + "\n").encode("utf-8")


def read_document(path: Path) -> dict[str, Any]:
    """Read and validate a bounded JSON draft before immutable export."""

    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestValidationError("manifest exceeds the 1 MiB input limit")
    document = _parse(path.read_bytes())
    validate_document(document)
    return document


def _atomic_create(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_transport(directory: Path, document: dict[str, Any]) -> TransportArtifact:
    """Write one immutable manifest and sidecar digest, or accept an exact replay."""

    raw = _serialize(document)
    digest = hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / MANIFEST_NAME
    digest_path = directory / DIGEST_NAME
    if manifest_path.exists() or digest_path.exists():
        try:
            existing = load_transport(directory)
        except (OSError, ManifestValidationError) as exc:
            raise ManifestConflict(
                f"immutable transport already exists and differs: {exc}"
            ) from exc
        if (
            existing.digest_sha256 != digest
            or existing.document["manifest_id"] != document["manifest_id"]
        ):
            raise ManifestConflict(
                "immutable transport already exists with different bytes or identity"
            )
        return existing
    _atomic_create(manifest_path, raw)
    try:
        _atomic_create(digest_path, (digest + "\n").encode("ascii"))
    except BaseException:
        manifest_path.unlink(missing_ok=True)
        raise
    return TransportArtifact(manifest_path, digest_path, digest, document)


def load_transport(directory: Path) -> TransportArtifact:
    """Read, byte-check, parse, and locally validate one transport directory."""

    manifest_path = directory / MANIFEST_NAME
    digest_path = directory / DIGEST_NAME
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestValidationError("manifest exceeds the 1 MiB input limit")
    raw = manifest_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    expected = digest_path.read_text(encoding="ascii").strip()
    if not _DIGEST.fullmatch(expected):
        raise ManifestValidationError("manifest digest sidecar is not a SHA-256 value")
    if actual != expected:
        raise ManifestValidationError("manifest byte digest mismatch")
    document = _parse(raw)
    validate_document(document)
    root = directory.resolve()
    for payload in document["payloads"]:
        relative = _relative_payload_path(payload["path"])
        path = directory.joinpath(*relative.parts)
        if any(
            directory.joinpath(*relative.parts[:index]).is_symlink()
            for index in range(1, len(relative.parts) + 1)
        ):
            raise ManifestValidationError(f"payload path contains a symbolic link: {relative}")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise ManifestValidationError(
                f"payload is missing or escapes the transport directory: {relative}"
            ) from exc
        if not resolved.is_file():
            raise ManifestValidationError(f"payload is missing or not a regular file: {relative}")
        if resolved.stat().st_size != payload["size"]:
            raise ManifestValidationError(f"payload size mismatch: {relative}")
        if _file_sha256(resolved) != payload["sha256"]:
            raise ManifestValidationError(f"payload digest mismatch: {relative}")
    return TransportArtifact(manifest_path, digest_path, actual, document)
