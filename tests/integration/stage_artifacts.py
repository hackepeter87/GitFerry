#!/usr/bin/env python3
"""Stage a small, secret-free allowlist of integration diagnostics for CI."""

import argparse
import json
import re
import shutil
import stat
from pathlib import Path
from typing import Any

_REDACTIONS = (
    (
        re.compile(
            r"(?im)^(\s*(?:-\s*)?(?:authorization|proxy-authorization|cookie|set-cookie|"
            r"x-[^:\n]*token)\s*:\s*).*$"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"(?im)(\b[\w.-]*(?:password|passwd|secret|token|session|cookie|csrf|authorization)"
            r"[\w.-]*\b\s*[=:]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"),
        "[REDACTED PRIVATE KEY]",
    ),
)

_ROOT_FILES = {"project-name", "runtime-image", "report.json", "workflow-cleanup.json"}
_DIAGNOSTIC_FILES = {"compose-ps.json", "compose-images.json", "compose.log"}
_JSON_FILES = {"report.json", "workflow-cleanup.json", "run-metadata.json"}
_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|secret|token|session|cookie|csrf|authorization|private[_-]?key)", re.I
)


def _safe_text(value: str) -> str:
    for pattern, replacement in _REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def _copy_text(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _safe_text(source.read_text(encoding="utf-8", errors="replace")), encoding="utf-8"
    )


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if isinstance(item, (str, list, dict)) and _SENSITIVE_KEY.search(str(key))
                else _sanitize_json(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _copy_json(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    value = json.loads(source.read_text(encoding="utf-8"))
    target.write_text(json.dumps(_sanitize_json(value), indent=2, sort_keys=True) + "\n")


def _copy_allowed(source: Path, target: Path, result_root: Path) -> bool:
    if not source.exists():
        return False
    if source.is_symlink() or stat.S_ISLNK(source.lstat().st_mode):
        raise SystemExit(f"refusing symbolic link in integration diagnostics: {source.name}")
    if source.stat().st_nlink != 1:
        raise SystemExit(f"refusing hard-linked integration diagnostic: {source.name}")
    try:
        source.resolve().relative_to(result_root.resolve())
    except ValueError as error:
        raise SystemExit("integration diagnostic escaped its result directory") from error
    if not source.is_file():
        return False
    if source.name in _JSON_FILES:
        _copy_json(source, target)
    else:
        _copy_text(source, target)
    return True


def stage(results_root: Path, output: Path) -> int:
    results_root = results_root.resolve()
    if output.name != "integration-artifacts" or output.parent.resolve() != results_root:
        raise SystemExit("artifact output must be the integration-artifacts child of results root")
    if output.is_symlink():
        raise SystemExit("refusing symbolic artifact output directory")
    sources = [
        results_root / "integration-results",
        results_root / "integration-failure-results",
    ]
    if not any(source.is_dir() for source in sources):
        raise SystemExit("no integration result directory available for artifact staging")
    if output.exists():
        if not output.is_dir():
            raise SystemExit("artifact output exists and is not a directory")
        shutil.rmtree(output)
    output.mkdir(parents=True)
    copied = 0
    for source in sources:
        if not source.is_dir():
            continue
        if source.is_symlink():
            raise SystemExit("refusing symbolic integration result directory")
        destination = output / source.name
        for name in _ROOT_FILES:
            path = source / name
            if _copy_allowed(path, destination / name, source):
                copied += 1
        for name in _DIAGNOSTIC_FILES:
            path = source / "workflow-diagnostics" / name
            if _copy_allowed(path, destination / "workflow-diagnostics" / name, source):
                copied += 1
        work = source / "work"
        if work.is_dir():
            if work.is_symlink():
                raise SystemExit("refusing symbolic integration work directory")
            for path in work.glob("browser-*.log"):
                if _copy_allowed(path, destination / "work" / path.name, source):
                    copied += 1
            for name in ("contract-gate.log", "run-metadata.json"):
                path = work / name
                if _copy_allowed(path, destination / "work" / name, source):
                    copied += 1
    if copied == 0:
        raise SystemExit("integration results contained no allowlisted diagnostics")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    copied = stage(args.results_root, args.output)
    print(f"staged {copied} integration diagnostic files")


if __name__ == "__main__":
    main()
