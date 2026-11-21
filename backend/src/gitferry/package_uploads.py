"""Durable file receipt; validating a package grants no target authority."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from gitferry.application_package import PackageArtifact, PackageError, _digest, inspect_package
from gitferry.connection_profiles import _identifier
from gitferry.operations import OperationExecutor
from gitferry.package_archive import _publish_file, _sync_directory, receive_package_archive
from gitferry.runtime import RuntimePaths
from gitferry.state_codec import canonical_json, now
from gitferry.state_store import StateStore


class UploadConflict(PackageError):
    """Receipt identity or locally confirmed bytes disagree."""


class UploadLimit(PackageError):
    """The declared or received upload exceeds its configured bound."""


class PackageUploads:
    def __init__(self, state: StateStore, paths: RuntimePaths, *, max_bytes: int | None = None):
        self.state, self.paths = state, paths
        self.max_bytes = (
            max_bytes
            if max_bytes is not None
            else int(os.environ.get("GITFERRY_UPLOAD_MAX_BYTES", str(64 * 1024**3)))
        )
        if type(self.max_bytes) is not int or not 0 < self.max_bytes <= 64 * 1024**3:
            raise UploadLimit("upload limit must be positive and at most 64 GiB")

    def get(self, upload_id: str, *, summary: bool = True) -> dict[str, Any]:
        _identifier(upload_id)
        with self.state.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM package_uploads WHERE upload_id=?", (upload_id,)
            ).fetchone()
        if row is None:
            raise KeyError("upload not found")
        result = dict(row)
        raw = result.pop("summary_json")
        result["summary"] = json.loads(raw) if raw and summary else None
        result["package_ref"] = upload_id if result["status"] == "ready" else None
        return result

    def list(self) -> list[dict[str, Any]]:
        with self.state.read_connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT upload_id FROM package_uploads "
                    "ORDER BY created_at DESC,upload_id LIMIT 100"
                )
            ]
        return [self.get(identifier, summary=False) for identifier in ids]

    def _directory(self, upload_id: str) -> Path:
        _identifier(upload_id)
        root = self.paths.work_directory
        if root.is_symlink() or not root.is_dir():
            raise UploadConflict("upload storage is unavailable")
        for name in (".uploads", upload_id):
            child = root / name
            if child.is_symlink() or (child.exists() and not child.is_dir()):
                raise UploadConflict("upload storage is not an owned directory")
            if not child.exists():
                child.mkdir(mode=0o700)
                _sync_directory(root)
            root = child
        return root

    def _failed(self, upload_id: str, code: str) -> None:
        with self.state.transaction() as connection:
            connection.execute(
                "UPDATE package_uploads SET status='failed',error_code=?,updated_at=? "
                "WHERE upload_id=? AND status!='ready'",
                (code, now(), upload_id),
            )

    @contextmanager
    def receiving(self, upload_id: str, archive_size: int) -> Iterator[UploadSink]:
        _identifier(upload_id)
        if type(archive_size) is not int or not 0 < archive_size <= self.max_bytes:
            raise UploadLimit("archive size exceeds upload limit")
        # One active application executor, including CLI, remains the supported
        # scope. HTTP body/disk work holds the file lock, never a SQLite transaction.
        with OperationExecutor(self.state, "upload-" + uuid.uuid4().hex):
            with self.state.transaction() as connection:
                row = connection.execute(
                    "SELECT archive_size FROM package_uploads WHERE upload_id=?", (upload_id,)
                ).fetchone()
                if row is not None and row[0] != archive_size:
                    raise UploadConflict("upload ID already has a different declared size")
                if row is None:
                    timestamp = now()
                    connection.execute(
                        "INSERT INTO package_uploads VALUES "
                        "(?,?,'receiving',NULL,NULL,NULL,NULL,?,?)",
                        (upload_id, archive_size, timestamp, timestamp),
                    )
            directory = self._directory(upload_id)
            staging = directory / "incoming.part"
            if staging.is_symlink() or (staging.exists() and not staging.is_file()):
                raise UploadConflict("upload staging is not a regular file")
            staging.unlink(missing_ok=True)
            descriptor = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                sink = UploadSink(self, upload_id, archive_size, staging, stream)
                try:
                    yield sink
                except BaseException:
                    self._failed(upload_id, "upload-not-confirmed")
                    raise
                finally:
                    staging.unlink(missing_ok=True)
            if not sink.finished:
                self._failed(upload_id, "upload-incomplete")

    def resume(
        self, upload_id: str, *, after_publish_hook: Callable[[], None] | None = None
    ) -> dict[str, Any]:
        with OperationExecutor(self.state, "upload-resume-" + uuid.uuid4().hex):
            return self._validate(upload_id, after_publish_hook=after_publish_hook)

    def _validate(
        self, upload_id: str, *, after_publish_hook: Callable[[], None] | None = None
    ) -> dict[str, Any]:
        job = self.get(upload_id)
        directory = self._directory(upload_id)
        archive = directory / "archive.zip"
        try:
            if job["archive_sha256"] is None:
                raise UploadConflict("upload incomplete; select the file and retry")
            if not archive.exists() and not archive.is_symlink():
                staging = directory / "incoming.part"
                if _digest(staging, limit=job["archive_size"]) != (
                    job["archive_size"],
                    job["archive_sha256"],
                ):
                    raise UploadConflict("upload candidate changed")
                _publish_file(staging, archive)
            if _digest(archive, limit=job["archive_size"]) != (
                job["archive_size"],
                job["archive_sha256"],
            ):
                raise UploadConflict("confirmed upload bytes changed")
            root = self.paths.package_directory
            if root.is_symlink() or not root.is_dir():
                raise UploadConflict("package storage is unavailable")
            destination = root / upload_id
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or job["package_digest"] is None:
                    raise UploadConflict("unproven destination cannot be adopted")
                artifact = inspect_package(destination)
                if artifact.digest_sha256 != job["package_digest"]:
                    raise UploadConflict("published package changed")
            else:

                def remember(artifact: PackageArtifact) -> None:
                    with self.state.transaction() as connection:
                        connection.execute(
                            "UPDATE package_uploads SET package_digest=?,updated_at=? "
                            "WHERE upload_id=?",
                            (artifact.digest_sha256, now(), upload_id),
                        )

                artifact = receive_package_archive(
                    archive,
                    destination,
                    max_bytes=self.max_bytes,
                    before_publish=remember,
                )
            if after_publish_hook is not None:
                after_publish_hook()
            document = artifact.document
            summary = {
                name: document[name]
                for name in ("package_id", "sender", "recipient", "limitations")
            }
            summary.update(
                repositories=[item["repository"] for item in document["repositories"]],
                metadata_present=document.get("metadata") is not None,
                inventory_complete=False,
            )
            with self.state.transaction() as connection:
                connection.execute(
                    "UPDATE package_uploads SET status='ready',summary_json=?,"
                    "error_code=NULL,updated_at=? "
                    "WHERE upload_id=?",
                    (canonical_json(summary), now(), upload_id),
                )
        except Exception:
            self._failed(upload_id, "package-validation-not-confirmed")
            raise
        return self.get(upload_id)


class UploadSink:
    def __init__(
        self, service: PackageUploads, upload_id: str, size: int, path: Path, stream: BinaryIO
    ):
        self.service, self.upload_id, self.size = service, upload_id, size
        self.path, self.stream = path, stream
        self.hash = hashlib.sha256()
        self.received = 0
        self.finished = False

    def write(self, chunk: bytes) -> None:
        if self.finished or self.received + len(chunk) > self.size:
            raise UploadLimit("received bytes exceed declared size")
        self.stream.write(chunk)
        self.hash.update(chunk)
        self.received += len(chunk)

    def finish(
        self,
        *,
        after_archive_hook: Callable[[], None] | None = None,
        after_publish_hook: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if self.finished or self.received != self.size:
            raise UploadConflict("upload is incomplete")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        digest = self.hash.hexdigest()
        current = self.service.get(self.upload_id)
        if current["archive_sha256"] not in (None, digest):
            raise UploadConflict("upload ID already identifies different bytes")
        archive = self.path.with_name("archive.zip")
        if archive.exists() or archive.is_symlink():
            if _digest(archive, limit=self.size) != (self.size, digest):
                raise UploadConflict("saved archive disagrees with receipt")
        else:
            with self.service.state.transaction() as connection:
                connection.execute(
                    "UPDATE package_uploads SET status='validating',archive_sha256=?,"
                    "error_code=NULL,"
                    "updated_at=? WHERE upload_id=?",
                    (digest, now(), self.upload_id),
                )
            _publish_file(self.path, archive)
        if after_archive_hook is not None:
            after_archive_hook()
        result = self.service._validate(self.upload_id, after_publish_hook=after_publish_hook)
        self.finished = True
        return result
