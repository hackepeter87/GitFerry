"""Canonical encoding and validation helpers for persisted control state."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from gitferry.state_models import StateStoreError


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def origin_id(instance_key: str, repository_scope: str, object_type: str, global_id: str) -> str:
    """Return the stable identity used by both local and transported origins."""

    identity = canonical_json(
        {
            "instance": instance_key,
            "repository": repository_scope,
            "type": object_type,
            "global_id": global_id,
        }
    )
    return "origin-" + hashlib.sha256(identity.encode()).hexdigest()


def decode_object(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise StateStoreError("stored JSON value is not an object")
    return cast(dict[str, Any], decoded)


def require_text(value: str, field: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty text without surrounding whitespace")


def find_secret_field(value: Any, path: str = "intent") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized.endswith("_ref"):
                continue
            if any(
                marker in normalized
                for marker in ("token", "password", "authorization", "private_key", "secret")
            ):
                return f"{path}.{key}"
            found = find_secret_field(child, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            found = find_secret_field(child, f"{path}[{index}]")
            if found:
                return found
    return None
