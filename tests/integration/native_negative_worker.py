"""Live source-only qualification for unsupported native payload cases."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import plan_worker
from gitferry.application_package import PackageError, inspect_package
from gitferry.connection_probe import LocalReferences
from gitferry.content_inventory import export_classes
from gitferry.gitea_bundles import GiteaBundlePusher
from gitferry.inventory_jobs import InventoryJobs
from gitferry.package_archive import receive_package_archive
from gitferry.source_exports import SourceExportConflict, SourceExports
from native_payload_worker import (
    ADMIN,
    HTTPS,
    _cert,
    _git,
    _profile_state,
    _remote,
    _root,
    _token,
    _writes,
)


def _create_repository(token: str, name: str, *, has_wiki: bool = True) -> dict[str, Any]:
    created = _remote(
        "source",
        "POST",
        "/orgs/native-payload-normal/repos",
        token=token,
        json={"name": name, "private": True, "auto_init": False, "has_wiki": has_wiki},
    )

    _remote(
        "source",
        "PATCH",
        "/repos/native-payload-normal/" + name,
        token=token,
        json={"has_wiki": has_wiki},
    )
    return created


def _seed_missing_lfs(root: Path, token: str, repository: dict[str, Any]) -> None:
    scope = f"native-payload-normal/{repository['name']}"
    work = root / "work" / "negative-missing-lfs"
    work.mkdir(parents=True, exist_ok=True)
    local = work / "repo"
    local.mkdir()
    _git("init", "-b", "main", cwd=local)
    _git("config", "user.name", "Synthetic Negative Author", cwd=local)
    _git("config", "user.email", "negative-author@example.invalid", cwd=local)
    oid = "e" * 64
    (local / "missing.bin").write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize 32\n",
        encoding="ascii",
    )
    _git("add", ".", cwd=local)
    _git("commit", "-m", "Synthetic unavailable LFS pointer", cwd=local)
    with (
        plan_worker.proxy(preserve_origin=True),
        GiteaBundlePusher(work, work, HTTPS, ADMIN, token, ca_file=root / "trusted.pem") as git,
    ):
        git._run_git(["push", git._repository_url(scope), "main"], cwd=local)


def _observe(
    state: Any,
    references: LocalReferences,
    repository: dict[str, Any],
    classes: list[str],
    inventory_id: str,
) -> dict[str, Any]:
    jobs = InventoryJobs(state, references)
    jobs.create(
        inventory_id,
        {
            "source_instance_key": "source",
            "source_profile_revision": 1,
            "repositories": [
                {
                    "repository": f"native-payload-normal/{repository['name']}",
                    "global_id": str(repository["id"]),
                }
            ],
            "requested_classes": classes,
        },
    )
    before = len(_writes(_events(jobs.state.path.parent)))
    with plan_worker.proxy(preserve_origin=True):
        job = jobs.run_next()
    after = len(_writes(_events(jobs.state.path.parent)))
    job["source_writes_during_observation"] = after - before
    return job


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "remote-events.jsonl"
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )


def _prepare_rejects(
    state: Any,
    paths: Any,
    references: LocalReferences,
    job: dict[str, Any],
    repository: dict[str, Any],
    *,
    include_lfs: bool,
    include_wiki: bool,
) -> bool:
    source = SourceExports(state, paths, references)
    request = {
        "version": 3,
        "inventory_id": job["inventory_id"],
        "inventory_digest": job["document_digest"],
        "source_instance_key": "source",
        "source_profile_revision": 1,
        "recipient_instance_key": "target",
        "repositories": [
            {
                "repository": f"native-payload-normal/{repository['name']}",
                "global_id": str(repository["id"]),
            }
        ],
        "include_metadata": False,
        "include_members": False,
        "include_lfs": include_lfs,
        "include_wiki": include_wiki,
    }
    try:
        source.prepare(request, export_id="negative-" + repository["name"])
    except SourceExportConflict:
        return True
    return False


def _package_corruption(root: Path) -> dict[str, bool]:
    original = _root("source", "normal") / "native-payload.zip"
    copied = root / "work" / "negative-package.zip"
    shutil.copyfile(original, copied)
    extracted = root / "work" / "negative-package"
    artifact = receive_package_archive(copied, extracted)
    lfs_path = next(
        Path(repository["lfs"][0]["path"])
        for repository in artifact.document["repositories"]
        if repository.get("lfs")
    )
    missing = root / "work" / "negative-package-missing"
    shutil.copytree(extracted, missing)
    (missing / lfs_path).unlink()
    try:
        inspect_package(missing)
    except PackageError:
        missing_rejected = True
    else:
        missing_rejected = False
    corrupt = root / "work" / "negative-package-corrupt"
    shutil.copytree(extracted, corrupt)
    corrupt_path = corrupt / lfs_path
    payload = corrupt_path.read_bytes()
    corrupt_path.write_bytes(bytes((payload[0] ^ 1,)) + payload[1:])
    try:
        inspect_package(corrupt)
    except PackageError:
        corrupt_rejected = True
    else:
        corrupt_rejected = False
    return {
        "missing_lfs_payload_rejected": missing_rejected,
        "corrupt_lfs_payload_rejected": corrupt_rejected,
    }


def run() -> dict[str, Any]:
    root = _root("source", "normal")
    token = _token(root, "source")
    context = _cert(root)
    state, paths, references = _profile_state(root, "source", context)
    missing = _create_repository(token, "missing-lfs")
    empty = _create_repository(token, "empty-wiki", has_wiki=True)
    disabled = _create_repository(token, "disabled-wiki", has_wiki=False)
    _seed_missing_lfs(root, token, missing)
    missing_job = _observe(
        state,
        references,
        missing,
        export_classes(metadata=False, members=False, lfs=True),
        "native-negative-missing-lfs",
    )
    empty_job = _observe(
        state,
        references,
        empty,
        export_classes(metadata=False, members=False, wiki=True),
        "native-negative-empty-wiki",
    )
    disabled_job = _observe(
        state,
        references,
        disabled,
        export_classes(metadata=False, members=False, wiki=True),
        "native-negative-disabled-wiki",
    )
    package_results = _package_corruption(root)
    missing_class = next(
        item
        for item in missing_job["document"]["repositories"][0]["classes"]
        if item["content_class"] == "lfs"
    )
    disabled_class = next(
        item
        for item in disabled_job["document"]["repositories"][0]["classes"]
        if item["content_class"] == "wiki"
    )
    empty_class = next(
        item
        for item in empty_job["document"]["repositories"][0]["classes"]
        if item["content_class"] == "wiki"
    )
    assertions = {
        "missing_lfs_is_blocked_unknown": missing_job["status"] == "blocked"
        and missing_class["status"] == "blocked"
        and missing_class["count"] is None,
        "missing_lfs_export_refuses_blocked_inventory": _prepare_rejects(
            state, paths, references, missing_job, missing, include_lfs=True, include_wiki=False
        ),
        "uninitialized_wiki_blocks_without_false_empty_claim": empty_job["status"] == "blocked"
        and empty_class["status"] == "blocked"
        and empty_class["count"] is None
        and _prepare_rejects(
            state, paths, references, empty_job, empty, include_lfs=False, include_wiki=True
        ),
        "disabled_wiki_is_blocked_unknown": disabled_job["status"] == "blocked"
        and disabled_class["status"] == "blocked"
        and disabled_class["count"] is None
        and _prepare_rejects(
            state, paths, references, disabled_job, disabled, include_lfs=False, include_wiki=True
        ),
        **package_results,
        "source_observation_is_write_free": all(
            job["source_writes_during_observation"] == 0
            for job in (missing_job, empty_job, disabled_job)
        ),
    }
    if not all(assertions.values()):
        raise RuntimeError("native negative qualification failed")
    return {
        "status": "passed",
        "assertions": assertions,
        "observations": {
            "missing_lfs": missing_job["status"],
            "empty_wiki": empty_job["status"],
            "disabled_wiki": disabled_job["status"],
        },
    }
