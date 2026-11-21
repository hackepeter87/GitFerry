"""Explicit local prerequisites for an uploaded package; no remote mutations."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from gitferry.application_package import PackageArtifact, load_package_metadata
from gitferry.comment_operations import resolve_author_binding
from gitferry.connection_probe import LocalReferences
from gitferry.connection_profiles import normalize_url
from gitferry.package_admission import PackageAdmissionConflict, PackageAdmissions, _authority
from gitferry.package_selection import _selector
from gitferry.state_store import BindingConflict, OperationBlocked
from gitferry.target_accounts import check_target_account
from gitferry.transport_state import _check_endpoint

Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Text = Annotated[str, Field(min_length=1, max_length=256)]


class TargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_instance_key: Reference
    profile_revision: Annotated[int, Field(ge=1)]


class DecisionRequest(TargetRequest):
    expected_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SenderRequest(DecisionRequest):
    display_name: Text
    base_url: Annotated[str, Field(min_length=1, max_length=2048)]


class RealmRequest(DecisionRequest):
    realm_key: Text
    display_name: Text
    external_marker: Text


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    realm_key: Text
    login: Text
    external_id: Text
    ou_path: Annotated[str, Field(max_length=1024)]


class AccountRequest(DecisionRequest):
    target_login: Annotated[str, Field(min_length=1, max_length=100)]
    credential_ref: Reference | None = None


class BindingRequest(AccountRequest):
    selector: Selector
    target_account_id: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]


def _identities(artifact: PackageArtifact) -> list[dict[str, Any]]:
    identities: dict[str, dict[str, Any]] = {}

    def add(selector: dict[str, Any], role: str) -> None:
        selected = Selector.model_validate(selector).model_dump()
        key = _selector(selected)
        item = identities.setdefault(key, {"selector": selected, "roles": []})
        if role not in item["roles"]:
            item["roles"].append(role)

    for repository in artifact.document["repositories"]:
        for team in repository["teams"]:
            for member in team.get("members", []):
                add(member, "member")
    if artifact.document["metadata"] is not None:
        for item in artifact.document["metadata"].get("issue_authors", []):
            add(item["author"], "author")
        for item in load_package_metadata(artifact).document["objects"]:
            if item["origin"]["object_type"] == "issue_comment":
                add(item["author"], "author")
    return [identities[key] for key in sorted(identities)]


class PackageDecisions:
    def __init__(self, admissions: PackageAdmissions, references: LocalReferences | None = None):
        self.admissions, self.state = admissions, admissions.state
        self.references = references or LocalReferences()

    def _artifact(self, package_ref: str, request: TargetRequest) -> PackageArtifact:
        artifact = self.admissions.artifact(package_ref)
        if artifact.document["recipient"]["instance_key"] != request.target_instance_key:
            raise PackageAdmissionConflict("package recipient differs from selected target")
        if (
            isinstance(request, DecisionRequest)
            and artifact.digest_sha256 != request.expected_digest
        ):
            raise PackageAdmissionConflict("package changed since review")
        with self.state.read_connection() as connection:
            self._authority(connection, artifact, request)
        return artifact

    @staticmethod
    def _authority(
        connection: sqlite3.Connection, artifact: PackageArtifact, request: TargetRequest
    ) -> str:
        _check_endpoint(connection, artifact.document["recipient"], role="recipient")
        return _authority(connection, request.target_instance_key, request.profile_revision)

    def prerequisites(self, package_ref: str, request: TargetRequest) -> dict[str, Any]:
        artifact = self._artifact(package_ref, request)
        document = artifact.document
        sender = {**document["sender"], "registered": False}
        try:
            registered = self.state.get_instance(sender["instance_key"])
        except KeyError:
            pass
        else:
            if registered.installation_marker != sender["installation_marker"]:
                raise PackageAdmissionConflict("sender conflicts with existing registration")
            sender["registered"] = True
        realms = []
        for proposal in document["realms"]:
            realm = {
                **proposal,
                "registered": False,
                "display_name": proposal["realm_key"],
                "external_marker": "",
            }
            try:
                realm.update(asdict(self.state.get_realm(proposal["realm_key"])), registered=True)
            except KeyError:
                pass
            realms.append(realm)
        identities = _identities(artifact)
        for item in identities:
            try:
                binding = resolve_author_binding(
                    self.state, item["selector"], request.target_instance_key
                )
                item["binding"] = {
                    "binding_id": binding.binding_id,
                    "target_login": binding.target_login,
                    "target_account_id": str(binding.target_account_id),
                    "mapping_version": binding.mapping_version,
                }
            except OperationBlocked:
                item["binding"] = None
        return {
            "package_id": document["package_id"],
            "digest_sha256": artifact.digest_sha256,
            "target_instance_key": request.target_instance_key,
            "profile_revision": request.profile_revision,
            "sender": sender,
            "realms": realms,
            "identities": identities,
            "repositories": [item["repository"] for item in document["repositories"]],
            "bundle_bytes": sum(
                (item.get("bundle") or {}).get("size", 0) for item in document["repositories"]
            ),
            "metadata_present": document["metadata"] is not None,
            "limitations": document["limitations"],
        }

    def confirm_sender(self, package_ref: str, request: SenderRequest) -> dict[str, Any]:
        artifact = self._artifact(package_ref, request)
        sender = artifact.document["sender"]
        base_url = normalize_url(request.base_url)
        with self.state.transaction() as connection:
            self._authority(connection, artifact, request)
            self.state.register_instance(
                sender["instance_key"],
                request.display_name,
                base_url,
                sender["installation_marker"],
                connection=connection,
            )
        return self.prerequisites(package_ref, request)

    def confirm_realm(self, package_ref: str, request: RealmRequest) -> dict[str, Any]:
        artifact = self._artifact(package_ref, request)
        if not any(item["realm_key"] == request.realm_key for item in artifact.document["realms"]):
            raise PackageAdmissionConflict("realm is not a proposal in this package")
        with self.state.transaction() as connection:
            self._authority(connection, artifact, request)
            self.state.register_realm(
                request.realm_key,
                request.display_name,
                request.external_marker,
                connection=connection,
            )
        return self.prerequisites(package_ref, request)

    def check_account(self, package_ref: str, request: AccountRequest) -> dict[str, Any]:
        artifact = self._artifact(package_ref, request)
        with self.state.read_connection() as connection:
            authority = self._authority(connection, artifact, request)
        observed = check_target_account(
            self.state,
            target_instance_key=request.target_instance_key,
            profile_revision=request.profile_revision,
            target_login=request.target_login,
            credential_ref=request.credential_ref,
            references=self.references,
        )
        artifact = self._artifact(package_ref, request)
        with self.state.read_connection() as connection:
            if self._authority(connection, artifact, request) != authority:
                raise PackageAdmissionConflict("target approval changed during account check")
        return {
            "authority_digest": authority,
            "target_login": request.target_login,
            "target_account_id": observed["target_account_id"],
            "account_evidence": observed["account_evidence"],
            "credential_checked": request.credential_ref is not None,
        }

    def confirm_binding(
        self, package_ref: str, request: BindingRequest, *, actor: str = "local-cli"
    ) -> dict[str, Any]:
        # Package-local approval and general identity administration share one journal.
        # A new package ID must not bypass a rejected or conflicted local decision.
        from gitferry.identity_application import (
            CandidateRequest,
            IdentityAction,
            IdentityApplication,
        )

        artifact = self._artifact(package_ref, request)
        selector = request.selector.model_dump()
        selected = next(
            (item for item in _identities(artifact) if item["selector"] == selector), None
        )
        if selected is None:
            raise PackageAdmissionConflict("identity is not selected by this package")
        self.state.get_realm(selector["realm_key"])
        observed = self.check_account(package_ref, request)
        if observed["target_account_id"] != request.target_account_id:
            raise PackageAdmissionConflict("target account differs from reviewed identity")
        application = IdentityApplication(self.state, self.references)
        # Prerequisite flags describe intended uses, not a second binding permission system.
        prerequisites = {
            "issue_author": "author" in selected["roles"],
            "comment_author": "author" in selected["roles"],
            "team_member": "member" in selected["roles"],
        }
        known = [
            item
            for item in application.decisions.list_candidates()
            if item["selector"] == selector
            and item["target"]["instance_key"] == request.target_instance_key
            and item["target"]["account_id"] == request.target_account_id
        ]
        if known:
            candidate = known[0]
            if (
                candidate["target"]["login"] != request.target_login
                or candidate["account_evidence"] != observed["account_evidence"]
                or candidate["authority_digest"] != observed["authority_digest"]
                or candidate["profile_revision"] != request.profile_revision
            ):
                raise BindingConflict("candidate evidence changed; review identity administration")
        else:
            candidate = application.propose(
                CandidateRequest(
                    target_instance_key=request.target_instance_key,
                    profile_revision=request.profile_revision,
                    authority_digest=observed["authority_digest"],
                    selector=request.selector,
                    target_account_id=request.target_account_id,
                    target_login=request.target_login,
                    credential_ref=request.credential_ref,
                    prerequisites=prerequisites,
                )
            )
        if candidate["status"] == "pending":
            candidate = application.decide(
                candidate["candidate_id"],
                IdentityAction(
                    expected_revision=candidate["revision"],
                    decision="approved",
                    profile_revision=request.profile_revision,
                    credential_ref=request.credential_ref,
                ),
                actor=actor,
            )
        if candidate["status"] != "approved":
            raise BindingConflict("local identity decision is unresolved; review it explicitly")
        return self.prerequisites(package_ref, request)
