"""Bounded discovery of standard LFS pointers reachable from selected Git refs."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

from .lfs_payloads import LFSPayloadError, LFSPointer, parse_lfs_pointer

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def inventory_lfs_pointers(
    repository: Path, *, max_objects: int = 200000, max_pointers: int = 10000, timeout: float = 120
) -> tuple[LFSPointer, ...]:
    """Inspect all reachable historical blobs; never run filters or consult LFS URLs.

    Only the supplied mirror's refs define scope. Unreachable Git objects are not
    included. Git LFS's pointer-size bound is 1024 bytes; larger ordinary blobs
    are not loaded. Extensions and malformed recognized pointers fail closed.
    """
    if repository.is_symlink() or not repository.is_dir():
        raise LFSPayloadError("LFS inventory requires an owned Git directory")
    if max_objects <= 0 or max_pointers <= 0 or timeout <= 0:
        raise LFSPayloadError("LFS inventory bounds must be positive")
    deadline = time.monotonic() + timeout
    env = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    command = ["git", "-c", "core.hooksPath=/dev/null", "--no-optional-locks"]
    objects: list[str] = []
    process = subprocess.Popen(  # noqa: S603
        [*command, "rev-list", "--objects", "--all", "--no-object-names"],
        cwd=repository,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    timer = threading.Timer(timeout, process.kill)
    timer.daemon = True
    timer.start()
    try:
        output = process.stdout
        if output is None:
            raise LFSPayloadError("Git output pipe is unavailable")
        for raw in iter(lambda: output.readline(128), b""):
            identifier = raw.rstrip(b"\n").decode("ascii", errors="strict")
            if not _OID.fullmatch(identifier) or len(objects) >= max_objects:
                raise LFSPayloadError("reachable Git objects exceed the inventory contract")
            objects.append(identifier)
        if process.wait() != 0 or time.monotonic() > deadline:
            raise LFSPayloadError("Git object enumeration failed or timed out")
    except (UnicodeError, OSError):
        raise LFSPayloadError("Git object enumeration was invalid") from None
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
    if len(set(objects)) != len(objects):
        raise LFSPayloadError("Git object enumeration repeated an identity")
    pointers: dict[str, LFSPointer] = {}
    for offset in range(0, len(objects), 512):
        selected = objects[offset : offset + 512]
        try:
            check = subprocess.run(  # noqa: S603
                [*command, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                input=("\n".join(selected) + "\n").encode(),
                cwd=repository,
                env=env,
                capture_output=True,
                check=True,
                timeout=max(0.001, deadline - time.monotonic()),
            ).stdout
            rows = check.decode("ascii").splitlines()
            if len(rows) != len(selected):
                raise LFSPayloadError("Git object metadata is incomplete")
            small_blobs: list[str] = []
            sizes: dict[str, int] = {}
            for expected, row in zip(selected, rows, strict=True):
                fields = row.split(" ")
                if len(fields) != 3 or fields[0] != expected or not fields[2].isdigit():
                    raise LFSPayloadError("Git object metadata is malformed")
                if fields[1] == "blob" and int(fields[2]) <= 1024:
                    small_blobs.append(expected)
                    sizes[expected] = int(fields[2])
            if not small_blobs:
                continue
            contents = subprocess.run(  # noqa: S603
                [*command, "cat-file", "--batch"],
                input=("\n".join(small_blobs) + "\n").encode(),
                cwd=repository,
                env=env,
                capture_output=True,
                check=True,
                timeout=max(0.001, deadline - time.monotonic()),
            ).stdout
            cursor = 0
            for expected in small_blobs:
                end = contents.find(b"\n", cursor)
                if (
                    end < cursor
                    or contents[cursor:end] != f"{expected} blob {sizes[expected]}".encode()
                ):
                    raise LFSPayloadError("Git blob identity changed during inventory")
                cursor = end + 1
                body = contents[cursor : cursor + sizes[expected]]
                cursor += sizes[expected]
                if len(body) != sizes[expected] or contents[cursor : cursor + 1] != b"\n":
                    raise LFSPayloadError("Git blob content is truncated")
                cursor += 1
                pointer = parse_lfs_pointer(body)
                if pointer is not None:
                    if pointer.oid in pointers and pointers[pointer.oid] != pointer:
                        raise LFSPayloadError("LFS object has contradictory sizes")
                    pointers[pointer.oid] = pointer
                    if len(pointers) > max_pointers:
                        raise LFSPayloadError("LFS pointer count exceeds the configured bound")
            if cursor != len(contents):
                raise LFSPayloadError("Git blob response has unexpected trailing content")
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise LFSPayloadError("Git pointer enumeration failed or timed out") from None
    if time.monotonic() > deadline:
        raise LFSPayloadError("LFS inventory exceeded its deadline")
    return tuple(pointers[key] for key in sorted(pointers))
