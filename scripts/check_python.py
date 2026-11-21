"""Reject unsupported interpreters before creating or modifying a development environment."""

import re
import sys
from pathlib import Path


def minimum_version():
    metadata = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    match = re.search(r'^requires-python = ">=(\d+)\.(\d+)"$', metadata, re.MULTILINE)
    if match is None:
        raise ValueError("unsupported Python requirement format")
    return tuple(int(value) for value in match.groups())


def main(version=None):
    actual = sys.version_info[:2] if version is None else version
    minimum = minimum_version()
    if actual < minimum:
        print(
            f"GitFerry requires Python {minimum[0]}.{minimum[1]}+; "
            f"selected {actual[0]}.{actual[1]}. "
            "Use make setup PYTHON_BOOTSTRAP=/path/to/supported/python.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
