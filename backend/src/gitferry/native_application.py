"""LFS/wiki adapters composed into the existing reviewed first-import plan."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .gitea_bundles import GiteaBundlePusher
from .gitea_lfs import GiteaLFSClient
from .gitea_wiki import GiteaWikiClient
from .lfs_inventory import inventory_lfs_pointers
from .lfs_payloads import LFSPointer
from .native_operations import confirmed_native_facts
from .operations import OperationExecutor
from .provisioning import BundleIntent
from .state_store import OperationBlocked, OperationRecord, StateStore, TargetConfirmation


class NativePayloadApplication:
    def __init__(
        self,
        state: StateStore,
        git: GiteaBundlePusher,
        wiki: GiteaWikiClient,
        lfs_client: Callable[[str], GiteaLFSClient],
        *,
        max_bytes: int,
    ) -> None:
        self.state, self.git, self.wiki, self.lfs_client = state, git, wiki, lfs_client
        self.max_bytes = max_bytes

    def preflight(
        self,
        selected: dict[str, Any],
        ordinary_bundle: BundleIntent | None,
    ) -> None:
        if selected["lfs"] is not None:
            pointers: tuple[LFSPointer, ...] = ()
            if ordinary_bundle is not None:
                imported = self.git.preflight(ordinary_bundle)
                pointers = inventory_lfs_pointers(imported.destination)
            if {p.oid: p.size for p in pointers} != {p["oid"]: p["size"] for p in selected["lfs"]}:
                raise OperationBlocked("Git history pointers differ from packaged LFS objects")
        wiki = selected["wiki"]
        if wiki is not None and wiki["bundle"] is not None:
            bundle = wiki["bundle"]
            imported = self.git.preflight(
                BundleIntent(bundle["path"], bundle["sha256"], bundle["size"])
            )
            if self.git._local_refs(imported.destination) != wiki["refs"]:
                raise OperationBlocked("wiki bundle differs from reviewed native refs")

    def _parent(self, operation: OperationRecord) -> str:
        mapping = self.state.get_object_mapping(operation.origin_id, operation.target_instance_key)
        if (
            mapping.target_object_type != "repository"
            or mapping.target_repository_scope != operation.target_repository_scope
        ):
            raise OperationBlocked("native payload parent mapping differs")
        self.git._verify_repository_id(mapping.target_repository_scope, mapping.target_global_id)
        return mapping.target_global_id

    def apply(self, operation: OperationRecord, executor: OperationExecutor) -> None:
        parent = self._parent(operation)
        scope, intent = operation.target_repository_scope, operation.intent
        if operation.operation_type == "upload_lfs_object":
            pointer = LFSPointer(intent["oid"], intent["size"])
            with self.lfs_client(scope) as lfs:
                if operation.status == "confirmed":
                    lfs.verify_remote(pointer, max_bytes=self.max_bytes)
                    return

                def upload(_: dict[str, Any]) -> TargetConfirmation:
                    lfs.upload(
                        pointer, self.git.package_root / intent["member"], max_bytes=self.max_bytes
                    )
                    self.git._verify_repository_id(scope, parent)
                    return TargetConfirmation(
                        parent, None, observed_state={"oid": pointer.oid, "size": pointer.size}
                    )

                executor.execute(operation.operation_id, upload)
        elif operation.operation_type == "bootstrap_wiki":
            if operation.status == "confirmed":
                confirmed_native_facts(self.state, operation)
                return  # The following push compares the saved refs before writing.

            def bootstrap(_: dict[str, Any]) -> TargetConfirmation:
                page = self.wiki.bootstrap(
                    scope, int(parent), intent["title"], intent["content"], intent["message"]
                )
                refs = self.verify_bootstrap(scope, parent, page, intent)
                return TargetConfirmation(
                    parent,
                    None,
                    observed_state={"refs": refs, "bootstrap_sha": page["last_commit"]["sha"]},
                )

            executor.execute(operation.operation_id, bootstrap)
        elif operation.operation_type == "push_wiki_bundle":
            if operation.status == "confirmed":
                current = self.git._git_remote("ls-remote", self.git._wiki_url(scope), None)
                if current != intent["refs"]:
                    raise OperationBlocked("confirmed target wiki refs drifted")
                return
            bootstrap_record = self.state.get_operation(intent["bootstrap_operation_id"])
            if (
                bootstrap_record.origin_id != operation.origin_id
                or bootstrap_record.target_instance_key != operation.target_instance_key
            ):
                raise OperationBlocked("wiki bootstrap belongs to another repository or instance")
            facts = confirmed_native_facts(self.state, bootstrap_record)
            expected = facts.get("refs")
            if not isinstance(expected, dict) or not set(expected) <= set(intent["refs"]):
                raise OperationBlocked(
                    "wiki bootstrap/default branch differs from the selected refs"
                )

            def push(_: dict[str, Any]) -> TargetConfirmation:
                return self.git.push_wiki_bundle(
                    scope, intent["member"], intent["sha256"], intent["size"], parent, expected
                )

            executor.execute(operation.operation_id, push)
        else:
            raise OperationBlocked("unknown native payload operation")

    def resolve_unknown(
        self,
        operation_id: str,
        *,
        evidence: str,
        wiki_commit: str | None = None,
    ) -> OperationRecord:
        """Explicit operator reconciliation with exact native target evidence; no writes."""
        operation = self.state.get_operation(operation_id)
        if operation.status != "outcome_unknown":
            raise OperationBlocked("native operation is not awaiting reconciliation")
        parent = self._parent(operation)
        scope, intent = operation.target_repository_scope, operation.intent
        facts: dict[str, Any]
        if operation.operation_type == "upload_lfs_object":
            pointer = LFSPointer(intent["oid"], intent["size"])
            with self.lfs_client(scope) as lfs:
                lfs.verify_remote(pointer, max_bytes=self.max_bytes)
            facts = {"oid": pointer.oid, "size": pointer.size}
        elif operation.operation_type == "bootstrap_wiki":
            if not wiki_commit:
                raise OperationBlocked("explicit native bootstrap commit SHA is required")
            page = self.wiki.get_page(scope, int(parent), intent["title"])
            if page.get("last_commit", {}).get("sha") != wiki_commit:
                raise OperationBlocked("specified native wiki commit differs")
            refs = self.verify_bootstrap(scope, parent, page, intent)
            facts = {"refs": refs, "bootstrap_sha": wiki_commit}
        elif operation.operation_type == "push_wiki_bundle":
            refs = self.git._git_remote("ls-remote", self.git._wiki_url(scope), None)
            if refs != intent["refs"]:
                raise OperationBlocked("target wiki refs do not prove the intended result")
            facts = {"refs": refs}
        else:
            raise OperationBlocked("operation is outside native payload reconciliation")
        self.git._verify_repository_id(scope, parent)
        return self.state.resolve_unknown_operation(
            operation_id,
            target_global_id=parent,
            target_issue_index=None,
            observed_intent=intent,
            evidence=evidence,
            observed_state=facts,
        )

    def verify_bootstrap(
        self,
        scope: str,
        parent: str,
        page: dict[str, Any],
        intent: dict[str, Any],
    ) -> dict[str, str]:
        """Never overwrite history after merely observing a bootstrap page/title.

        A confirmed bootstrap is a sole root commit containing only our technical
        page. Preexisting history or concurrent content prevents confirmation and
        the journal stays outcome_unknown after the POST; no wiki push follows.
        """
        sha = page["last_commit"]["sha"]
        with self.git.source_mirror(scope, parent, max_bytes=self.max_bytes, wiki=True) as mirror:
            if mirror is None:
                raise OperationBlocked("wiki bootstrap has no readable native history")
            refs = self.git._local_refs(mirror)
            parents = self.git._run_git(
                ["rev-list", "--parents", "-n", "1", sha], cwd=mirror
            ).stdout.strip()
            names = self.git._run_git(
                ["ls-tree", "-r", "--name-only", sha], cwd=mirror
            ).stdout.splitlines()
            filename = intent["title"] + ".md"
            if (
                len(refs) != 1
                or set(refs.values()) != {sha}
                or parents != sha
                or names != [filename]
            ):
                raise OperationBlocked("wiki bootstrap contains preexisting or concurrent history")
            body = self.git._run_git(["show", sha + ":" + filename], cwd=mirror).stdout
            if body != intent["content"]:
                raise OperationBlocked("native wiki bootstrap content differs")
            return refs
