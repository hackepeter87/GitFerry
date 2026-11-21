from __future__ import annotations

import hashlib
import subprocess
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest
from gitferry.content_inventory import InventoryError, inventory_digest, parse_inventory
from gitferry.gitea_bundles import GiteaBundleError
from gitferry.gitea_wiki import GiteaWikiError
from gitferry.native_inventory import observe_native_payloads
from test_content_inventory import _document


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _pointer(oid: str, size: int) -> str:
    return f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {size}\n"


def _history_repo(tmp_path: Path) -> Path:
    repository = tmp_path / "mirror.git"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.name", "Synthetic User", cwd=repository)
    _git("config", "user.email", "synthetic@example.invalid", cwd=repository)
    for payload in (b"first-lfs", b"second-lfs"):
        oid = hashlib.sha256(payload).hexdigest()
        (repository / "asset.pointer").write_text(_pointer(oid, len(payload)), encoding="utf-8")
        _git("add", "asset.pointer", cwd=repository)
        _git("commit", "-m", "synthetic pointer", cwd=repository)
    return repository


class _FakeGit:
    def __init__(
        self, mirror: Path, *, wiki_mirror: Path | None = None, error: Exception | None = None
    ):
        self.mirror = mirror
        self.wiki_mirror = wiki_mirror
        self.error = error

    @contextmanager
    def source_mirror(
        self, repository: str, expected_id: str, *, max_bytes: int, wiki: bool = False
    ):
        if self.error is not None:
            raise self.error
        yield self.wiki_mirror if wiki else self.mirror

    def _local_refs(self, mirror: Path) -> dict[str, str]:
        return {"refs/heads/main": "a" * 40} if self.wiki_mirror == mirror else {}


class _FakeLFS:
    def __init__(self) -> None:
        self.inspected: list[str] = []

    def __enter__(self) -> _FakeLFS:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def inspect(self, pointer) -> None:
        self.inspected.append(pointer.oid)


class _FakeWiki:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.parents: list[tuple[str, int]] = []

    def _wiki_parent(self, repository: str, expected_id: int) -> None:
        self.parents.append((repository, expected_id))
        if self.error is not None:
            raise self.error


def _observe(document: dict, git: _FakeGit, lfs: _FakeLFS, wiki: _FakeWiki):
    return observe_native_payloads(
        parse_inventory(document),
        git,
        lambda _: lfs,
        wiki,
        max_bytes=1024 * 1024,
    )


def test_native_observation_preserves_v1_input_and_finds_all_historical_pointers(
    tmp_path: Path,
) -> None:
    value = _document(requested_classes=["lfs", "wiki"])
    original = deepcopy(value)
    before_digest = inventory_digest(parse_inventory(value))
    lfs = _FakeLFS()
    observed = _observe(value, _FakeGit(_history_repo(tmp_path)), lfs, _FakeWiki())
    assert value == original
    assert inventory_digest(parse_inventory(value)) == before_digest
    lfs_observation = next(
        item for item in observed.repositories[0].classes if item.content_class == "lfs"
    )
    assert lfs_observation.status == "observed"
    assert lfs_observation.count == 2
    assert sorted(lfs_observation.objects[0].facts) == ["oid", "size"]
    assert len(lfs.inspected) == 2


def test_only_selected_classes_receive_new_native_facts(tmp_path: Path) -> None:
    value = _document(requested_classes=["lfs"])
    before_refs = next(
        item
        for item in parse_inventory(value).repositories[0].classes
        if item.content_class == "git_refs"
    )
    observed = _observe(value, _FakeGit(_history_repo(tmp_path)), _FakeLFS(), _FakeWiki())
    after_refs = next(
        item for item in observed.repositories[0].classes if item.content_class == "git_refs"
    )
    assert after_refs.model_dump(mode="json") == before_refs.model_dump(mode="json")


def test_missing_lfs_blocks_instead_of_claiming_zero(tmp_path: Path) -> None:
    value = _document(requested_classes=["lfs"])
    observed = _observe(
        value,
        _FakeGit(_history_repo(tmp_path), error=GiteaBundleError("source drift")),
        _FakeLFS(),
        _FakeWiki(),
    )
    item = next(item for item in observed.repositories[0].classes if item.content_class == "lfs")
    assert item.status == "blocked" and item.count is None and item.objects == []


def test_uninitialized_wiki_blocks_instead_of_claiming_empty(tmp_path: Path) -> None:
    value = _document(requested_classes=["wiki"])
    wiki = _FakeWiki(GiteaWikiError("uninitialized"))
    observed = _observe(value, _FakeGit(_history_repo(tmp_path)), _FakeLFS(), wiki)
    item = next(item for item in observed.repositories[0].classes if item.content_class == "wiki")
    assert item.status == "blocked" and item.count is None


def test_wiki_observation_keeps_exact_refs_and_parent_identity(tmp_path: Path) -> None:
    value = _document(requested_classes=["wiki"])
    wiki = _FakeWiki()
    mirror = tmp_path / "wiki.git"
    mirror.mkdir()
    observed = _observe(
        value, _FakeGit(_history_repo(tmp_path), wiki_mirror=mirror), _FakeLFS(), wiki
    )
    item = next(item for item in observed.repositories[0].classes if item.content_class == "wiki")
    assert item.status == "observed"
    assert item.objects[0].facts == {"state": "populated", "refs": {"refs/heads/main": "a" * 40}}
    assert wiki.parents == [("acme/one", 90071992547409931234567890)]


def test_source_postyield_drift_prevents_observed_facts(tmp_path: Path) -> None:
    value = _document(requested_classes=["lfs"])

    class DriftingGit(_FakeGit):
        @contextmanager
        def source_mirror(self, *args, **kwargs):
            yield self.mirror
            raise GiteaBundleError("post-yield drift")

    observed = _observe(value, DriftingGit(_history_repo(tmp_path)), _FakeLFS(), _FakeWiki())
    item = next(item for item in observed.repositories[0].classes if item.content_class == "lfs")
    assert item.status == "blocked" and item.count is None


def test_successful_zero_git_refs_yields_explicit_empty_wiki(tmp_path: Path) -> None:
    value = _document(requested_classes=["wiki"])
    wiki = _FakeWiki()
    observed = _observe(
        value, _FakeGit(_history_repo(tmp_path), wiki_mirror=None), _FakeLFS(), wiki
    )
    item = next(item for item in observed.repositories[0].classes if item.content_class == "wiki")
    assert item.status == "observed"
    assert item.objects[0].facts == {"state": "empty", "refs": {}}


@pytest.mark.parametrize("schema_version, comparison_version", [(3, 3), (2, 1)])
def test_inventory_versions_reject_mismatch_or_unknown(
    schema_version: int, comparison_version: int
) -> None:
    document = _document()
    document["schema_version"] = schema_version
    document["comparison_version"] = comparison_version
    with pytest.raises(InventoryError):
        parse_inventory(document)
