"""Fixed Gitea execution for locally reviewed plans, using the existing journal."""

from __future__ import annotations

import hashlib
import shutil
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from gitferry.comment_operations import execute_comment_operation, preflight_comment_operation
from gitferry.connection_probe import LocalReferences, probe_connection
from gitferry.connection_profiles import ProfileService
from gitferry.gitea_comments import GiteaCommentClient
from gitferry.gitea_issues import GiteaIssueClient
from gitferry.gitea_provisioning import (
    GiteaProvisioningClient,
    verify_account,
    verify_technical_administrator,
)
from gitferry.issue_operations import execute_issue_operation, preflight_issue_operation
from gitferry.operations import OperationExecutor
from gitferry.plan_application import PlanRequest, parse_plan_request
from gitferry.provisioning import preflight_provisioning_operations, provision_selected_repository
from gitferry.runtime import RuntimePaths
from gitferry.state_store import OperationBlocked, OperationRecord, StateStore


class GiteaPlanAdapter:
    """No caller-supplied URLs, credentials, adapters or status overrides in a plan."""

    def __init__(
        self, state: StateStore, paths: RuntimePaths, references: LocalReferences | None = None
    ):
        self.state, self.paths = state, paths
        self.references = references or LocalReferences()
        self.resources = ExitStack()
        self.issues: dict[str, GiteaIssueClient] = {}
        self.comments: dict[str, GiteaCommentClient] = {}
        self.admin: GiteaProvisioningClient | None = None
        self.request: PlanRequest | None = None
        self.preflight_digest: str | None = None
        # Imported lazily so the existing issue/comment operations remain independent.
        from gitferry.gitea_bundles import GiteaBundlePusher

        self.bundles: GiteaBundlePusher | None = None

    def __enter__(self) -> GiteaPlanAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.resources.close()

    def _metadata(self, plan: dict[str, Any]) -> list[OperationRecord]:
        return [
            self.state.get_operation(step["operation_id"])
            for step in plan["operations"]
            if step["operation_type"]
            in ("create_issue", "update_issue", "create_comment", "update_comment")
        ]

    def preflight(self, plan: dict[str, Any]) -> None:
        document = plan["document"]
        request = parse_plan_request(document["inventory"]["request"])
        self.request = request
        if request.target_instance_key != plan["target_instance_key"]:
            raise OperationBlocked("plan selection points at another target")
        if request.unsupported_items:
            raise OperationBlocked("selected unsupported contents require a revised selection")
        # The estimate is explicit, not a claim about arbitrary Git expansion. A
        # bounded initial import still needs both payload and working space.
        for directory in {self.paths.package_directory, self.paths.work_directory}:
            if shutil.disk_usage(directory).free < document["required_bytes"]:
                raise OperationBlocked("insufficient local space for the reviewed plan")
        profiles = ProfileService(self.state)
        evidence = probe_connection(
            profiles,
            request.target_instance_key,
            expected_revision=plan["profile_revision"],
            references=self.references,
        )
        if evidence["requires_approval"] or evidence["evidence_digest"] != plan["evidence_digest"]:
            raise OperationBlocked("target connection evidence changed; local approval required")
        profile = profiles.get(request.target_instance_key)
        context, ca_digest = self.references.tls(profile.ca_ref)
        if ca_digest != evidence["observed_identity"]["ca_digest"]:
            raise OperationBlocked("local CA reference changed after the connection check")
        token = self.references.token(profile.credential_ref)
        self.admin = self.resources.enter_context(
            GiteaProvisioningClient(
                profile.base_url, username=evidence["login"], token=token, verify=context
            )
        )
        administrator_id = verify_technical_administrator(
            self.admin.get_authenticated_user(), evidence["login"]
        )
        if str(administrator_id) != evidence["account_id"]:
            raise OperationBlocked("technical account changed since connection approval")
        for repository in request.repositories:
            preflight_provisioning_operations(
                self.state, self.admin, repository.provisioning(request.target_instance_key)
            )
        metadata = self._metadata(plan)
        # Resolve every actor before any target mutation. A token for the issue
        # author or technical administrator cannot stand in for a comment author.
        for operation in metadata:
            binding_id = str(operation.intent["author_binding_id"])
            if binding_id in self.issues:
                continue
            binding = self.state.get_identity_binding(binding_id)
            if binding.target_instance_key != request.target_instance_key:
                raise OperationBlocked("selected author binding points at another instance")
            reference = request.author_credential_refs.get(binding_id)
            if reference is None:
                raise OperationBlocked("selected author has no local credential reference")
            author_token = self.references.token(reference)
            issue_client = GiteaIssueClient(
                profile.base_url,
                username=binding.target_login,
                token=author_token,
                verify=context,
            )
            self.resources.callback(issue_client.close)
            verify_account(
                issue_client.get_authenticated_user(),
                binding.target_login,
                binding.target_account_id,
            )
            self.issues[binding_id] = issue_client
            self.comments[binding_id] = self.resources.enter_context(
                GiteaCommentClient(
                    profile.base_url,
                    username=binding.target_login,
                    token=author_token,
                    verify=context,
                )
            )
        planned_parents = frozenset(
            operation.origin_id
            for operation in metadata
            if operation.operation_type == "create_issue"
        )
        for operation in metadata:
            self._preflight_metadata(operation, planned_parents)
        if any(repository.bundle for repository in request.repositories):
            self._preflight_bundles(
                plan, profile.base_url, evidence["login"], token, profile.ca_ref, ca_digest
            )
        self.preflight_digest = plan["digest"]

    def _preflight_metadata(
        self, operation: OperationRecord, planned_parents: frozenset[str]
    ) -> None:
        if self.request is None:
            raise OperationBlocked("plan request has not been validated")
        binding_id = str(operation.intent["author_binding_id"])
        client = self.issues[binding_id]
        repository = operation.target_repository_scope
        remote = client.find_repository(repository)
        planned = next(
            (item for item in self.request.repositories if item.repository == repository), None
        )
        if remote is None:
            if planned is None or not any(
                member.binding_id == binding_id for team in planned.teams for member in team.members
            ):
                raise OperationBlocked(
                    "metadata author needs existing or explicitly planned access"
                )
        elif remote.get("archived") or remote.get("has_issues") is not True:
            raise OperationBlocked("selected repository does not permit issue operations")
        if operation.operation_type in ("create_issue", "update_issue"):
            preflight_issue_operation(self.state, client, operation.operation_id)
            labels = operation.intent["values"]["labels"]
            if labels:
                if remote is None:
                    raise OperationBlocked("new repositories require separate label provisioning")
                client.label_ids(repository, labels)
        else:
            parent_origin = str(operation.intent["parent_origin_id"])
            try:
                parent = self.state.get_object_mapping(parent_origin, operation.target_instance_key)
            except KeyError:
                if parent_origin not in planned_parents:
                    raise OperationBlocked("comment parent is not mapped or selected") from None
            else:
                if (
                    parent.target_repository_scope != repository
                    or parent.target_issue_index is None
                ):
                    raise OperationBlocked("comment parent mapping points at another repository")
                value = client.get_issue(repository, parent.target_issue_index)
                if str(value.get("id")) != parent.target_global_id:
                    raise OperationBlocked(
                        "mapped parent issue no longer has the confirmed identity"
                    )
            preflight_comment_operation(
                self.state,
                self.comments[binding_id],
                operation.operation_id,
                planned_parent_origins=planned_parents,
            )

    def _preflight_bundles(
        self,
        plan: dict[str, Any],
        base_url: str,
        username: str,
        token: str,
        ca_ref: str | None,
        ca_digest: str,
    ) -> None:
        from gitferry.gitea_bundles import GiteaBundlePusher

        if self.request is None or self.admin is None:
            raise OperationBlocked("plan request and technical account must be validated first")
        if self.request.package_ref is None:
            raise OperationBlocked("selected bundles require an owned package")
        root = self.paths.package_directory / self.request.package_ref
        if root.is_symlink() or root.resolve().parent != self.paths.package_directory.resolve():
            raise OperationBlocked("package reference leaves the owned package directory")
        ca_file: Path | None = None
        if ca_ref is not None:
            # Copy the already validated CA bytes into an owned temporary file;
            # Git must use exactly the trust material checked by preflight.
            import tempfile

            directory = Path(
                self.resources.enter_context(
                    tempfile.TemporaryDirectory(prefix="plan-ca-", dir=self.paths.work_directory)
                )
            )
            ca_file = directory / "ca.pem"
            value = self.references._read(self.references.cas, ca_ref, secret=False)
            if hashlib.sha256(value).hexdigest() != ca_digest:
                raise OperationBlocked("local CA reference changed before bundle preflight")
            ca_file.write_bytes(value)
        self.bundles = self.resources.enter_context(
            GiteaBundlePusher(
                package_root=root,
                work_root=self.paths.work_directory,
                base_url=base_url,
                username=username,
                token=token,
                ca_file=ca_file,
            )
        )
        for repository in self.request.repositories:
            if repository.bundle:
                from gitferry.provisioning import BundleIntent

                bundle = BundleIntent(**repository.bundle.model_dump())
                self.bundles.preflight(bundle)
                if self.admin.find_repository(repository.repository) is not None:
                    confirmed = any(
                        step["operation_type"] == "push_git_bundle"
                        and step["target_repository_scope"] == repository.repository
                        and step["status"] == "confirmed"
                        for step in plan["operations"]
                    )
                    self.bundles.check_remote(
                        repository.repository, bundle=bundle, expected_empty=not confirmed
                    )

    def apply(self, plan: dict[str, Any], executor: OperationExecutor) -> None:
        if self.preflight_digest != plan["digest"] or self.request is None or self.admin is None:
            raise OperationBlocked("complete preflight is required before execution")
        for repository in self.request.repositories:
            provision_selected_repository(
                self.state,
                executor,
                self.admin,
                repository.provisioning(self.request.target_instance_key),
                bundle_pusher=self.bundles,
            )
        # PlanService stored the dependency order, independent of input ordering.
        for operation in self._metadata(plan):
            binding_id = str(operation.intent["author_binding_id"])
            if operation.operation_type in ("create_issue", "update_issue"):
                result = execute_issue_operation(
                    self.state, executor, self.issues[binding_id], operation.operation_id
                )
            else:
                result = execute_comment_operation(
                    self.state, executor, self.comments[binding_id], operation.operation_id
                )
            if result.status != "confirmed":
                raise OperationBlocked("metadata operation needs local resolution")
