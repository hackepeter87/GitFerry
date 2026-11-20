from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def _unused_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output, _ = process.communicate()
            raise AssertionError(f"GitFerry server exited before readiness:\n{output}")
        try:
            with urlopen(f"{base_url}/healthz", timeout=0.5) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError):
            time.sleep(0.05)
    raise AssertionError("GitFerry server did not become ready within 10 seconds")


def test_production_server_logs_only_allowlisted_request_metadata(tmp_path: Path) -> None:
    port = _unused_local_port()
    base_url = f"http://127.0.0.1:{port}"
    raw_path_marker = "never-log-this-synthetic-token"
    query_marker = "never-log-this-synthetic-query-secret"
    environment = os.environ.copy()
    environment.update(
        {
            "GITFERRY_HOST": "127.0.0.1",
            "GITFERRY_PORT": str(port),
            "GITFERRY_RUNTIME_MANAGED": "0",
            "GITFERRY_STATE_PATH": str(tmp_path / "state.sqlite3"),
        }
    )
    process = subprocess.Popen(  # noqa: S603 - fixed local test command
        [sys.executable, "-m", "gitferry.server"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_until_ready(base_url, process)
        try:
            urlopen(  # noqa: S310 - loopback regression probe
                f"{base_url}/{raw_path_marker}?session={query_marker}", timeout=2
            )
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("unmatched request unexpectedly succeeded")
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)

    assert "http_request method=GET route=<unmatched> status=404" in output
    assert raw_path_marker not in output
    assert query_marker not in output
