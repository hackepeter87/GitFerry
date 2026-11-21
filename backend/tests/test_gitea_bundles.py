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


def _remote_source(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    source = tmp_path / "source-export"
    source.mkdir()
    _git("init", cwd=source)
    _git("config", "user.name", "Synthetic User", cwd=source)
    _git("config", "user.email", "synthetic@example.invalid", cwd=source)
    (source / "README").write_text("synthetic export\n", encoding="utf-8")
    _git("add", "README", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    _git("branch", "release", cwd=source)
    _git("tag", "-a", "v1", "-m", "version one", cwd=source)
    remote = tmp_path / "remote-export.git"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    _git("remote", "add", "origin", str(remote), cwd=source)
    _git("push", "origin", "--all", cwd=source)
    _git("push", "origin", "--tags", cwd=source)
    refs = {
        line.split("\t", 1)[1]: line.split("\t", 1)[0]
        for line in subprocess.run(  # noqa: S603
            ["git", "ls-remote", "--refs", str(remote)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }
    return source, remote, refs


def _export_pusher(tmp_path: Path) -> GiteaBundlePusher:
    package = tmp_path / "package-export"
    package.mkdir()
    return GiteaBundlePusher(
        package,
        tmp_path / "work-export",
        "https://target.invalid",
        "synthetic-admin",
        "synthetic-token",
    )


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


def test_export_captures_mirror_refs_in_selected_member(tmp_path: Path, monkeypatch) -> None:
    _, remote, refs = _remote_source(tmp_path)
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        monkeypatch.setattr(pusher, "_verify_repository_id", lambda *_: None)
        result = pusher.export_bundle(
            "org/repo",
            member="repositories/org/repo.bundle",
            expected_repository_id="42",
        )
        assert result is not None
        assert result.member == "repositories/org/repo.bundle"
        assert result.path == tmp_path / "package-export/repositories/org/repo.bundle"
        assert result.path.is_file()
        assert pusher._git_remote("ls-remote", str(remote), None) == refs


def test_export_ref_drift_refuses_and_publishes_no_bundle(tmp_path: Path, monkeypatch) -> None:
    _, remote, refs = _remote_source(tmp_path)
    drift = {**refs, "refs/heads/drift": "0" * 40}
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        monkeypatch.setattr(pusher, "_verify_repository_id", lambda *_: None)
        reads = iter([refs, drift])
        monkeypatch.setattr(pusher, "_git_remote", lambda *_: next(reads))
        with pytest.raises(GiteaBundleError, match="refs changed"):
            pusher.export_bundle("org/repo", member="repo.bundle", expected_repository_id="42")
        assert not (tmp_path / "package-export/repo.bundle").exists()


def test_export_identity_drift_removes_new_bundle(tmp_path: Path, monkeypatch) -> None:
    _, remote, refs = _remote_source(tmp_path)
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        identities = iter([None, GiteaBundleError("identity drift")])

        def verify(*_: object) -> None:
            outcome = next(identities)
            if isinstance(outcome, Exception):
                raise outcome

        monkeypatch.setattr(pusher, "_verify_repository_id", verify)
        monkeypatch.setattr(pusher, "_git_remote", lambda *_: refs)
        with pytest.raises(GiteaBundleError, match="identity drift"):
            pusher.export_bundle("org/repo", member="repo.bundle", expected_repository_id="42")
        assert not (tmp_path / "package-export/repo.bundle").exists()


def test_export_empty_source_returns_no_bundle_after_two_empty_reads(
    tmp_path: Path, monkeypatch
) -> None:
    remote = tmp_path / "empty.git"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        identity_calls: list[str] = []
        monkeypatch.setattr(
            pusher,
            "_verify_repository_id",
            lambda *_: identity_calls.append("verified"),
        )
        result = pusher.export_bundle(
            "org/empty", member="empty.bundle", expected_repository_id="7"
        )
        assert result is None
        assert identity_calls == ["verified", "verified", "verified"]
        assert not (tmp_path / "package-export/empty.bundle").exists()


def test_export_rejects_completed_clone_over_limit_before_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, remote, _ = _remote_source(tmp_path)
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        monkeypatch.setattr(pusher, "_verify_repository_id", lambda *_: None)
        with pytest.raises(GiteaBundleError, match="clone exceeds"):
            pusher.export_bundle(
                "org/repo", member="repo.bundle", expected_repository_id="42", max_bytes=1
            )
        assert not (tmp_path / "package-export/repo.bundle").exists()
        assert not list(pusher.work_root.rglob("gitferry-source-*"))


def test_source_mirror_uses_explicit_wiki_url_and_empty_is_none(
    tmp_path: Path, monkeypatch
) -> None:
    pusher = _export_pusher(tmp_path)
    urls: list[str] = []
    identities: list[str] = []
    monkeypatch.setattr(
        pusher,
        "_verify_repository_id",
        lambda repository, expected: identities.append(repository),
    )

    def empty_remote(command: str, url: str, cwd: Path | None) -> dict[str, str]:
        urls.append(url)
        return {}

    monkeypatch.setattr(pusher, "_git_remote", empty_remote)
    with pusher.source_mirror("org/repo", "42", wiki=True) as mirror:
        assert mirror is None
    assert urls == ["https://target.invalid/org/repo.wiki.git"] * 3
    assert identities == ["org/repo"] * 3


def test_push_wiki_bundle_uses_leases_and_rejects_stale_expected_refs(
    tmp_path: Path, monkeypatch
) -> None:
    _, bundle = _source(tmp_path)
    remote = tmp_path / "wiki.git"
    _git("init", "--bare", str(remote), cwd=tmp_path)
    with GiteaBundlePusher(
        tmp_path / "package", tmp_path / "work", "https://target.invalid", "actor", "synthetic"
    ) as pusher:
        monkeypatch.setattr(pusher, "_wiki_url", lambda _: str(remote))
        identities: list[str] = []
        monkeypatch.setattr(
            pusher,
            "_verify_repository_id",
            lambda repository, _: identities.append(repository),
        )
        captured: list[list[str]] = []
        original_run = pusher._run_git

        def capture(args: list[str], *, cwd: Path | None):
            captured.append(args)
            return original_run(args, cwd=cwd)

        monkeypatch.setattr(pusher, "_run_git", capture)
        result = pusher.push_wiki_bundle(
            "org/repo", bundle.member, bundle.sha256, bundle.size, "42", {}
        )
        assert result.observed_state and result.observed_state["refs"]
        push_args = next(args for args in captured if args[:2] == ["push", "--atomic"])
        assert any(
            value.startswith("--force-with-lease=refs/heads/") for value in push_args
        )
        assert identities == ["org/repo", "org/repo"]
        with pytest.raises(GiteaBundleError, match="expected state"):
            pusher.push_wiki_bundle(
                "org/repo", bundle.member, bundle.sha256, bundle.size, "42", {}
            )


def test_source_mirror_post_yield_drift_removes_exported_bundle_and_clone(
    tmp_path: Path, monkeypatch
) -> None:
    _, remote, refs = _remote_source(tmp_path)
    drift = {**refs, "refs/heads/drift": "0" * 40}
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        monkeypatch.setattr(pusher, "_verify_repository_id", lambda *_: None)
        reads = iter([refs, refs, drift])
        monkeypatch.setattr(pusher, "_git_remote", lambda *_: next(reads))
        with pytest.raises(GiteaBundleError, match="refs changed"):
            pusher.export_bundle("org/repo", member="repo.bundle", expected_repository_id="42")
        assert not (tmp_path / "package-export/repo.bundle").exists()
        assert not list(pusher.work_root.rglob("gitferry-source-*"))


def test_source_mirror_consumer_failure_cleans_clone(tmp_path: Path, monkeypatch) -> None:
    _, remote, refs = _remote_source(tmp_path)
    with _export_pusher(tmp_path) as pusher:
        monkeypatch.setattr(pusher, "_repository_url", lambda _: str(remote))
        monkeypatch.setattr(pusher, "_verify_repository_id", lambda *_: None)
        monkeypatch.setattr(pusher, "_git_remote", lambda *_: refs)
        with pytest.raises(RuntimeError, match="consumer failure"):
            with pusher.source_mirror("org/repo", "42") as mirror:
                assert mirror is not None and mirror.exists()
                mirror_path = mirror
                raise RuntimeError("consumer failure")
        assert not mirror_path.exists()


def test_push_wiki_bundle_rejects_disappearing_ref_and_rechecks_identity(
    tmp_path: Path, monkeypatch
) -> None:
    _, bundle = _source(tmp_path)
    pusher = GiteaBundlePusher(
        tmp_path / "package", tmp_path / "work", "https://target.invalid", "actor", "synthetic"
    )
    imported = pusher.preflight(bundle)
    expected = pusher._local_refs(imported.destination)
    monkeypatch.setattr(pusher, "_wiki_url", lambda _: "https://target.invalid/org/repo.wiki.git")
    identities: list[str] = []
    monkeypatch.setattr(
        pusher, "_verify_repository_id", lambda repository, _: identities.append(repository)
    )
    reads = iter([expected, {}])
    monkeypatch.setattr(pusher, "_git_remote", lambda *_: next(reads))
    monkeypatch.setattr(pusher, "_local_refs", lambda _: expected)
    monkeypatch.setattr(pusher, "_run_git", lambda *_args, **_kwargs: None)
    with pytest.raises(GiteaBundleError, match="after push"):
        pusher.push_wiki_bundle(
            "org/repo", bundle.member, bundle.sha256, bundle.size, "42", expected
        )
    assert identities == ["org/repo"]
