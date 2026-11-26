"""Durable, read-only source capture into an application-owned portable package."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gitferry.application_package import (
    PackageArtifact,
    PackageError,
    inspect_package,
    write_package,
)
from gitferry.connection_probe import ConnectionUnavailable, LocalReferences, probe_connection
from gitferry.connection_profiles import ProfileConflict, ProfileService, _identifier
from gitferry.content_inventory import InventoryDocument, InventoryError, export_classes
from gitferry.gitea_bundles import GiteaBundleError, GiteaBundlePusher
from gitferry.gitea_provisioning import ProvisioningTargetConflict, _repository_segments
from gitferry.inventory_jobs import InventoryJobs
from gitferry.operations import OperationExecutor
from gitferry.package_archive import _rename_noreplace, _sync_directory
from gitferry.runtime import RuntimePaths
from gitferry.source_catalog import _SourceReferences
from gitferry.source_export_state import record_source_capture
from gitferry.source_package import build_source_documents
from gitferry.source_snapshot import SourceSnapshotError, capture_source
from gitferry.state_codec import canonical_json, now
from gitferry.state_store import StateStore
from gitferry.transport import write_transport


class SourceExportConflict(RuntimeError):
    """Local intent, source observation or immutable capture needs review."""


class _Selection(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    repository: Annotated[str, Field(min_length=3, max_length=256)]
    global_id: Annotated[str, Field(pattern=r"^[1-9][0-9]*$", max_length=128)]


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    version: Literal[1, 2, 3]
    inventory_id: str | None = None
    inventory_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    source_instance_key: str
    source_profile_revision: Annotated[int, Field(gt=0)]
    recipient_instance_key: str
    repositories: list[_Selection] = Field(min_length=1, max_length=128)
    include_metadata: bool
    include_members: bool
    include_lfs: bool = False
    include_wiki: bool = False


def parse_export_request(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        request = _Request.model_validate(value).model_dump()
        if type(value.get("version")) is not int:
            raise ValueError("version must be an integer")
        if request["version"] < 3:
            if "include_lfs" in value or "include_wiki" in value:
                raise ValueError("legacy requests cannot select native payloads")
            del request["include_lfs"], request["include_wiki"]
        elif not (request["include_lfs"] or request["include_wiki"]):
            raise ValueError("version three requires a selected native payload class")
        if request["version"] == 1:
            if "inventory_id" in value or "inventory_digest" in value:
                raise ValueError("legacy requests cannot bind inventory")
            del request["inventory_id"], request["inventory_digest"]
        else:
            _identifier(request["inventory_id"])
            if request["inventory_digest"] is None:
                raise ValueError("inventory digest is required")
        _identifier(request["source_instance_key"])
        _identifier(request["recipient_instance_key"])
        for item in request["repositories"]:
            _repository_segments(item["repository"])
    except (ValueError, TypeError, ValidationError, ProvisioningTargetConflict):
        raise SourceExportConflict("invalid source export selection") from None
    if request["source_instance_key"] == request["recipient_instance_key"]:
        raise SourceExportConflict("source and recipient must be different instances")
    if len({item["repository"] for item in request["repositories"]}) != len(
        request["repositories"]
    ) or len({item["global_id"] for item in request["repositories"]}) != len(
        request["repositories"]
    ):
        raise SourceExportConflict("source repositories must have unique native identities")
    request["repositories"].sort(key=lambda item: item["repository"])
    return request


class SourceExports:
    def __init__(
        self, state: StateStore, paths: RuntimePaths, references: LocalReferences | None = None
    ):
        self.state, self.paths = state, paths
        self.references = references or LocalReferences()
        self.profiles = ProfileService(state)
        try:
            self.max_git_bytes = int(os.environ.get("GITFERRY_EXPORT_MAX_GIT_BYTES", str(1024**3)))
        except ValueError:
            raise SourceExportConflict("export Git size limit must be a positive integer") from None
        if self.max_git_bytes <= 0:
            raise SourceExportConflict("export Git size limit must be a positive integer")

    def get(self, export_id: str) -> dict[str, Any]:
        _identifier(export_id)
        with self.state.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_exports WHERE export_id=?", (export_id,)
            ).fetchone()
        if row is None:
            raise KeyError("source export is unknown")
        return {
            "export_id": row["export_id"],
            "request": json.loads(row["request_json"]),
            "status": row["status"],
            "package_ref": row["export_id"],
            "package_digest": row["package_digest"],
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self) -> list[dict[str, Any]]:
        with self.state.read_connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT export_id FROM source_exports ORDER BY created_at DESC LIMIT 100"
                )
            ]
        return [self.get(identifier) for identifier in ids]

    def prepare(self, value: Mapping[str, Any], *, export_id: str | None = None) -> dict[str, Any]:
        request = parse_export_request(value)
        identifier = export_id or "export-" + uuid.uuid4().hex
        _identifier(identifier)
        request_json = canonical_json(request)
        digest = hashlib.sha256(request_json.encode()).hexdigest()
        with self.state.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM source_exports WHERE export_id=?", (identifier,)
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != digest:
                    raise SourceExportConflict("export ID cannot be reused for a changed selection")
            else:
                self._inventory(request)
                profile = self.profiles._get(connection, request["source_instance_key"])
                self.profiles._check_revision(profile, request["source_profile_revision"])
                evidence = connection.execute(
                    "SELECT approved_identity FROM connection_checks "
                    "WHERE instance_key=? AND profile_revision=?",
                    (profile.instance_key, profile.revision),
                ).fetchone()
                recipient = connection.execute(
                    "SELECT 1 FROM instances WHERE instance_key=?",
                    (request["recipient_instance_key"],),
                ).fetchone()
                if (
                    not profile.locally_approved
                    or evidence is None
                    or not evidence[0]
                    or recipient is None
                ):
                    raise SourceExportConflict(
                        "source approval and local recipient registration are required"
                    )
                timestamp = now()
                connection.execute(
                    "INSERT INTO source_exports VALUES (?,?,?,?,'prepared',NULL,NULL,NULL,?,?)",
                    (identifier, digest, request_json, evidence[0], timestamp, timestamp),
                )
        return self.get(identifier)

    def _inventory(
        self, request: dict[str, Any], *, reobserve: bool = False
    ) -> InventoryDocument | None:
        if request["version"] == 1:
            return None  # Existing bounded exports remain explicit legacy selections.
        try:
            service = InventoryJobs(self.state, self.references)
            job = service.get(request["inventory_id"])
            expected = {
                key: request[key]
                for key in ("source_instance_key", "source_profile_revision", "repositories")
            }
            expected["requested_classes"] = export_classes(
                metadata=request["include_metadata"],
                members=request["include_members"],
                lfs=request.get("include_lfs", False),
                wiki=request.get("include_wiki", False),
            )
            if job["request"] != expected:
                raise InventoryError("inventory and export selections differ")
            return service.require_ready(
                request["inventory_id"], request["inventory_digest"], reobserve=reobserve
            )
        except (InventoryError, KeyError, ProfileConflict):
            raise SourceExportConflict(
                "reviewed inventory is unavailable, stale, blocked or belongs to another selection"
            ) from None

    def _check_local(self, export_id: str) -> dict[str, Any]:
        job = self.get(export_id)
        request = job["request"]
        profile = self.profiles.get(request["source_instance_key"])
        self.profiles._check_revision(profile, request["source_profile_revision"])
        with self.state.read_connection() as connection:
            row = connection.execute(
                "SELECT source_identity_json FROM source_exports WHERE export_id=?", (export_id,)
            ).fetchone()
            evidence = connection.execute(
                "SELECT approved_identity FROM connection_checks "
                "WHERE instance_key=? AND profile_revision=?",
                (profile.instance_key, profile.revision),
            ).fetchone()
        if not profile.locally_approved or evidence is None or evidence[0] != row[0]:
            raise SourceExportConflict("source approval changed after the export was prepared")
        return cast(dict[str, Any], json.loads(row[0]))

    def _root(self, root: Path) -> None:
        if root.is_symlink() or not root.is_dir():
            raise SourceExportConflict("source capture needs existing owned runtime directories")

    def run(
        self, export_id: str, *, after_publish_hook: Callable[[], None] | None = None
    ) -> dict[str, Any]:
        with OperationExecutor(self.state, "source-" + uuid.uuid4().hex):
            return self._run_owned(export_id, after_publish_hook=after_publish_hook)

    def _run_owned(
        self, export_id: str, *, after_publish_hook: Callable[[], None] | None = None
    ) -> dict[str, Any]:
        """Run while the caller owns this state's OperationExecutor lock."""
        _identifier(export_id)
        self._root(self.paths.package_directory)
        self._root(self.paths.work_directory)
        destination = self.paths.package_directory / export_id
        work = self.paths.work_directory / export_id
        job = self.get(export_id)
        if job["status"] == "captured":
            if work.is_symlink() or (work.exists() and not work.is_dir()):
                raise SourceExportConflict("source work path is not an owned directory")
            if inspect_package(destination).digest_sha256 != job["package_digest"]:
                raise SourceExportConflict("confirmed source package bytes changed")
            if work.exists():
                shutil.rmtree(work)
            return job
        try:
            if work.is_symlink() or (work.exists() and not work.is_dir()):
                raise SourceExportConflict("source work path is not an owned directory")
            self._check_local(export_id)
            if destination.exists() or destination.is_symlink():
                # A crash after publication needs no remote retry. Admit only
                # bytes matching the saved candidate and original local intent.
                record_source_capture(self.state, inspect_package(destination), export_id)
                return self.get(export_id)
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(mode=0o700)
            with self.state.transaction() as connection:
                connection.execute(
                    "UPDATE source_exports SET status='capturing', candidate_digest=NULL, "
                    "error_code=NULL, updated_at=? WHERE export_id=?",
                    (now(), export_id),
                )
            artifact = self._capture(job, work)
            self._check_local(export_id)
            with self.state.transaction() as connection:
                connection.execute(
                    "UPDATE source_exports SET candidate_digest=?, updated_at=? WHERE export_id=?",
                    (artifact.digest_sha256, now(), export_id),
                )
            for member in artifact.files:
                with (artifact.directory / member).open("rb") as stream:
                    os.fsync(stream.fileno())
            for path in sorted(
                (p for p in artifact.directory.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts),
                reverse=True,
            ):
                _sync_directory(path)
            _sync_directory(artifact.directory)
            _rename_noreplace(artifact.directory, destination)
            _sync_directory(destination.parent)
            if after_publish_hook is not None:
                after_publish_hook()
            record_source_capture(self.state, inspect_package(destination), export_id)
        except Exception:
            with self.state.transaction() as connection:
                connection.execute(
                    "UPDATE source_exports SET status='failed', "
                    "error_code='source-capture-not-confirmed', updated_at=? "
                    "WHERE export_id=? AND status!='captured'",
                    (now(), export_id),
                )
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return self.get(export_id)

    def _capture(self, job: dict[str, Any], work: Path) -> PackageArtifact:
        request = job["request"]
        inventory = self._inventory(request, reobserve=True)
        source = request["source_instance_key"]
        profile = self.profiles.get(source)
        self.profiles._check_revision(profile, request["source_profile_revision"])
        if not profile.locally_approved:
            raise SourceExportConflict("source approval is required before reading")
        references = _SourceReferences(self.references, profile.credential_ref, profile.ca_ref)
        evidence = probe_connection(
            self.profiles, source, expected_revision=profile.revision, references=references
        )
        if evidence["requires_approval"] or evidence["observed_identity"] != self._check_local(
            job["export_id"]
        ):
            raise SourceExportConflict("source connection evidence changed")
        token = references.token(profile.credential_ref)
        context, _ = references.tls(profile.ca_ref)
        selections = request["repositories"]
        options = {
            "include_metadata": request["include_metadata"],
            "include_members": request["include_members"],
        }
        snapshot = capture_source(profile.base_url, token, context, selections, **options)
        package, metadata = build_source_documents(
            self.state,
            snapshot,
            export_id=job["export_id"],
            created_at=job["created_at"],
            source=source,
            recipient=request["recipient_instance_key"],
            include_metadata=request["include_metadata"],
        )
        directory = work / "package"
        directory.mkdir(mode=0o700)
        # Git uses the same captured trust anchors as the API, not a reopened CA reference.
        anchors = context.get_ca_certs(binary_form=True)
        if not anchors:
            raise ConnectionUnavailable("source Git capture needs a usable CA trust snapshot")
        ca = work / "captured-ca.pem"
        ca.write_text(
            "".join(ssl.DER_cert_to_PEM_cert(certificate) for certificate in anchors),
            encoding="ascii",
        )
        with GiteaBundlePusher(
            directory, work, profile.base_url, evidence["login"], token, ca_file=ca
        ) as git:
            remaining_bytes = self.max_git_bytes
            for index, selected in enumerate(package["repositories"]):
                native = selections[index]
                bundle = git.export_bundle(
                    selected["repository"],
                    member=f"bundles/{index}.bundle",
                    expected_repository_id=native["global_id"],
                    max_bytes=remaining_bytes,
                )
                if bundle is not None:
                    remaining_bytes -= bundle.size
                    selected["bundle"] = {
                        "path": bundle.member,
                        "size": bundle.size,
                        "sha256": bundle.sha256,
                    }
            if request["version"] == 3:
                if inventory is None or inventory.schema_version != 2:
                    raise SourceExportConflict(
                        "native payload capture requires inventory version two"
                    )
                from gitferry.gitea_lfs import GiteaLFSClient
                from gitferry.lfs_payloads import LFSPointer

                for selected in package["repositories"]:
                    reviewed = next(
                        r for r in inventory.repositories if r.repository == selected["repository"]
                    )
                    classes = {c.content_class: c for c in reviewed.classes}
                    selected["lfs"] = None
                    selected["wiki"] = None
                    if request["include_lfs"]:
                        selected["lfs"] = []
                        with GiteaLFSClient(
                            profile.base_url,
                            selected["repository"],
                            evidence["login"],
                            token,
                            verify=context,
                        ) as lfs:
                            for item in classes["lfs"].objects:
                                if item.size_bytes is None:
                                    raise SourceExportConflict("LFS inventory has no verified size")
                                pointer = LFSPointer(item.key, item.size_bytes)
                                member = "lfs/" + pointer.oid
                                # Content-addressed objects can be shared by selected repositories.
                                if not (directory / member).exists():
                                    lfs.fetch(pointer, directory, member, max_bytes=remaining_bytes)
                                    remaining_bytes -= pointer.size
                                else:
                                    lfs.inspect(pointer)
                                selected["lfs"].append(
                                    {
                                        "oid": pointer.oid,
                                        "size": pointer.size,
                                        "sha256": pointer.oid,
                                        "path": member,
                                    }
                                )
                    if request["include_wiki"]:
                        facts = classes["wiki"].objects[0].facts
                        wiki_bundle = git.export_bundle(
                            selected["repository"],
                            member=f"wikis/{reviewed.native_id}.bundle",
                            expected_repository_id=reviewed.native_id,
                            max_bytes=remaining_bytes,
                            wiki=True,
                        )
                        selected["wiki"] = {
                            "state": facts["state"],
                            "refs": facts["refs"],
                            "bundle": None,
                        }
                        if wiki_bundle is not None:
                            remaining_bytes -= wiki_bundle.size
                            selected["wiki"]["bundle"] = {
                                "path": wiki_bundle.member,
                                "size": wiki_bundle.size,
                                "sha256": wiki_bundle.sha256,
                            }
                        if (
                            git._git_remote(
                                "ls-remote", git._wiki_url(selected["repository"]), None
                            )
                            != facts["refs"]
                        ):
                            raise SourceExportConflict("wiki refs differ from reviewed inventory")
            observed = capture_source(profile.base_url, token, context, selections, **options)
            if canonical_json(observed) != canonical_json(snapshot):
                raise SourceExportConflict("selected source data changed while capturing")
            # Check all refs once more after the full selected-data reread.
            from gitferry.provisioning import BundleIntent

            for selected in package["repositories"]:
                payload = selected["bundle"]
                if payload:
                    observed_refs = git.check_remote(
                        selected["repository"],
                        bundle=BundleIntent(payload["path"], payload["sha256"], payload["size"]),
                        expected_empty=False,
                    )
                    if inventory is not None:
                        from gitferry.package_inventory import expected_git_refs

                        if observed_refs != expected_git_refs(inventory, selected["repository"]):
                            raise SourceExportConflict("Git bundle differs from reviewed inventory")
                elif git._git_remote(
                    "ls-remote", git._repository_url(selected["repository"]), None
                ):
                    raise SourceExportConflict(
                        "empty source repository acquired refs during capture"
                    )
        if inventory is not None:
            from gitferry.package_inventory import attach_inventory

            self._inventory(request, reobserve=True)
            attach_inventory(
                directory,
                package,
                inventory,
                snapshot,
                include_members=request["include_members"],
                include_lfs=request.get("include_lfs", False),
                include_wiki=request.get("include_wiki", False),
            )
        if metadata is not None:
            manifest = write_transport(directory / "metadata", metadata)
            package["metadata"]["digest_sha256"] = manifest.digest_sha256
        return write_package(directory, package)


__all__ = [
    "SourceExportConflict",
    "SourceExports",
    "parse_export_request",
    "SourceSnapshotError",
    "GiteaBundleError",
    "PackageError",
    "ProfileConflict",
]
