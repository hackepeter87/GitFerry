"""Invoke the repository CLI with the current source tree for browser tests."""

from __future__ import annotations

import sys

from gitferry.cli import main

raise SystemExit(main(sys.argv[1:]))
