"""Local identity administration using verified accounts and existing immutable bindings."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gitferry.connection_probe import ConnectionUnavailable, LocalReferences
from gitferry.directory_claims import DirectoryClaimsError, DirectoryClaimsUnavailable
from gitferry.directory_identity import (
    DirectorySelection,
    evidence_digest,
    observe_target_directory,
    require_directory_revision,
    same_directory_identity,
    same_directory_subject,
)
from gitferry.directory_settings import DirectorySettingsConflict, DirectorySettingsService
from gitferry.gitea_provisioning import ProvisioningTargetConflict
from gitferry.identity_decisions import IdentityDecisionService
from gitferry.package_admission import PackageAdmissionConflict, _authority
from gitferry.package_decisions import Reference, Selector, TargetRequest, Text
from gitferry.state_codec import canonical_json
from gitferry.state_store import BindingConflict, StateStore
from gitferry.target_accounts import check_target_account
from gitferry.transfer_plans import PlanConflict


class CheckAccountRequest(TargetRequest):
    target_login: Annotated[str, Field(min_length=1, max_length=100)]
    credential_ref: Reference | None = None
    directory: DirectorySelection | None = None


class CandidateRequest(CheckAccountRequest):
    authority_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    selector: Selector
    target_account_id: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$")]
    email: Annotated[str, Field(max_length=254)] | None = None
    prerequisites: dict[str, bool]
    directory_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class IdentityAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: Annotated[int, Field(ge=1)]
    decision: Literal["approved", "rejected", "pending"]
    profile_revision: Annotated[int, Field(ge=1)]
    credential_ref: Reference | None = None


class RegisterRealm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    realm_key: Text
    display_name: Text
    external_marker: Text


class IdentityApplication:
    def __init__(self, state: StateStore, references: LocalReferences | None = None):
        self.state = state
        self.references = references
        self.decisions = IdentityDecisionService(state)

    def check_account(self, request: CheckAccountRequest) -> dict[str, Any]:
        if request.directory is not None and request.credential_ref is None:
            raise BindingConflict("directory account selection requires its own credential check")
        observed = check_target_account(
            self.state, **request.model_dump(exclude={"directory"}), references=self.references
        )
        if request.directory is not None:
            directory = observe_target_directory(
                self.state,
                request.target_instance_key,
                request.directory,
                observed["account_evidence"],
                self.references,
            )
            observed["directory_evidence"] = directory
            observed["directory_digest"] = evidence_digest(directory)
            observed["account_evidence"]["directory"] = directory
        return observed

    def propose(self, request: CandidateRequest) -> dict[str, Any]:
        # The browser's check is presentation evidence only; re-read before storing it.
        observed = self.check_account(
            CheckAccountRequest(
                **{key: getattr(request, key) for key in CheckAccountRequest.model_fields}
            )
        )
        if (
            observed["authority_digest"] != request.authority_digest
            or observed["target_account_id"] != request.target_account_id
            or observed.get("directory_digest") != request.directory_digest
        ):
            raise BindingConflict("target account or local authority changed since review")
        return self.decisions.submit_candidate(
            selector=request.selector.model_dump(),
            target={
                "instance_key": request.target_instance_key,
                "account_id": int(request.target_account_id),
                "login": request.target_login,
            },
            email=request.email,
            prerequisites=request.prerequisites,
            account_evidence=observed["account_evidence"],
            credential_checked=observed["credential_checked"],
            authority_digest=request.authority_digest,
            profile_revision=request.profile_revision,
        )

    def decide(self, identifier: str, request: IdentityAction, *, actor: str) -> dict[str, Any]:
        candidate = self.decisions.get_candidate(identifier)
        observed: dict[str, Any] | None = None
        if request.decision in ("approved", "pending"):
            if candidate["account_evidence"].get("directory") and not request.credential_ref:
                raise ValueError(
                    "a functional credential reference is required for directory review"
                )
            try:
                directory = None
                if candidate["account_evidence"].get("directory"):
                    directory = DirectorySelection.model_validate(
                        candidate["account_evidence"]["directory"]["selection"]
                    )
                    if request.decision == "pending":
                        current = DirectorySettingsService(self.state).get(
                            candidate["target"]["instance_key"], directory.source_name
                        )
                        directory = directory.model_copy(
                            update={"source_revision": current["revision"]}
                        )
                observed = self.check_account(
                    CheckAccountRequest(
                        target_instance_key=candidate["target"]["instance_key"],
                        profile_revision=request.profile_revision,
                        target_login=candidate["target"]["login"],
                        credential_ref=request.credential_ref,
                        directory=directory,
                    )
                )
            except (
                ConnectionUnavailable,
                ProvisioningTargetConflict,
                PackageAdmissionConflict,
                PlanConflict,
                DirectoryClaimsError,
                DirectoryClaimsUnavailable,
                DirectorySettingsConflict,
                BindingConflict,
            ):
                if request.decision == "pending":
                    raise BindingConflict("target account cannot be rechecked") from None
                return self.decisions.mark_conflict(
                    identifier,
                    expected_revision=request.expected_revision,
                    actor=actor,
                    reason_code="target_changed",
                )

        if request.decision == "pending":
            if (
                observed is None
                or str(candidate["target"]["account_id"]) != observed["target_account_id"]
                or not _same_account(
                    candidate["account_evidence"], observed["account_evidence"], trust_refresh=True
                )
            ):
                raise BindingConflict("target account changed; cannot reopen this candidate")
            return self.decisions.refresh_review(
                identifier,
                expected_revision=request.expected_revision,
                actor=actor,
                authority_digest=observed["authority_digest"],
                profile_revision=request.profile_revision,
                credential_checked=observed["credential_checked"],
                directory_evidence=observed["account_evidence"].get("directory"),
            )

        def bind(record: dict[str, Any], connection: sqlite3.Connection) -> None:
            target, selector = record["target"], record["selector"]
            if record["account_evidence"].get("directory") is not None:
                require_directory_revision(
                    connection, target["instance_key"], record["account_evidence"]["directory"]
                )
            try:
                current_authority = _authority(
                    connection, target["instance_key"], request.profile_revision
                )
            except (PackageAdmissionConflict, PlanConflict):
                raise BindingConflict("target approval changed before local commit") from None
            if (
                observed is None
                or str(target["account_id"]) != observed["target_account_id"]
                or not _same_account(record["account_evidence"], observed["account_evidence"])
                or record["profile_revision"] != request.profile_revision
                or record["authority_digest"] != observed["authority_digest"]
                or current_authority != observed["authority_digest"]
            ):
                raise BindingConflict("target account or local approval changed")
            binding_id = (
                "binding-"
                + hashlib.sha256(
                    canonical_json(
                        {
                            "target": target["instance_key"],
                            "selector": selector,
                        }
                    ).encode()
                ).hexdigest()[:48]
            )
            previous = connection.execute(
                "SELECT binding_id,mapping_version,target_account_id,target_login "
                "FROM identity_bindings "
                "WHERE source_realm_key=? AND source_login=? AND source_external_id=? "
                "AND source_ou_path=? AND target_instance_key=?",
                (
                    *[selector[key] for key in ("realm_key", "login", "external_id", "ou_path")],
                    target["instance_key"],
                ),
            ).fetchone()
            if previous:
                if (
                    str(previous["target_account_id"]) != str(target["account_id"])
                    or previous["target_login"] != target["login"]
                ):
                    raise BindingConflict("existing identity binding must not be redirected")
                binding_id = previous["binding_id"]
            self.state.confirm_identity_binding(
                binding_id=binding_id,
                source_realm_key=selector["realm_key"],
                source_login=selector["login"],
                source_external_id=selector["external_id"],
                source_ou_path=selector["ou_path"],
                target_instance_key=target["instance_key"],
                target_account_id=int(target["account_id"]),
                target_login=target["login"],
                mapping_version=previous["mapping_version"] if previous else 1,
                connection=connection,
            )

        return self.decisions.decide(
            identifier,
            expected_revision=request.expected_revision,
            decision=request.decision,
            actor=actor,
            binder=bind if observed else None,
            credential_checked=bool(observed and observed["credential_checked"]),
        )


def _same_account(
    expected: dict[str, Any], observed: dict[str, Any], *, trust_refresh: bool = False
) -> bool:
    if any(expected.get(key) != observed.get(key) for key in ("source_id", "login_name")):
        return False
    previous, current = expected.get("directory"), observed.get("directory")
    if previous is None or current is None:
        return previous is current
    return (same_directory_subject if trust_refresh else same_directory_identity)(previous, current)
