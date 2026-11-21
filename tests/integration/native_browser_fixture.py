"""Synthetic native payloads inside the existing desktop first-import fixture."""

from __future__ import annotations

import base64
import hashlib
import ssl
import tempfile
from pathlib import Path
from typing import Any

import httpx
from gitferry.gitea_bundles import GiteaBundlePusher
from gitferry.gitea_lfs import GiteaLFSClient
from gitferry.gitea_wiki import GiteaWikiClient
from gitferry.lfs_payloads import LFSPointer

HTTPS = "https://localhost:34443"
ADMIN = "gitferry-admin"


def seed(root: Path, token: str) -> None:
    context = ssl.create_default_context(cafile=str(root / "trusted.pem"))
    with tempfile.TemporaryDirectory(dir=root / "work", prefix="native-fixture-") as temporary:
        work = Path(temporary)
        with (
            GiteaBundlePusher(work, work, HTTPS, ADMIN, token, ca_file=root / "trusted.pem") as git,
            GiteaWikiClient(HTTPS, username=ADMIN, token=token, verify=context) as wiki,
            httpx.Client(
                base_url=HTTPS + "/api/v1",
                headers={"Authorization": "token " + token},
                verify=context,
                trust_env=False,
                timeout=20,
            ) as api,
        ):
            for name in ("selected", "selected-extra"):
                scope = "source-export-lab/" + name
                repository = api.get("/repos/" + scope)
                repository.raise_for_status()
                wiki.bootstrap(
                    scope,
                    repository.json()["id"],
                    "Home",
                    "Original wiki revision\n",
                    "Synthetic wiki",
                )
                response = api.patch(
                    "/repos/" + scope + "/wiki/page/Home",
                    json={
                        "title": "Home",
                        "content_base64": base64.b64encode(b"Offline native wiki\n").decode(),
                        "message": "Synthetic second wiki revision",
                    },
                )
                response.raise_for_status()
            scope = "source-export-lab/selected"
            checkout = work / "checkout"
            git._run_git(["clone", git._repository_url(scope), str(checkout)], cwd=None)
            git._run_git(["config", "user.name", "Synthetic LFS Author"], cwd=checkout)
            git._run_git(["config", "user.email", "lfs-author@example.invalid"], cwd=checkout)
            with GiteaLFSClient(HTTPS, scope, ADMIN, token, verify=context) as lfs:
                for revision in ("historical", "current"):
                    data = (revision.encode() + b" synthetic LFS payload\x00\xff\n") * 128
                    pointer = LFSPointer(hashlib.sha256(data).hexdigest(), len(data))
                    native = work / "native.bin"
                    native.write_bytes(data)
                    lfs.upload(pointer, native, max_bytes=1024 * 1024)
                    (checkout / "asset.bin").write_text(
                        "version https://git-lfs.github.com/spec/v1\n"
                        f"oid sha256:{pointer.oid}\nsize {pointer.size}\n"
                    )
                    git._run_git(["add", "asset.bin"], cwd=checkout)
                    git._run_git(
                        ["commit", "-m", "Synthetic " + revision + " pointer"], cwd=checkout
                    )
            git._run_git(["push", "origin", "HEAD"], cwd=checkout)


def verify(root: Path, package: Path, token: str, document: dict[str, Any]) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(root / "trusted.pem"))
    result: dict[str, Any] = {"lfs": {}, "wiki": {}}
    with (
        GiteaBundlePusher(
            package, root / "work", HTTPS, ADMIN, token, ca_file=root / "trusted.pem"
        ) as git,
        GiteaWikiClient(HTTPS, username=ADMIN, token=token, verify=context) as wiki,
    ):
        for selected in document["repositories"]:
            scope = selected["repository"]
            with GiteaLFSClient(HTTPS, scope, ADMIN, token, verify=context) as lfs:
                for value in selected["lfs"]:
                    pointer = LFSPointer(value["oid"], value["size"])
                    lfs.verify_remote(pointer, max_bytes=1024 * 1024)
                    result["lfs"][pointer.oid] = pointer.size
            parent = wiki.find_repository(scope)
            if parent is None:
                raise RuntimeError("desktop target repository missing")
            page = wiki.get_page(scope, parent["id"], "Home")
            if base64.b64decode(page["content_base64"]) != b"Offline native wiki\n":
                raise RuntimeError("desktop wiki native content differs")
            refs = git._git_remote("ls-remote", git._wiki_url(scope), None)
            if refs != selected["wiki"]["refs"]:
                raise RuntimeError("desktop target wiki refs differ")
            result["wiki"][scope] = refs
    if len(result["lfs"]) != 2 or len(result["wiki"]) != 2:
        raise RuntimeError("desktop native history evidence incomplete")
    return {"status": "passed", **result}
