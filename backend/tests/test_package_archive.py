from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import gitferry.package_archive as package_archive
import pytest


def _artifact(directory: Path) -> package_archive.PackageArtifact:
    files = {
        path.relative_to(directory).as_posix(): (
            path.stat().st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in directory.rglob("*")
        if path.is_file()
    }
    return package_archive.PackageArtifact(directory, "a" * 64, {}, files)


def test_archive_round_trip_streams_declared_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "package"
    source.mkdir()
    (source / "package.json").write_bytes(b"descriptor")
    (source / "package.sha256").write_bytes(b"digest\n")
    (source / "payload.bin").write_bytes(b"payload\x00bytes")
    monkeypatch.setattr(package_archive, "inspect_package", _artifact)

    archive = tmp_path / "package.zip"
    package_archive.write_package_archive(source, archive)
    received = package_archive.receive_package_archive(archive, tmp_path / "received")

    assert received.files == _artifact(tmp_path / "received").files
    assert (tmp_path / "received" / "payload.bin").read_bytes() == b"payload\x00bytes"


def test_writer_rechecks_source_bytes_after_inspection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "package"
    source.mkdir()
    (source / "package.json").write_bytes(b"descriptor")
    (source / "package.sha256").write_bytes(b"digest\n")
    payload = source / "payload.bin"
    payload.write_bytes(b"before")

    def inspect_then_change(directory: Path) -> package_archive.PackageArtifact:
        artifact = _artifact(directory)
        payload.write_bytes(b"after")
        return artifact

    monkeypatch.setattr(package_archive, "inspect_package", inspect_then_change)
    with pytest.raises(package_archive.PackageError, match="changed while archiving"):
        package_archive.write_package_archive(source, tmp_path / "package.zip")
    assert not (tmp_path / "package.zip").exists()
    assert not list(tmp_path.glob(".package.zip.*.part"))


def test_receiver_rejects_compressed_members_without_publishing(tmp_path: Path) -> None:
    archive = tmp_path / "compressed.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("package.json", b"descriptor")

    with pytest.raises(package_archive.PackageError):
        package_archive.receive_package_archive(archive, tmp_path / "received")
    assert not (tmp_path / "received").exists()
    assert not list(tmp_path.glob(".received.*"))


def test_receiver_preserves_existing_destination(tmp_path: Path) -> None:
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as handle:
        handle.writestr("package.json", b"descriptor")
    destination = tmp_path / "received"
    destination.mkdir()
    (destination / "keep").write_bytes(b"keep")

    with pytest.raises(package_archive.PackageError):
        package_archive.receive_package_archive(archive, destination)
    assert (destination / "keep").read_bytes() == b"keep"


@pytest.mark.parametrize("member", ["../escape", "/absolute", "a\\b", "C:drive"])
def test_receiver_rejects_unsafe_member_names(member: str, tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as handle:
        handle.writestr(member, b"x")

    with pytest.raises(package_archive.PackageError):
        package_archive.receive_package_archive(archive, tmp_path / "received")
    assert not (tmp_path / "received").exists()


@pytest.mark.parametrize(
    "kind", ["duplicate", "symlink", "directory", "nul", "limit", "count", "corrupt"]
)
def test_untrusted_archive_rejected_before_publication(
    kind: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import warnings

    archive = tmp_path / "unsafe.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as handle:
            info = zipfile.ZipInfo("payload")
            if kind == "symlink":
                info.create_system = 3
                info.external_attr = 0o120777 << 16
            elif kind == "directory":
                info = zipfile.ZipInfo("payload/")
            handle.writestr(info, b"payload")
            if kind == "duplicate":
                handle.writestr("payload", b"other")
            elif kind == "count":
                handle.writestr("other", b"other")
    if kind == "nul":
        archive.write_bytes(archive.read_bytes().replace(b"payload", b"pay\x00oad"))
    elif kind == "corrupt":
        archive.write_bytes(b"not a zip archive")
    monkeypatch.setattr(package_archive, "inspect_package", _artifact)
    with pytest.raises(package_archive.PackageError):
        package_archive.receive_package_archive(
            archive,
            tmp_path / "received",
            max_bytes=1 if kind == "limit" else 64 * 1024**3,
            max_files=1 if kind == "count" else 10000,
        )
    assert not (tmp_path / "received").exists()
    assert not list(tmp_path.glob(".received.*"))


def test_atomic_publication_does_not_replace_concurrent_empty_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_bytes(b"data")
    archive = tmp_path / "package.zip"
    monkeypatch.setattr(package_archive, "inspect_package", _artifact)
    package_archive.write_package_archive(source, archive)
    destination = tmp_path / "received"

    def competing_receive(directory: Path) -> package_archive.PackageArtifact:
        destination.mkdir()
        return _artifact(directory)

    monkeypatch.setattr(package_archive, "inspect_package", competing_receive)
    with pytest.raises(package_archive.PackageError):
        package_archive.receive_package_archive(archive, destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".received.*"))


def test_central_directory_limit_checked_before_zip_index_allocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / "oversized-index.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        info = zipfile.ZipInfo("payload")
        info.comment = b"x" * 2048
        handle.writestr(info, b"data")

    def unexpected_index(*args: object, **kwargs: object) -> None:
        pytest.fail("ZipFile index must not be built before its central-directory limit is checked")

    monkeypatch.setattr(package_archive.zipfile, "ZipFile", unexpected_index)
    with pytest.raises(package_archive.PackageError, match="central directory"):
        package_archive.receive_package_archive(archive, tmp_path / "received", max_files=1)


def test_archive_output_cannot_add_an_undeclared_file_to_its_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "package"
    source.mkdir()
    (source / "payload").write_bytes(b"data")
    monkeypatch.setattr(package_archive, "inspect_package", _artifact)
    with pytest.raises(package_archive.PackageError, match="outside"):
        package_archive.write_package_archive(source, source / "export.zip")
    assert [path.name for path in source.iterdir()] == ["payload"]


def test_writer_rejects_replaced_fifo_without_waiting_for_a_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    source = tmp_path / "package"
    source.mkdir()
    payload = source / "payload"
    payload.write_bytes(b"original")

    def replace_payload(directory: Path) -> package_archive.PackageArtifact:
        artifact = _artifact(directory)
        payload.unlink()
        os.mkfifo(payload)
        return artifact

    monkeypatch.setattr(package_archive, "inspect_package", replace_payload)
    with pytest.raises(package_archive.PackageError, match="changed while archiving"):
        package_archive.write_package_archive(source, tmp_path / "archive.zip")
    assert not (tmp_path / "archive.zip").exists()
    assert not list(tmp_path.glob(".archive.zip.*.part"))
