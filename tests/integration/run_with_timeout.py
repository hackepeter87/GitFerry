"""Run one command with a portable bounded timeout."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: run_with_timeout.py SECONDS COMMAND [ARG ...]")
    seconds = float(sys.argv[1])
    try:
        return subprocess.run(sys.argv[2:], check=False, timeout=seconds).returncode
    except subprocess.TimeoutExpired:
        print(f"integration probe exceeded {seconds:.0f}s", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
