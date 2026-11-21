"""Durable source-export commands and immutable, streamed download artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from gitferry.application_package import _digest
from gitferry.connection_probe import LocalReferences
from gitferry.operations import OperationExecutor
from gitferry.package_archive import _publish_file, write_package_archive
from gitferry.runtime import RuntimePaths
from gitferry.source_exports import SourceExportConflict, SourceExports
from gitferry.state_codec import now
from gitferry.state_store import StateStore


class SourceExportJobs:
    def __init__(
        self, state: StateStore, paths: RuntimePaths, references: LocalReferences | None = None
    ):
        self.state, self.paths = state, paths
        self.exports = SourceExports(state, paths, references)

    def get(self, export_id: str) -> dict[str, Any]:
        capture = self.exports.get(export_id)
        with self.state.read_connection() as connection:
            delivery = connection.execute(
                "SELECT * FROM source_export_deliveries WHERE export_id=?", (export_id,)
            ).fetchone()
        return {
            **capture,
            "capture_status": capture["status"],
            "status": delivery["status"] if delivery else "prepared",
            "archive_sha256": delivery["archive_sha256"] if delivery else None,
            "archive_size": delivery["archive_size"] if delivery else None,
            "error_code": delivery["error_code"] if delivery else capture["error_code"],
            "updated_at": delivery["updated_at"] if delivery else capture["updated_at"],
        }

    def recipients(self) -> list[dict[str, Any]]:
        with self.state.read_connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT instance_key,display_name,installation_marker FROM instances "
                    "ORDER BY display_name,instance_key"
                )
            ]

    def list(self) -> list[dict[str, Any]]:
        return [self.get(item["export_id"]) for item in self.exports.list()]

    def create(self, export_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        # A crash between prepare and queue leaves visible unqueued intent, never
        # an implicitly authorized background task. Repeating this ID is safe.
        self.exports.prepare(request, export_id=export_id)
        return self.queue(export_id)

    def queue(self, export_id: str) -> dict[str, Any]:
        capture = self.exports.get(export_id)
        if capture["status"] != "captured":
            self.exports._check_local(export_id)
        with self.state.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM source_export_deliveries WHERE export_id=?", (export_id,)
            ).fetchone()
            if row is None:
                timestamp = now()
                connection.execute(
                    "INSERT INTO source_export_deliveries VALUES "
                    "(?,'queued',NULL,NULL,NULL,NULL,?,?)",
                    (export_id, timestamp, timestamp),
                )
            elif row["status"] == "failed":
                connection.execute(
                    "UPDATE source_export_deliveries SET status='queued',error_code=NULL,"
                    "updated_at=? WHERE export_id=?",
                    (now(), export_id),
                )
        return self.get(export_id)

    def _directory(self, export_id: str, *, create: bool) -> Path:
        # Dot-prefixed namespace cannot collide with a CLI export identifier.
        root = self.paths.work_directory
        self.exports._root(root)
        for part in (".downloads", export_id):
            root = root / part
            if root.is_symlink() or (root.exists() and not root.is_dir()):
                raise SourceExportConflict("download storage is not an owned directory")
            if create:
                root.mkdir(mode=0o700, exist_ok=True)
        return root

    def run_next(
        self, *, after_publish_hook: Callable[[], None] | None = None
    ) -> dict[str, Any] | None:
        # The same lock excludes CLI capture and Gitea writes throughout capture,
        # packaging and confirmation. There is no long SQLite write transaction.
        with self.state.read_connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM source_export_deliveries "
                    "WHERE status IN ('queued','running') LIMIT 1"
                ).fetchone()
                is None
            ):
                return None
        with OperationExecutor(self.state, "export-job-" + uuid.uuid4().hex):
            with self.state.transaction() as connection:
                row = connection.execute(
                    "SELECT export_id FROM source_export_deliveries "
                    "WHERE status IN ('queued','running') ORDER BY created_at,export_id LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                export_id = str(row[0])
                connection.execute(
                    "UPDATE source_export_deliveries SET status='running',error_code=NULL,"
                    "updated_at=? WHERE export_id=?",
                    (now(), export_id),
                )
            try:
                self.exports._run_owned(export_id)
                directory = self._directory(export_id, create=True)
                archive = directory / "package.zip"
                with self.state.read_connection() as connection:
                    candidate = connection.execute(
                        "SELECT candidate_digest,archive_size FROM source_export_deliveries "
                        "WHERE export_id=?",
                        (export_id,),
                    ).fetchone()
                if archive.exists() or archive.is_symlink():
                    if candidate[0] is None or candidate[1] is None:
                        raise SourceExportConflict("published archive lacks local evidence")
                    size, digest = _digest(archive, limit=candidate[1])
                    if (digest, size) != (candidate[0], candidate[1]):
                        raise SourceExportConflict(
                            "published archive lacks matching local evidence"
                        )
                else:
                    staging = directory / "candidate.zip"
                    # Only fixed, application-owned staging paths are disposable.
                    for leftover in [staging, *directory.glob(".candidate.zip.*.part")]:
                        if leftover.is_symlink() or (leftover.exists() and not leftover.is_file()):
                            raise SourceExportConflict("download staging is not a regular file")
                        leftover.unlink(missing_ok=True)
                    write_package_archive(self.paths.package_directory / export_id, staging)
                    size, digest = _digest(staging)
                    with self.state.transaction() as connection:
                        connection.execute(
                            "UPDATE source_export_deliveries SET candidate_digest=?,archive_size=?,"
                            "updated_at=? WHERE export_id=?",
                            (digest, size, now(), export_id),
                        )
                    _publish_file(staging, archive)
                if after_publish_hook is not None:
                    after_publish_hook()
                with self.state.transaction() as connection:
                    connection.execute(
                        "UPDATE source_export_deliveries SET status='ready',archive_sha256=?,"
                        "archive_size=?,updated_at=? WHERE export_id=?",
                        (digest, size, now(), export_id),
                    )
            except Exception:
                with self.state.transaction() as connection:
                    connection.execute(
                        "UPDATE source_export_deliveries SET status='failed',"
                        "error_code='export-delivery-not-confirmed',updated_at=? WHERE export_id=?",
                        (now(), export_id),
                    )
        return self.get(export_id)

    def open_download(self, export_id: str) -> tuple[BinaryIO, dict[str, Any]]:
        job = self.get(export_id)
        if job["status"] != "ready":
            raise SourceExportConflict("export download is not ready")
        archive = self._directory(export_id, create=False) / "package.zip"
        descriptor = os.open(
            archive, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        stream = os.fdopen(descriptor, "rb")
        try:
            details = os.fstat(stream.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_size != job["archive_size"]:
                raise SourceExportConflict("confirmed download size changed")
            digest = hashlib.sha256()
            read = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                read += len(chunk)
                if read > job["archive_size"]:
                    raise SourceExportConflict("confirmed download grew")
                digest.update(chunk)
            if read != job["archive_size"] or digest.hexdigest() != job["archive_sha256"]:
                raise SourceExportConflict("confirmed download bytes changed")
            stream.seek(0)
            return stream, job
        except BaseException:
            stream.close()
            raise
