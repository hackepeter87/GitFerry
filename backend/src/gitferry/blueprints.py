"""Fail-closed loading and validation for versioned GitFerry blueprints."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

MAX_BLUEPRINT_BYTES = 1024 * 1024
SCHEMA_BY_KIND = {
    "InstanceBlueprint": "instance-blueprint.schema.json",
    "TransferBlueprint": "transfer-blueprint.schema.json",
}


class BlueprintValidationError(ValueError):
    """A blueprint is malformed, unsupported, or violates its schema."""


def _schema_directory() -> Path:
    candidates = [
        Path.cwd() / "schemas" / "v1",
        Path(__file__).resolve().parents[3] / "schemas" / "v1",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise RuntimeError("GitFerry schema directory is unavailable")


def _parse_yaml(content: str) -> Mapping[str, Any]:
    if len(content.encode("utf-8")) > MAX_BLUEPRINT_BYTES:
        raise BlueprintValidationError("blueprint exceeds the 1 MiB input limit")
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise BlueprintValidationError(f"invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise BlueprintValidationError("blueprint root must be a mapping")
    return document


def validate_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one parsed blueprint and return a detached plain dictionary."""

    version = document.get("apiVersion")
    if version != "gitferry.io/v1":
        raise BlueprintValidationError(f"unsupported apiVersion: {version!r}")
    kind = document.get("kind")
    if kind not in SCHEMA_BY_KIND:
        raise BlueprintValidationError(f"unsupported kind: {kind!r}")

    schema_path = _schema_directory() / SCHEMA_BY_KIND[str(kind)]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise BlueprintValidationError(f"{location}: {error.message}")
    return dict(document)


def validate_text(content: str) -> dict[str, Any]:
    """Parse YAML or JSON text safely and validate the versioned contract."""

    return validate_document(_parse_yaml(content))


def load_and_validate(path: Path) -> dict[str, Any]:
    """Read at most one bounded blueprint file and validate it."""

    if path.stat().st_size > MAX_BLUEPRINT_BYTES:
        raise BlueprintValidationError("blueprint exceeds the 1 MiB input limit")
    return validate_text(path.read_text(encoding="utf-8"))
