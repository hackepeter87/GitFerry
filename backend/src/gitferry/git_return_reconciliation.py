"""Explicit Git-return reconciliation through a locally approved target profile."""

from __future__ import annotations

import ssl
import tempfile
from pathlib import Path

from .connection_probe import LocalReferences, probe_connection
from .connection_profiles import ProfileService
from .git_return_operations import reconcile_git_ref_operation
from .gitea_bundles import GiteaBundlePusher
from .runtime import RuntimePaths
from .state_store import OperationBlocked, OperationRecord, StateStore


def resolve_git_ref_operation(
    state: StateStore,
    paths: RuntimePaths,
    operation_id: str,
    *,
    evidence: str,
    references: LocalReferences | None = None,
) -> OperationRecord:
    """Confirm an unknown push from live refs on the approved local target only."""

    references = references or LocalReferences()
    operation = state.get_operation(operation_id)
    if operation.status != "outcome_unknown" or operation.operation_type != "push_git_ref":
        raise OperationBlocked("Git ref operation is not awaiting reconciliation")
    profile = ProfileService(state).get(operation.target_instance_key)
    check = probe_connection(
        ProfileService(state),
        profile.instance_key,
        expected_revision=profile.revision,
        references=references,
    )
    if check["requires_approval"]:
        raise OperationBlocked("current target connection requires local approval")
    token = references.token(profile.credential_ref)
    context, ca_digest = references.tls(profile.ca_ref)
    if check["observed_identity"]["ca_digest"] != ca_digest:
        raise OperationBlocked("target TLS trust changed during reconciliation")

    intent = operation.intent
    repository_scope = operation.target_repository_scope
    if repository_scope is None:
        raise OperationBlocked("Git ref operation has no target repository")
    with tempfile.TemporaryDirectory(
        prefix="git-return-reconcile-", dir=paths.work_directory
    ) as temporary:
        ca_file: Path | None = None
        anchors = context.get_ca_certs(binary_form=True)
        if profile.ca_ref is not None:
            if not anchors:
                raise OperationBlocked("Git ref reconciliation needs approved trust anchors")
            ca_file = Path(temporary) / "approved-ca.pem"
            ca_file.write_text(
                "".join(ssl.DER_cert_to_PEM_cert(anchor) for anchor in anchors),
                encoding="ascii",
            )
        with GiteaBundlePusher(
            paths.package_directory,
            Path(temporary),
            profile.base_url,
            check["login"],
            token,
            ca_file=ca_file,
        ) as target:
            return reconcile_git_ref_operation(
                state,
                operation_id,
                lambda: {
                    "target_instance_key": profile.instance_key,
                    "repository_id": str(intent["repository_id"]),
                    "repository_scope": repository_scope,
                    "refs": target.read_repository_refs(
                        repository_scope,
                        expected_repository_id=str(intent["repository_id"]),
                    ),
                },
                evidence=evidence,
            )


__all__ = ["resolve_git_ref_operation"]
