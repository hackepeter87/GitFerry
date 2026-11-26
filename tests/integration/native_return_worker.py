"""Ref-specific return feasibility, not an application return-plan implementation.

Only the files explicitly copied by the harness cross networks. A compares its
own saved export refs before the native adapter writes; B never supplies A's
baseline or authority. No new package format or synchronization engine is used.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import plan_worker
from gitferry.application_package import inspect_package
from gitferry.gitea_bundles import GiteaBundleError, GiteaBundlePusher
from gitferry.gitea_lfs import GiteaLFSClient
from gitferry.lfs_payloads import LFSPointer
from gitferry.native_operations import prepare_native_operations
from gitferry.provisioning import ProvisioningPlan
from gitferry.state_store import OperationBlocked
from native_payload_worker import (
    ADMIN,
    HTTPS,
    _assert_isolated,
    _cert,
    _profile_state,
    _remote,
    _root,
    _token,
)

SCOPE = "native-payload-normal/selected"


def run(side: str) -> dict[str, Any]:
    _assert_isolated(side)
    root = _root(side, "normal")
    context = _cert(root)
    state, paths, _ = _profile_state(root, side, context)
    token = _token(root, side)
    directory = root / "return-files" if side == "target" else Path("/tmp/native-return")
    directory.mkdir(exist_ok=True)
    with (
        plan_worker.proxy(preserve_origin=True),
        GiteaBundlePusher(
            directory, paths.work_directory, HTTPS, ADMIN, token, ca_file=root / "trusted.pem"
        ) as git,
        GiteaLFSClient(HTTPS, SCOPE, ADMIN, token, verify=context) as lfs,
    ):
        parent = _remote(side, "GET", "/repos/" + SCOPE, token=token)
        if side == "target":
            _remote(
                side,
                "PATCH",
                "/repos/" + SCOPE + "/wiki/page/Home",
                token=token,
                json={
                    "title": "Home",
                    "content_base64": base64.b64encode(b"Changed offline on B\n").decode(),
                    "message": "Synthetic offline change",
                },
            )
            data = b"New B native LFS payload\x00\xff\n" * 128
            pointer = LFSPointer(hashlib.sha256(data).hexdigest(), len(data))
            local = paths.work_directory / "return-object.bin"
            local.write_bytes(data)
            lfs.upload(pointer, local, max_bytes=1024 * 1024)
            lfs.fetch(pointer, directory, "lfs/" + pointer.oid, max_bytes=1024 * 1024)
            checkout = paths.work_directory / "return-checkout"
            git._run_git(["clone", git._repository_url(SCOPE), str(checkout)], cwd=None)
            git._run_git(["config", "user.name", "Synthetic Offline Author"], cwd=checkout)
            git._run_git(["config", "user.email", "offline@example.invalid"], cwd=checkout)
            (checkout / "asset.bin").write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                f"oid sha256:{pointer.oid}\nsize {pointer.size}\n"
            )
            git._run_git(["add", "asset.bin"], cwd=checkout)
            git._run_git(["commit", "-m", "Synthetic offline pointer change"], cwd=checkout)
            git._run_git(["push", "origin", "HEAD"], cwd=checkout)
            ordinary = git.export_bundle(
                SCOPE, member="bundles/return.bundle", expected_repository_id=str(parent["id"])
            )

            bundle = git.export_bundle(
                SCOPE,
                member="wikis/return.bundle",
                expected_repository_id=str(parent["id"]),
                wiki=True,
            )
            refs = git._git_remote("ls-remote", git._wiki_url(SCOPE), None)
            facts = {
                "bundle": {"member": bundle.member, "sha256": bundle.sha256, "size": bundle.size},
                "refs": refs,
                "lfs": {"oid": pointer.oid, "size": pointer.size},
                "ordinary": {
                    "member": ordinary.member,
                    "sha256": ordinary.sha256,
                    "size": ordinary.size,
                },
            }
            # The existing first-import planner must not apply these changed refs.
            with state.read_connection() as connection:
                row = connection.execute(
                    "SELECT origin_id FROM operations WHERE operation_type='push_wiki_bundle'"
                ).fetchone()
                organization = connection.execute(
                    "SELECT origin_id FROM operations WHERE operation_type='create_organization'"
                ).fetchone()
            plan = ProvisioningPlan(
                "target", organization[0], SCOPE.split("/")[0], row[0], SCOPE, (), None
            )
            try:
                prepare_native_operations(
                    state,
                    plan,
                    {
                        "repository": SCOPE,
                        "lfs": [],
                        "wiki": {
                            "state": "populated",
                            "refs": refs,
                            "bundle": {
                                "path": bundle.member,
                                "sha256": bundle.sha256,
                                "size": bundle.size,
                            },
                        },
                    },
                )
            except OperationBlocked:
                pass
            else:
                raise RuntimeError("first-import planner accepted a changed wiki return")
            (directory / "facts.json").write_text(json.dumps(facts))
            return {"status": "passed", "application_return_blocked": True, "files": facts}
        control_url = git._repository_url("native-payload-normal/missing-lfs")
        control_before = git._git_remote("ls-remote", control_url, None)
        facts = json.loads((directory / "facts.json").read_text())
        original = inspect_package(paths.package_directory / "native-export-normal").document
        expected = original["repositories"][0]["wiki"]["refs"]
        pointer = LFSPointer(**facts["lfs"])
        from gitferry.lfs_inventory import inventory_lfs_pointers
        from gitferry.provisioning import BundleIntent

        incoming = git.preflight(BundleIntent(**facts["ordinary"]))
        if pointer not in inventory_lfs_pointers(incoming.destination):
            raise RuntimeError("returned LFS object has no returned Git-history pointer")

        lfs.upload(pointer, directory / "lfs" / pointer.oid, max_bytes=1024 * 1024)
        lfs.verify_remote(pointer, max_bytes=1024 * 1024)
        bundle = facts["bundle"]
        result = git.push_wiki_bundle(
            SCOPE, bundle["member"], bundle["sha256"], bundle["size"], str(parent["id"]), expected
        )
        returned = git._git_remote("ls-remote", git._wiki_url(SCOPE), None)
        page = _remote(side, "GET", "/repos/" + SCOPE + "/wiki/page/Home", token=token)
        if (
            returned != facts["refs"]
            or base64.b64decode(page["content_base64"]) != b"Changed offline on B\n"
        ):
            raise RuntimeError("returned wiki bytes/refs differ")
        _remote(
            side,
            "PATCH",
            "/repos/" + SCOPE + "/wiki/page/Home",
            token=token,
            json={
                "title": "Home",
                "content_base64": base64.b64encode(b"New local A change\n").decode(),
                "message": "Synthetic A drift",
            },
        )
        drift = git._git_remote("ls-remote", git._wiki_url(SCOPE), None)
        try:
            git.push_wiki_bundle(
                SCOPE,
                bundle["member"],
                bundle["sha256"],
                bundle["size"],
                str(parent["id"]),
                returned,
            )
        except GiteaBundleError:
            pass
        else:
            raise RuntimeError("selected wiki drift was overwritten")
        if git._git_remote("ls-remote", git._wiki_url(SCOPE), None) != drift:
            raise RuntimeError("wiki changed after rejected return")
        if git._git_remote("ls-remote", control_url, None) != control_before:
            raise RuntimeError("unselected control refs changed during return")
        return {
            "status": "passed",
            "control_refs_unchanged": True,
            "scope": "native adapter feasibility only",
            "lfs_sha256": pointer.oid,
            "a_baseline": expected,
            "returned_refs": returned,
            "confirmation": result.observed_state,
            "drift_preserved": drift,
            "application_return_implemented": False,
            "isolation_checked": True,
        }
