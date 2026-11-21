from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from gitferry.git_bundle import create_git_bundle
from gitferry.gitea_bundles import GiteaBundleError, GiteaBundlePusher
from gitferry.provisioning import BundleIntent


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(  # noqa: S603, S607
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _source(tmp_path: Path) -> tuple[Path, BundleIntent]:
    source = tmp_path / "source"
    source.mkdir()
    _git("init", cwd=source)
    _git("config", "user.name", "Synthetic User", cwd=source)
    _git("config", "user.email", "synthetic@example.invalid", cwd=source)
    (source / "README").write_text("synthetic\n", encoding="utf-8")
    _git("add", "README", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    package = tmp_path / "package"
    package.mkdir()
    bundle = create_git_bundle(source, package, "repo.bundle")
    return source, BundleIntent(bundle.member, bundle.sha256, bundle.size)


def test_preflight_imports_once_and_preserves_refs(tmp_path: Path) -> None:
    _, intent = _source(tmp_path)
    pusher = GiteaBundlePusher(
        tmp_path / "package",
        tmp_path / "work",
        "https://target.invalid:3000",
        "synthetic-admin",
        "synthetic-token",
    )
    first = pusher.preflight(intent)
    second = pusher.preflight(intent)
    assert first is second
    refs = pusher._local_refs(first.destination)
    assert refs and all(name.startswith("refs/") for name in refs)


def test_context_manager_cleans_owned_import(tmp_path: Path) -> None:
    _, intent = _source(tmp_path)
    pusher = GiteaBundlePusher(
        tmp_path / "package",
        tmp_path / "work",
        "https://target.invalid:3000",
        "synthetic-admin",
        "synthetic-token",
    )
    with pusher:
        imported = pusher.preflight(intent)
        assert imported.destination.exists()
    assert not imported.destination.exists()


def test_push_rejects_divergent_existing_refs_without_write(tmp_path: Path, monkeypatch) -> None:
    _, intent = _source(tmp_path)
    pusher = GiteaBundlePusher(
        tmp_path / "package",
        tmp_path / "work",
        "https://target.invalid:3000",
        "synthetic-admin",
        "synthetic-token",
    )
    imported = pusher.preflight(intent)
    expected = pusher._local_refs(imported.destination)
    calls: list[str] = []

    def fake_remote(command: str, url: str, cwd: Path | None) -> dict[str, str]:
        calls.append(command)
        return {**expected, "refs/heads/other": "0" * 40}

    monkeypatch.setattr(pusher, "_git_remote", fake_remote)
    with pytest.raises(GiteaBundleError, match="divergent refs"):
        pusher.check_remote("org/repo", bundle=intent, expected_empty=False)
    assert calls == ["ls-remote"]


def test_constructor_rejects_unsafe_roots_and_urls(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    with pytest.raises(GiteaBundleError):
        GiteaBundlePusher(package, tmp_path / "work", "file:///tmp/target", "u", "t")


def test_push_uses_imported_object_store_and_exact_replay(tmp_path: Path, monkeypatch) -> None:
    _, bundle = _source(tmp_path)
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    with GiteaBundlePusher(
        tmp_path / "package", tmp_path / "work", "https://target.invalid", "actor", "synthetic"
    ) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        checked = []
        monkeypatch.setattr(
            pusher,
            "_verify_repository_id",
            lambda repository, expected: checked.append((repository, expected)),
        )
        result = pusher.push(
            "org/repo",
            bundle_member=bundle.member,
            bundle_sha256=bundle.sha256,
            bundle_size=bundle.size,
            expected_repository_id="15",
        )
        assert result.target_global_id == "15"
        assert checked == [("org/repo", "15")]
        pusher.check_remote("org/repo", bundle=bundle, expected_empty=False)
        assert (
            pusher.push(
                "org/repo",
                bundle_member=bundle.member,
                bundle_sha256=bundle.sha256,
                bundle_size=bundle.size,
                expected_repository_id="15",
            )
            == result
        )
