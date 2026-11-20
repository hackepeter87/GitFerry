"""Production ASGI server entrypoint with safe request logging."""

from __future__ import annotations

import os

import uvicorn


def _port_from_environment() -> int:
    raw_port = os.environ.get("GITFERRY_PORT", "8080")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit("GITFERRY_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("GITFERRY_PORT must be between 1 and 65535")
    return port


def main() -> None:
    uvicorn.run(
        "gitferry.api:app",
        host=os.environ.get("GITFERRY_HOST", "0.0.0.0"),  # noqa: S104 - OCI service bind
        port=_port_from_environment(),
        lifespan="on",
        timeout_graceful_shutdown=30,
        access_log=False,
    )


if __name__ == "__main__":
    main()
