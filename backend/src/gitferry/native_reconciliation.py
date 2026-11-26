"""Explicit native-result resolution using only an approved local profile."""

from __future__ import annotations

import os
import ssl
import tempfile
from pathlib import Path

from .connection_probe import LocalReferences, probe_connection
from .connection_profiles import ProfileService
from .gitea_bundles import GiteaBundlePusher
from .gitea_lfs import GiteaLFSClient
from .gitea_wiki import GiteaWikiClient
from .native_application import NativePayloadApplication
from .operations import OperationExecutor
from .runtime import RuntimePaths
from .state_store import OperationBlocked, OperationRecord, StateStore


def resolve_native_operation(
    state: StateStore,
    paths: RuntimePaths,
    operation_id: str,
    *,
    evidence: str,
    wiki_commit: str | None = None,
    references: LocalReferences | None = None,
) -> OperationRecord:
    references = references or LocalReferences()
    profiles = ProfileService(state)
    with OperationExecutor(state, "native-reconciliation"):
        operation = state.get_operation(operation_id)
        if operation.status != "outcome_unknown":
            raise OperationBlocked("operation is not awaiting reconciliation")
        profile = profiles.get(operation.target_instance_key)
        check = probe_connection(
            profiles,
            profile.instance_key,
            expected_revision=profile.revision,
            references=references,
        )
        if check["requires_approval"] or not profile.locally_approved:
            raise OperationBlocked("current target connection requires local approval")
        token = references.token(profile.credential_ref)
        context, ca_digest = references.tls(profile.ca_ref)
        if ca_digest != check["observed_identity"]["ca_digest"]:
            raise OperationBlocked("target TLS trust changed during reconciliation")
        maximum = int(os.environ.get("GITFERRY_EXPORT_MAX_GIT_BYTES", str(1024**3)))
        if maximum <= 0:
            raise OperationBlocked("native payload bound must be positive")
        with tempfile.TemporaryDirectory(
            prefix="native-reconcile-", dir=paths.work_directory
        ) as temporary:
            ca = Path(temporary) / "ca.pem"
            anchors = context.get_ca_certs(binary_form=True)
            if not anchors:
                raise OperationBlocked("native reconciliation needs approved trust anchors")
            ca.write_text("".join(ssl.DER_cert_to_PEM_cert(c) for c in anchors), encoding="ascii")
            git = GiteaBundlePusher(
                paths.package_directory,
                Path(temporary),
                profile.base_url,
                check["login"],
                token,
                ca_file=ca,
            )
            wiki = GiteaWikiClient(
                profile.base_url, username=check["login"], token=token, verify=context
            )
            with git, wiki:
                adapter = NativePayloadApplication(
                    state,
                    git,
                    wiki,
                    lambda scope: GiteaLFSClient(
                        profile.base_url, scope, check["login"], token, verify=context
                    ),
                    max_bytes=maximum,
                )
                return adapter.resolve_unknown(
                    operation_id, evidence=evidence, wiki_commit=wiki_commit
                )
