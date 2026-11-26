"""Bounded real-source/real-target native payload integration worker.

The source action writes one archive to its private state root.  The host copies
that archive to the target container before running the target action.  No source
database, credentials, or fixture metadata crosses that boundary.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import plan_worker
from gitferry.application_package import DIGEST_NAME, PACKAGE_NAME, inspect_package, write_package
from gitferry.connection_probe import LocalReferences, approve_connection, probe_connection
from gitferry.connection_profiles import ConnectionProfile, ProfileService
from gitferry.content_inventory import export_classes
from gitferry.gitea_bundles import GiteaBundlePusher
from gitferry.gitea_lfs import GiteaLFSClient
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.gitea_wiki import GiteaWikiClient
from gitferry.inventory_jobs import InventoryJobs
from gitferry.lfs_payloads import LFSPointer
from gitferry.native_reconciliation import resolve_native_operation
from gitferry.operations import OperationExecutor
from gitferry.package_admission import PackageAdmissionConflict, PackageAdmissions
from gitferry.package_archive import receive_package_archive, write_package_archive
from gitferry.plan_runner import PlanRunner
from gitferry.runtime import RuntimePaths
from gitferry.source_exports import SourceExports
from gitferry.state_store import OperationBlocked, StateStore
from gitferry.transfer_plans import PlanConflict, PlanService

ADMIN = "gitferry-admin"
REPOSITORY = "native-payload-{case}/selected"
HTTPS = "https://localhost:34443"


def _root(side: str, case: str) -> Path:
    return Path(f"/state/native-payload-{side}-{case}")


def _password(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required integration credential is unavailable: {name}")
    return value


def _remote(side: str, method: str, path: str, *, token: str | None = None, **kwargs: Any) -> Any:
    url = f"http://{side}:3000/api/v1{path}"
    auth = (ADMIN, _password("GITFERRY_IT_ADMIN_PASSWORD"))
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = "token " + token
        auth = None
    response = httpx.request(
        method, url, auth=auth, headers=headers, timeout=20, trust_env=False, **kwargs
    )
    response.raise_for_status()
    return response.json() if response.content else None


def _assert_isolated(side: str) -> None:
    other = "target" if side == "source" else "source"
    for host, port in ((other, 3000), ("1.1.1.1", 443)):
        try:
            with socket.create_connection((host, port), timeout=2):
                raise RuntimeError("network isolation failed")
        except OSError:
            pass


def _cert(root: Path) -> ssl.SSLContext:
    certificate = root / "trusted.pem"
    key = root / "trusted.key"
    if not certificate.exists():
        subprocess.run(  # noqa: S603
            [  # noqa: S607
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-keyout",
                str(key),
                "-out",
                str(certificate),
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            check=True,
            capture_output=True,
        )
        certificate.chmod(0o600)
        key.chmod(0o600)
    context = ssl.create_default_context(cafile=str(certificate))
    return context


def _token(root: Path, side: str) -> str:
    path = root / "secrets" / "admin-token"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_file():
        return path.read_text(encoding="ascii").strip()
    value = _remote(
        side,
        "POST",
        f"/users/{ADMIN}/tokens",
        json={"name": root.name, "scopes": ["all"]},
    )
    token = str(value["sha1"])
    path.write_text(token, encoding="ascii")
    path.chmod(0o600)
    return token


def _ensure_repository(side: str, case: str) -> dict[str, Any]:
    organization = f"native-payload-{case}"
    current = httpx.get(
        f"http://{side}:3000/api/v1/repos/{organization}/selected",
        auth=(ADMIN, _password("GITFERRY_IT_ADMIN_PASSWORD")),
        timeout=20,
        trust_env=False,
    )
    if current.status_code == 404:
        org = httpx.post(
            f"http://{side}:3000/api/v1/orgs",
            auth=(ADMIN, _password("GITFERRY_IT_ADMIN_PASSWORD")),
            json={"username": organization, "visibility": "private"},
            timeout=20,
            trust_env=False,
        )
        if org.status_code not in (201, 409):
            org.raise_for_status()
        return _remote(
            side,
            "POST",
            f"/orgs/{organization}/repos",
            json={"name": "selected", "private": True, "auto_init": False},
        )
    current.raise_for_status()
    return current.json()


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _profile_state(
    root: Path, side: str, context: ssl.SSLContext
) -> tuple[StateStore, RuntimePaths, LocalReferences]:
    database = root / "state.sqlite3"
    paths = RuntimePaths(database, root / "packages", root / "work")
    plan_worker.ROOT = root
    plan_worker.URL = f"http://{side}:3000"
    for directory in (root / "secrets", paths.package_directory, paths.work_directory):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _token(root, side)
    references = LocalReferences(root / "secrets", root)
    state = StateStore(database)
    profiles = ProfileService(state)
    if not profiles.list_profiles():
        profiles.create(
            ConnectionProfile(
                side,
                f"Synthetic {side}",
                HTTPS,
                f"native-payload-{side}-install",
                "admin-token",
                "trusted.pem",
            )
        )
        other = "target" if side == "source" else "source"
        state.register_instance(
            other,
            f"Synthetic {other}",
            f"https://{other}.invalid",
            f"native-payload-{other}-install",
        )
    with plan_worker.proxy(preserve_origin=True):
        evidence = probe_connection(profiles, side, expected_revision=1, references=references)
        approve_connection(
            profiles,
            side,
            expected_revision=1,
            evidence_digest=evidence["evidence_digest"],
        )
    return state, paths, references


def _seed_source(
    root: Path, repository: dict[str, Any], token: str, context: ssl.SSLContext
) -> dict[str, Any]:
    work = root / "work"
    package = root / "packages" / "seed"
    package.mkdir(parents=True, exist_ok=True)
    payload = b"Synthetic native LFS bytes\x00\xff\n" * 128
    pointer = LFSPointer(hashlib.sha256(payload).hexdigest(), len(payload))
    object_path = work / "object.bin"
    object_path.write_bytes(payload)
    with (
        plan_worker.proxy(preserve_origin=True),
        GiteaBundlePusher(package, work, HTTPS, ADMIN, token, ca_file=root / "trusted.pem") as git,
        GiteaLFSClient(
            HTTPS, REPOSITORY.format(case=repository["_case"]), ADMIN, token, verify=context
        ) as lfs,
    ):
        lfs.upload(pointer, object_path, max_bytes=1024 * 1024)
        local = work / "repo"
        local.mkdir(exist_ok=True)
        _git("init", "-b", "main", cwd=local)
        _git("config", "user.name", "Synthetic Author", cwd=local)
        _git("config", "user.email", "author@example.invalid", cwd=local)
        (local / "asset.bin").write_text(
            (
                "version https://git-lfs.github.com/spec/v1\n"
                f"oid sha256:{pointer.oid}\nsize {pointer.size}\n"
            ),
            encoding="ascii",
        )
        _git("add", ".", cwd=local)
        _git("commit", "-m", "Synthetic native pointer", cwd=local)
        git._run_git(
            ["push", git._repository_url(REPOSITORY.format(case=repository["_case"])), "main"],
            cwd=local,
        )
        wiki = GiteaWikiClient(HTTPS, username=ADMIN, token=token, verify=context)
        with wiki:
            wiki.bootstrap(
                REPOSITORY.format(case=repository["_case"]),
                repository["id"],
                "Home",
                "First revision\n",
                "Initialize native wiki",
            )
        _remote(
            "source",
            "PATCH",
            f"/repos/{REPOSITORY.format(case=repository['_case'])}/wiki/page/Home",
            token=token,
            json={
                "title": "Home",
                "content_base64": base64.b64encode(b"Second revision\n").decode(),
                "message": "Second revision",
            },
        )
        ordinary = git.export_bundle(
            REPOSITORY.format(case=repository["_case"]),
            member="bundles/repository.bundle",
            expected_repository_id=str(repository["id"]),
        )
        wiki_bundle = git.export_bundle(
            REPOSITORY.format(case=repository["_case"]),
            member="wikis/repository.bundle",
            expected_repository_id=str(repository["id"]),
            wiki=True,
        )
        refs = git._git_remote(
            "ls-remote", git._wiki_url(REPOSITORY.format(case=repository["_case"])), None
        )
    return {
        "pointer": {
            "oid": pointer.oid,
            "size": pointer.size,
            "sha256": pointer.oid,
            "path": "lfs/" + pointer.oid,
        },
        "ordinary": {"member": ordinary.member, "size": ordinary.size, "sha256": ordinary.sha256},
        "wiki": {
            "state": "populated",
            "refs": refs,
            "bundle": {
                "path": wiki_bundle.member,
                "size": wiki_bundle.size,
                "sha256": wiki_bundle.sha256,
            },
        },
    }


def source(case: str) -> dict[str, Any]:
    root = _root("source", case)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_isolated("source")
    repository = _ensure_repository("source", case)
    repository["_case"] = case
    token = _token(root, "source")
    context = _cert(root)
    state, paths, references = _profile_state(root, "source", context)
    os.environ["GITFERRY_WORK_DIR"] = str(paths.work_directory)
    facts = _seed_source(root, repository, token, context)
    selected = [{"repository": REPOSITORY.format(case=case), "global_id": str(repository["id"])}]
    inventory = InventoryJobs(state, references)
    inventory_id = f"native-inventory-{case}"
    inventory.create(
        inventory_id,
        {
            "source_instance_key": "source",
            "source_profile_revision": 1,
            "repositories": selected,
            "requested_classes": export_classes(metadata=False, members=False, lfs=True, wiki=True),
        },
    )
    with plan_worker.proxy(preserve_origin=True):
        job = inventory.run_next()
    if not job or job["status"] != "ready":
        raise RuntimeError("native inventory did not become ready")
    source = SourceExports(state, paths, references)
    observed = inventory.get(inventory_id)
    request = {
        "version": 3,
        "inventory_id": inventory_id,
        "inventory_digest": observed["document_digest"],
        "source_instance_key": "source",
        "source_profile_revision": 1,
        "recipient_instance_key": "target",
        "repositories": selected,
        "include_metadata": False,
        "include_members": False,
        "include_lfs": True,
        "include_wiki": True,
    }
    captured = source.prepare(request, export_id=f"native-export-{case}")
    with plan_worker.proxy(preserve_origin=True):
        captured = source.run(captured["export_id"])
    if captured["status"] != "captured":
        raise RuntimeError("native source export did not capture")
    archive = root / "native-payload.zip"
    write_package_archive(paths.package_directory / captured["export_id"], archive)
    return {
        "status": "passed",
        "side": "source",
        "case": case,
        "archive_name": archive.name,
        "inventory_status": job["status"],
        "payload_facts": facts,
    }


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "remote-events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _writes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Both the LFS download batch and Git upload-pack use read-only POSTs.
    return [
        event
        for event in events
        if event["method"] in ("PUT", "PATCH")
        or (
            event["method"] == "POST"
            and not event["path"].endswith(("/objects/batch", "/git-upload-pack"))
        )
    ]


def target(case: str, mode: str) -> dict[str, Any]:
    root = _root("target", case)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_isolated("target")
    token = _token(root, "target")
    context = _cert(root)
    state, paths, references = _profile_state(root, "target", context)
    os.environ["GITFERRY_WORK_DIR"] = str(paths.work_directory)
    archive = Path(f"/tmp/native-payload-{case}.zip")
    if not archive.is_file():
        raise RuntimeError("host did not provide the native payload archive")
    received = paths.package_directory / f"native-payload-{case}"
    if not received.exists():
        receive_package_archive(archive, received)
    if mode == "repackage":
        original = inspect_package(received)
        copied = received.with_name(received.name + "-repacked")
        if not copied.exists():
            shutil.copytree(received, copied)
            (copied / PACKAGE_NAME).unlink()
            (copied / DIGEST_NAME).unlink()
            document = deepcopy(original.document)
            document["package_id"] += "-repacked"
            write_package(copied, document)
        received = copied
    service = PackageAdmissions(state, paths)
    with OperationExecutor(state, "native-probe-restart"):
        pass  # The ordinary process lock/journal recovers abandoned writes.
    review = service.review(received.name, target_instance_key="target", profile_revision=1)
    service.admit(
        received.name,
        target_instance_key="target",
        profile_revision=1,
        expected_digest=review["digest_sha256"],
    )
    repacked_plan = None
    if mode == "repackage":
        with state.read_connection() as connection:
            old_operations = [
                tuple(row)
                for row in connection.execute(
                    "SELECT operation_id,intent_hash,status FROM operations ORDER BY operation_id"
                )
            ]
        try:
            reviewed = service.plan_review(
                received.name,
                target_instance_key="target",
                profile_revision=1,
                include_metadata=False,
                apply_members=False,
            )
            repacked_plan = service.prepare_plan(
                received.name,
                target_instance_key="target",
                profile_revision=1,
                expected_review_digest=reviewed["review_digest"],
                include_metadata=False,
                apply_members=False,
            )
            if case != "normal":
                raise RuntimeError("repackaging bypassed unresolved remote operation")
        except (OperationBlocked, PlanConflict, PackageAdmissionConflict):
            if case == "normal":
                raise
        with state.read_connection() as connection:
            new_operations = [
                tuple(row)
                for row in connection.execute(
                    "SELECT operation_id,intent_hash,status FROM operations ORDER BY operation_id"
                )
            ]
        if old_operations != new_operations:
            raise RuntimeError("repackaging changed operation identity or state")
    # An interrupted plan already owns its operations. Re-admission must not clear them.
    plan_file = root / "local-plan.json"
    if repacked_plan is not None:
        plan = repacked_plan
    elif plan_file.exists():
        plan_id = json.loads(plan_file.read_text())["plan_id"]
        plan = PlanService(state).get(plan_id)
    else:
        plan_review = service.plan_review(
            received.name,
            target_instance_key="target",
            profile_revision=1,
            include_metadata=False,
            apply_members=False,
        )
        plan = service.prepare_plan(
            received.name,
            target_instance_key="target",
            profile_revision=1,
            expected_review_digest=plan_review["review_digest"],
            include_metadata=False,
            apply_members=False,
        )
        with plan_file.open("w") as stream:
            json.dump({"plan_id": plan["plan_id"]}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    plans = PlanService(state)
    before_events = len(_events(root))

    def crash_after_lfs() -> None:
        current = plans.get(plan["plan_id"])
        if any(
            step["operation_type"] == "upload_lfs_object" and step["status"] == "executing"
            for step in current["operations"]
        ):
            with (root / "crash-after-remote.json").open("w") as stream:
                json.dump({"remote_returned_success": True, "local_status": "executing"}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os._exit(88)

    with plan_worker.proxy(
        preserve_origin=True, lose_lfs_response=(case == "lost" and mode == "target")
    ):
        if mode == "resolve":
            operation = next(
                step for step in plan["operations"] if step["operation_type"] == "upload_lfs_object"
            )
            resolve_native_operation(
                state,
                paths,
                operation_id=operation["operation_id"],
                references=references,
                evidence="Explicit synthetic target digest reconciliation",
            )
        runner = PlanRunner(
            plans,
            lambda _: GiteaPlanAdapter(state, paths, references),
            after_remote_hook=crash_after_lfs if case == "crash" and mode == "target" else None,
        )
        if plan["status"] != "confirmed":
            try:
                if plan["status"] in ("queued", "preflighting", "running"):
                    runner.run(plan["plan_id"])
                    plan = plans.get(plan["plan_id"])
                plans.request_preview(plan["plan_id"], plan["digest"])
                preview = runner.preview(plan["plan_id"])
                if preview["preview"]["status"] == "passed":
                    plans.queue(
                        plan["plan_id"],
                        plan["digest"],
                        preview_generation=preview["preview"]["generation"],
                    )
                    runner.run(plan["plan_id"])
            except (OperationBlocked, PlanConflict):
                if not (case in ("crash", "lost") and mode in ("replay", "repackage")):
                    raise
        result = plans.get(plan["plan_id"])
        package = service.artifact(received.name).document
        selected = package["repositories"][0]
        # All target IDs and expected digests come from this local journal/package.
        repository = _remote("target", "GET", "/repos/" + selected["repository"], token=token)
        pointer = LFSPointer(selected["lfs"][0]["oid"], selected["lfs"][0]["size"])
        with GiteaLFSClient(HTTPS, selected["repository"], ADMIN, token, verify=context) as lfs:
            lfs.verify_remote(pointer, max_bytes=1024 * 1024)
        wiki_verified = False
        if result["status"] == "confirmed":
            with (
                GiteaBundlePusher(
                    received,
                    paths.work_directory,
                    HTTPS,
                    ADMIN,
                    token,
                    ca_file=root / "trusted.pem",
                ) as git,
                GiteaWikiClient(HTTPS, username=ADMIN, token=token, verify=context) as wiki,
            ):
                page = wiki.get_page(selected["repository"], int(repository["id"]), "Home")
                content = base64.b64decode(page["content_base64"])
                refs = git._git_remote("ls-remote", git._wiki_url(selected["repository"]), None)
                if content != b"Second revision\n" or refs != selected["wiki"]["refs"]:
                    raise RuntimeError("target wiki differs from received native history")
                wiki_verified = True
    writes = _writes(_events(root)[before_events:])
    unknown = [step for step in result["operations"] if step["status"] == "outcome_unknown"]
    expected_unknown = case != "normal" and mode != "resolve"
    if expected_unknown:
        if len(unknown) != 1 or unknown[0]["operation_type"] != "upload_lfs_object":
            raise RuntimeError("native interruption was not persistently unresolved")
        if result["status"] == "confirmed":
            raise RuntimeError("unresolved native write reported successful plan")
    elif result["status"] != "confirmed":
        raise RuntimeError("native plan was not confirmed")
    if mode in ("replay", "repackage") and writes:
        raise RuntimeError("native restart replay performed another remote write")
    return {
        "status": "passed",
        "side": "target",
        "case": case,
        "mode": mode,
        "plan_status": result["status"],
        "plan_id": result["plan_id"],
        "operations": {step["operation_type"]: step["status"] for step in result["operations"]},
        "remote_writes": len(writes),
        "lfs_uploads": sum(
            event["method"] == "PUT" and "/info/lfs/objects/" in event["path"]
            for event in _events(root)
        ),
        "lfs_sha256": pointer.oid,
        "lfs_remote_bytes_verified": True,
        "wiki_refs_and_content_match": wiki_verified,
        "isolation_checked": True,
        "source_state_absent": not _root("source", case).exists(),
        "response_lost_marker": (root / "lfs-response-lost.json").exists(),
        "after_remote_crash_marker": (root / "crash-after-remote.json").exists(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "side",
        choices=(
            "source",
            "target",
            "replay",
            "resolve",
            "repackage",
            "return-export",
            "return-import",
            "negatives",
        ),
    )
    parser.add_argument("--case", choices=("normal", "crash", "lost"), default="normal")
    args = parser.parse_args()
    if args.side in ("return-export", "return-import"):
        from native_return_worker import run

        result = run("target" if args.side == "return-export" else "source")
    elif args.side == "negatives":
        from native_negative_worker import run

        result = run()
    else:
        result = source(args.case) if args.side == "source" else target(args.case, args.side)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
