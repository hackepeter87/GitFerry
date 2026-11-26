"""Durable read-only inventory jobs using the existing single executor boundary."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import tempfile
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gitferry.connection_probe import LocalReferences, probe_connection
from gitferry.connection_profiles import ProfileService, _identifier
from gitferry.content_inventory import (
    CONTENT_CLASSES,
    InventoryDocument,
    InventoryError,
    comparison_facts,
    inventory_digest,
    inventory_summary,
    parse_inventory,
)
from gitferry.gitea_provisioning import ProvisioningTargetConflict, _repository_segments
from gitferry.operations import OperationExecutor
from gitferry.source_catalog import _SourceReferences
from gitferry.state_codec import canonical_json, now
from gitferry.state_store import StateStore


class _Repository(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    repository: Annotated[str, Field(min_length=3, max_length=256)]
    global_id: Annotated[str, Field(pattern=r"^[1-9][0-9]*$", max_length=128)]


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    source_instance_key: str
    source_profile_revision: Annotated[int, Field(gt=0)]
    repositories: list[_Repository] = Field(min_length=1, max_length=128)
    requested_classes: list[str] = Field(min_length=1, max_length=len(CONTENT_CLASSES))


class _InventoryStageError(RuntimeError):
    """Secret-free stage marker for a failed inventory observation."""

    def __init__(self, stage: str):
        self.error_code = f"inventory_failed_{stage}"
        super().__init__(self.error_code)


def parse_request(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        request = _Request.model_validate(value).model_dump()
        _identifier(request["source_instance_key"])
        for item in request["repositories"]:
            _repository_segments(item["repository"])
        classes = request["requested_classes"]
        if len(set(classes)) != len(classes) or not set(classes) <= set(CONTENT_CLASSES):
            raise ValueError("invalid requested classes")
        for field in ("repository", "global_id"):
            if len({item[field] for item in request["repositories"]}) != len(
                request["repositories"]
            ):
                raise ValueError("duplicate repository")
    except (ValueError, ValidationError, ProvisioningTargetConflict):
        raise InventoryError("invalid inventory selection") from None
    request["repositories"].sort(key=lambda item: item["repository"])
    request["requested_classes"].sort()
    return request


class InventoryJobs:
    def __init__(self, state: StateStore, references: LocalReferences | None = None):
        self.state = state
        self.profiles = ProfileService(state)
        self.references = references or LocalReferences()

    def get(self, inventory_id: str) -> dict[str, Any]:
        _identifier(inventory_id)
        with self.state.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM source_inventory_jobs WHERE inventory_id=?", (inventory_id,)
            ).fetchone()
        if row is None:
            raise KeyError("inventory is unknown")
        document = (
            parse_inventory(json.loads(row["document_json"])) if row["document_json"] else None
        )
        if document is not None and inventory_digest(document) != row["document_digest"]:
            raise InventoryError("stored inventory digest differs")
        return {
            "inventory_id": inventory_id,
            "request": json.loads(row["request_json"]),
            "status": row["status"],
            "document": document.model_dump(mode="json") if document else None,
            "document_digest": row["document_digest"],
            "summary": inventory_summary(document) if document else None,
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self) -> list[dict[str, Any]]:
        with self.state.read_connection() as connection:
            rows = connection.execute(
                "SELECT inventory_id,status,document_digest,error_code,created_at,updated_at "
                "FROM source_inventory_jobs ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

    def create(self, inventory_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        _identifier(inventory_id)
        request = parse_request(value)
        encoded = canonical_json(request)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.state.transaction() as connection:
            existing = connection.execute(
                "SELECT request_hash FROM source_inventory_jobs WHERE inventory_id=?",
                (inventory_id,),
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise InventoryError("inventory ID cannot be reused for changed intent")
            else:
                profile = self.profiles._get(connection, request["source_instance_key"])
                self.profiles._check_revision(profile, request["source_profile_revision"])
                evidence = connection.execute(
                    "SELECT approved_identity FROM connection_checks WHERE "
                    "instance_key=? AND profile_revision=?",
                    (profile.instance_key, profile.revision),
                ).fetchone()
                if not profile.locally_approved or evidence is None or not evidence[0]:
                    raise InventoryError("approved source connection is required")
                timestamp = now()
                connection.execute(
                    "INSERT INTO source_inventory_jobs VALUES "
                    "(?,?,?,?,?,'queued',NULL,NULL,NULL,?,?)",
                    (
                        inventory_id,
                        profile.instance_key,
                        digest,
                        encoded,
                        evidence[0],
                        timestamp,
                        timestamp,
                    ),
                )
        return self.get(inventory_id)

    def _authority(self, job: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        request = job["request"]
        profile = self.profiles.get(request["source_instance_key"])
        self.profiles._check_revision(profile, request["source_profile_revision"])
        with self.state.read_connection() as connection:
            saved = connection.execute(
                "SELECT source_identity_json FROM source_inventory_jobs WHERE inventory_id=?",
                (job["inventory_id"],),
            ).fetchone()
            current = connection.execute(
                "SELECT approved_identity FROM connection_checks WHERE "
                "instance_key=? AND profile_revision=?",
                (profile.instance_key, profile.revision),
            ).fetchone()
        if not profile.locally_approved or not current or current[0] != saved[0]:
            raise InventoryError("source authority changed after inventory selection")
        return profile, json.loads(saved[0])

    def _observe(self, job: dict[str, Any]) -> InventoryDocument:
        from gitferry.source_inventory import collect_inventory

        try:
            profile, approved = self._authority(job)
        except Exception as exc:
            raise _InventoryStageError("authority") from exc
        references = _SourceReferences(self.references, profile.credential_ref, profile.ca_ref)
        try:
            evidence = probe_connection(
                self.profiles,
                profile.instance_key,
                expected_revision=profile.revision,
                references=references,
            )
        except Exception as exc:
            raise _InventoryStageError("connection") from exc
        if evidence["requires_approval"] or evidence["observed_identity"] != approved:
            raise _InventoryStageError("authority")
        token = references.token(profile.credential_ref)
        context, _ = references.tls(profile.ca_ref)
        request = job["request"]
        try:
            document = collect_inventory(
                profile.base_url,
                token,
                context,
                request["repositories"],
                request["requested_classes"],
                source_instance_key=profile.instance_key,
                installation_marker=profile.installation_marker,
                snapshot_id=job["inventory_id"],
                created_at=job["created_at"],
            )
        except Exception as exc:
            raise _InventoryStageError("metadata") from exc
        if {"lfs", "wiki"}.intersection(request["requested_classes"]):
            try:
                document = self._native_payloads(
                    document, profile, evidence["login"], token, context
                )
            except Exception as exc:
                raise _InventoryStageError("native_payloads") from exc
        try:
            self._authority(job)
        except Exception as exc:
            raise _InventoryStageError("authority") from exc
        return document

    def _native_payloads(
        self,
        document: InventoryDocument,
        profile: Any,
        login: str,
        token: str,
        context: ssl.SSLContext,
    ) -> InventoryDocument:
        from gitferry.gitea_bundles import GiteaBundlePusher
        from gitferry.gitea_lfs import GiteaLFSClient
        from gitferry.gitea_wiki import GiteaWikiClient
        from gitferry.native_inventory import observe_native_payloads

        # Managed runtimes supply their configured work volume; standalone callers
        # retain the same private local state parent rather than a hidden host path.
        work_root = Path(os.environ.get("GITFERRY_WORK_DIR", str(self.state.path.parent)))
        if work_root.is_symlink() or not work_root.is_dir():
            raise InventoryError("native inventory needs an existing local work directory")
        try:
            maximum = int(os.environ.get("GITFERRY_EXPORT_MAX_GIT_BYTES", str(1024**3)))
        except ValueError:
            raise InventoryError("native inventory size bound must be positive") from None
        if maximum <= 0:
            raise InventoryError("native inventory size bound must be positive")
        anchors = context.get_ca_certs(binary_form=True)
        if not anchors:
            raise InventoryError("native inventory needs approved CA trust anchors")
        with tempfile.TemporaryDirectory(prefix="inventory-", dir=work_root) as temporary:
            work = Path(temporary)
            ca = work / "captured-ca.pem"
            ca.write_text("".join(ssl.DER_cert_to_PEM_cert(c) for c in anchors), encoding="ascii")
            git = GiteaBundlePusher(work, work, profile.base_url, login, token, ca_file=ca)
            wiki = GiteaWikiClient(profile.base_url, username=login, token=token, verify=context)
            with git, wiki:
                return observe_native_payloads(
                    document,
                    git,
                    lambda repository: GiteaLFSClient(
                        profile.base_url, repository, login, token, verify=context
                    ),
                    wiki,
                    max_bytes=maximum,
                )

    def _validate_result(self, job: dict[str, Any], document: InventoryDocument) -> None:
        parse_inventory(document.model_dump(mode="json"))
        profile, _ = self._authority(job)
        request = job["request"]
        observed = sorted(
            (r.repository, r.native_id, sorted(r.requested_classes)) for r in document.repositories
        )
        selected = sorted(
            (r["repository"], r["global_id"], request["requested_classes"])
            for r in request["repositories"]
        )
        if (
            document.snapshot_id != job["inventory_id"]
            or document.created_at != job["created_at"]
            or document.source_instance_key != profile.instance_key
            or document.source_installation_marker != profile.installation_marker
            or observed != selected
        ):
            raise InventoryError("observed inventory differs from its selected identity and scope")

    def run_next(
        self, *, observe: Callable[[dict[str, Any]], InventoryDocument] | None = None
    ) -> dict[str, Any] | None:
        with self.state.read_connection() as connection:
            if (
                connection.execute(
                    "SELECT 1 FROM source_inventory_jobs WHERE status IN "
                    "('queued','running') LIMIT 1"
                ).fetchone()
                is None
            ):
                return None
        with OperationExecutor(self.state, "inventory-" + uuid.uuid4().hex):
            with self.state.transaction() as connection:
                row = connection.execute(
                    "SELECT inventory_id FROM source_inventory_jobs WHERE status IN "
                    "('queued','running') ORDER BY created_at,inventory_id LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                identifier = row[0]
                connection.execute(
                    "UPDATE source_inventory_jobs SET status='running',updated_at=? "
                    "WHERE inventory_id=?",
                    (now(), identifier),
                )
            job = self.get(identifier)
            try:
                document = (observe or self._observe)(job)
                self._validate_result(job, document)
                summary = inventory_summary(document)
                status = "ready" if summary["transfer_ready"] else "blocked"
                with self.state.transaction() as connection:
                    connection.execute(
                        "UPDATE source_inventory_jobs SET "
                        "status=?,document_json=?,document_digest=?,updated_at=? WHERE "
                        "inventory_id=? AND status='running'",
                        (
                            status,
                            canonical_json(document.model_dump(mode="json")),
                            inventory_digest(document),
                            now(),
                            identifier,
                        ),
                    )
            except Exception as exc:
                # Never copy paths, tokens, remote bodies or exception messages into public state.
                error_code = (
                    exc.error_code if isinstance(exc, _InventoryStageError) else "inventory_failed"
                )
                with self.state.transaction() as connection:
                    connection.execute(
                        "UPDATE source_inventory_jobs SET "
                        "status='failed',error_code=?,updated_at=? WHERE "
                        "inventory_id=? AND status='running'",
                        (error_code, now(), identifier),
                    )
            return self.get(identifier)

    def require_ready(
        self, inventory_id: str, digest: str, *, reobserve: bool = False
    ) -> InventoryDocument:
        job = self.get(inventory_id)
        if job["status"] != "ready" or job["document_digest"] != digest:
            raise InventoryError("selected inventory is not ready or its digest changed")
        self._authority(job)
        document = parse_inventory(job["document"])
        if reobserve:
            observed = self._observe(job)
            self._validate_result(job, observed)
            if comparison_facts(observed) != comparison_facts(document):
                raise InventoryError("selected source facts changed after inventory review")
        return document
