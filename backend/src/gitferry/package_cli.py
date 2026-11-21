"""File-only package inspection and transport; receipt grants no execution rights."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gitferry.application_package import PackageArtifact, PackageError, inspect_package
from gitferry.package_archive import receive_package_archive, write_package_archive


def configure(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="package_action", required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("path", type=Path)
    pack = actions.add_parser("pack")
    pack.add_argument("path", type=Path)
    pack.add_argument("--output", type=Path, required=True)
    receive = actions.add_parser("receive")
    receive.add_argument("path", type=Path)
    receive.add_argument("--output", type=Path, required=True)
    receive.add_argument("--max-bytes", type=int, default=64 * 1024**3)


def report(artifact: PackageArtifact) -> dict[str, object]:
    document = artifact.document
    return {
        "status": "validated-files-only",
        "package_id": document["package_id"],
        "digest_sha256": artifact.digest_sha256,
        "sender": document["sender"],
        "recipient": document["recipient"],
        "repositories": [item["repository"] for item in document["repositories"]],
        "files": len(artifact.files),
        "bytes": sum(size for size, _ in artifact.files.values()),
        "inventory_complete": document["inventory_complete"],
        "limitations": document["limitations"],
        "execution_authorized": False,
    }


def execute(args: argparse.Namespace) -> int:
    try:
        if args.package_action == "receive":
            artifact = receive_package_archive(args.path, args.output, max_bytes=args.max_bytes)
        else:
            artifact = inspect_package(args.path)
            if args.package_action == "pack":
                write_package_archive(args.path, args.output)
        print(json.dumps(report(artifact), sort_keys=True))
        return 0
    except (OSError, PackageError):
        print(
            json.dumps({"status": "failed", "error": "Package validation or file transfer failed"})
        )
        return 2
