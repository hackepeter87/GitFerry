"""Typed native Git ref facts, independent of package and execution state."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def valid_ref(value: str) -> bool:
    return (
        value.startswith("refs/")
        and not value.endswith(("/", "."))
        and not any(mark in value for mark in ("..", "@{", "//"))
        and not any(ord(c) <= 32 or ord(c) == 127 or c in "~^:?*[\\" for c in value)
        and all(
            part and not part.startswith(".") and not part.endswith(".lock")
            for part in value.split("/")
        )
    )


def is_ref_map(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and len(value) <= 10000
        and all(
            isinstance(ref, str) and valid_ref(ref) and isinstance(sha, str) and _SHA.fullmatch(sha)
            for ref, sha in value.items()
        )
    )


def ref_confirmation(refs: dict[str, str]) -> dict[str, Any]:
    if not is_ref_map(refs):
        raise ValueError("invalid native ref confirmation")
    # Full comparison facts remain immutable in the operation intent. Confirmation
    # binds the observed exact set without exceeding the journal's evidence bound.
    encoded = json.dumps(refs, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"ref_count": len(refs), "refs_sha256": hashlib.sha256(encoded).hexdigest()}
