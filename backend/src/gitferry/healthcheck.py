"""Dependency-free readiness probe for the OCI image."""

from __future__ import annotations

import json
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> int:
    try:
        with urlopen("http://127.0.0.1:8080/readyz", timeout=2) as response:  # noqa: S310
            payload: Any = json.load(response)
            healthy = (
                response.status == 200
                and isinstance(payload, dict)
                and payload.get("status") == "ready"
            )
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return 1
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
