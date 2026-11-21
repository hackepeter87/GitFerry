# ruff: noqa: S603, S607

import subprocess
from pathlib import Path

import gitferry.git_bundle as git_bundle_module
import pytest
from gitferry.git_bundle import GitBundleError, create_git_bundle, import_git_bundle


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir()
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.name", "Synthetic Developer", cwd=path)
    _git("config", "user.email", "developer@example.invalid", cwd=path)
    (path / "README.md").write_text("synthetic\n", encoding="utf-8")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "initial", cwd=path)
    _git("branch", "feature/offline-proof", cwd=path)
    _git("tag", "v1.0.0", cwd=path)
    return path


def test_bundle_roundtrip_preserves_refs(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bundle = create_git_bundle(source, package, "repositories/org/repo.bundle")
    imported = import_git_bundle(
        package,
        bundle.member,
        bundle.sha256,
        bundle.size,
        work,
        "imports/repo.git",
    )
    assert _git("show-ref", cwd=source) == _git("show-ref", cwd=imported.destination)


def test_tampered_bundle_digest_and_content_are_rejected(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bundle = create_git_bundle(source, package, "repo.bundle")
    data = bytearray(bundle.path.read_bytes())
    data[-1] ^= 0xFF
    bundle.path.write_bytes(data)
    with pytest.raises(GitBundleError, match="integrity"):
        import_git_bundle(package, bundle.member, bundle.sha256, bundle.size, work, "repo.git")


def test_path_escape_and_symlinks_are_rejected(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    with pytest.raises(ValueError):
        create_git_bundle(source, package, "../escape.bundle")
    symlink = tmp_path / "source-link"
    symlink.symlink_to(source, target_is_directory=True)
    with pytest.raises(GitBundleError, match="regular directory"):
        create_git_bundle(symlink, package, "repo.bundle")


def test_bundle_member_symlink_is_rejected(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    real_bundle = create_git_bundle(source, package, "real.bundle")
    work = tmp_path / "work"
    work.mkdir()
    (package / "linked.bundle").symlink_to(real_bundle.path)
    with pytest.raises(GitBundleError, match="symlink"):
        import_git_bundle(
            package,
            "linked.bundle",
            real_bundle.sha256,
            real_bundle.size,
            work,
            "repo.git",
        )


def test_non_empty_destination_is_rejected(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bundle = create_git_bundle(source, package, "repo.bundle")
    destination = work / "repo.git"
    destination.mkdir()
    (destination / "existing").write_text("occupied", encoding="utf-8")
    with pytest.raises(GitBundleError, match="must not exist"):
        import_git_bundle(package, bundle.member, bundle.sha256, bundle.size, work, "repo.git")


def test_import_verification_does_not_depend_on_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    bundle = create_git_bundle(source, package, "repo.bundle")
    outside_repository = tmp_path / "outside"
    outside_repository.mkdir()
    monkeypatch.chdir(outside_repository)
    imported = import_git_bundle(
        package, bundle.member, bundle.sha256, bundle.size, work, "repo.git"
    )
    assert imported.destination.is_dir()


def test_create_does_not_clobber_existing_package_artifact(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    existing = package / "repo.bundle"
    existing.write_bytes(b"existing artifact")
    with pytest.raises(GitBundleError, match="already exists"):
        create_git_bundle(source, package, "repo.bundle")
    assert existing.read_bytes() == b"existing artifact"


def test_create_rejects_target_that_appears_during_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    target = package / "repo.bundle"
    real_link = git_bundle_module.os.link

    def racing_link(source_path: Path, destination: Path, *, follow_symlinks: bool) -> None:
        target.write_bytes(b"competing artifact")
        real_link(source_path, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(git_bundle_module.os, "link", racing_link)
    with pytest.raises(GitBundleError, match="appeared during publication"):
        create_git_bundle(source, package, "repo.bundle")
    assert target.read_bytes() == b"competing artifact"


def test_max_bytes_is_positive_and_bound_is_enforced(tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()
    with pytest.raises(GitBundleError, match="positive"):
        create_git_bundle(source, package, "repo.bundle", max_bytes=0)
    with pytest.raises(GitBundleError, match="exceeds"):
        create_git_bundle(source, package, "repo.bundle", max_bytes=1)


def test_bundle_creation_timeout_removes_partial_output(tmp_path: Path, monkeypatch) -> None:
    source = _repository(tmp_path / "source")
    package = tmp_path / "package"
    package.mkdir()

    def timeout(args, **kwargs):
        assert kwargs["timeout"] == 60
        Path(args[3]).write_bytes(b"partial synthetic bundle")
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(git_bundle_module.subprocess, "run", timeout)
    with pytest.raises(GitBundleError, match="operation failed"):
        create_git_bundle(source, package, "repo.bundle")
    assert not list(package.iterdir())
