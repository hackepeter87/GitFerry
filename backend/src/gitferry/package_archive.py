"""Bounded, integrity checked offline package archives.

The archive is deliberately a transport envelope only.  Package contents are
validated as a normal package before publication and are never trusted merely
because they came from a ZIP file.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from gitferry.application_package import (
    PackageArtifact,
    PackageError,
    inspect_package,
    package_path,
)

PACKAGE_NAME = "package.json"
PACKAGE_DIGEST_NAME = "package.sha256"
_CHUNK = 1024 * 1024


def _safe_name(name: str) -> str:
    try:
        path = package_path(name)
    except (PackageError, TypeError) as exc:
        raise PackageError(f"unsafe package path: {name!r}") from exc
    if str(path) != name:
        raise PackageError(f"non-canonical package path: {name!r}")
    return name


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_file(staging: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise PackageError("archive destination already exists")
    try:
        os.link(staging, destination)
    except FileExistsError as exc:
        raise PackageError("archive destination already exists") from exc
    staging.unlink()
    _sync_directory(destination.parent)


def _rename_noreplace(staging: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing a concurrent target."""
    import ctypes

    sys_platform = os.uname().sysname
    libc = ctypes.CDLL(None, use_errno=True)
    if sys_platform == "Linux":
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise PackageError("atomic no-replace directory publication is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    elif sys_platform == "Darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise PackageError("atomic no-replace directory publication is unavailable")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(os.fsencode(staging), os.fsencode(destination), 4)
    else:
        raise PackageError("atomic no-replace directory publication is unsupported")
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise PackageError("package destination already exists")
        raise OSError(error, os.strerror(error))


def write_package_archive(directory: Path, destination: Path) -> None:
    """Stream a validated package directory into an uncompressed ZIP archive."""
    artifact = inspect_package(directory)
    if destination.resolve().is_relative_to(directory.resolve()):
        raise PackageError("archive destination must be outside the source package")
    if destination.exists() or destination.is_symlink():
        raise PackageError("archive destination already exists")
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.part"
    try:
        descriptor = os.open(staging, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        with (
            os.fdopen(descriptor, "w+b") as stream,
            zipfile.ZipFile(
                stream, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as archive,
        ):
            for name in sorted(artifact.files):
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED
                size = 0
                digest = hashlib.sha256()
                with (
                    (artifact.directory / PurePosixPath(name)).open("rb") as source,
                    archive.open(info, "w", force_zip64=True) as target,
                ):
                    for chunk in iter(lambda: source.read(_CHUNK), b""):
                        size += len(chunk)
                        digest.update(chunk)
                        target.write(chunk)
                if (size, digest.hexdigest()) != artifact.files[name]:
                    raise PackageError(f"package file changed while archiving: {name}")
            archive.close()
            stream.flush()
            os.fsync(stream.fileno())
        _publish_file(staging, destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def _extract_member(
    source: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, limit: int, used: list[int]
) -> None:
    name = _safe_name(info.orig_filename)
    if info.is_dir() or info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 0x1:
        raise PackageError(f"unsafe ZIP member: {name}")
    mode = (info.external_attr >> 16) & 0o170000
    if mode and mode != 0o100000:
        raise PackageError(f"ZIP member is not a regular file: {name}")
    path = target / PurePosixPath(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with source.open(info, "r") as src, path.open("xb") as dst:
            for chunk in iter(lambda: src.read(_CHUNK), b""):
                count += len(chunk)
                used[0] += len(chunk)
                if count > info.file_size or used[0] > limit:
                    raise PackageError("archive exceeds byte limit")
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if count != info.file_size:
        raise PackageError(f"ZIP member size mismatch: {name}")


def receive_package_archive(
    source: Path, destination: Path, *, max_bytes: int = 64 * 1024**3, max_files: int = 10000
) -> PackageArtifact:
    """Safely receive a stored ZIP archive into a new package directory."""
    if max_bytes < 0 or max_files < 1:
        raise PackageError("archive limits must be positive")
    if source.is_symlink() or not source.is_file():
        raise PackageError("archive source is not a regular file")
    try:
        if source.stat().st_size > max_bytes:
            raise PackageError("archive exceeds byte limit")
    except OSError as exc:
        raise PackageError("archive source is unreadable") from exc
    if destination.exists() or destination.is_symlink():
        raise PackageError("package destination already exists")
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        with source.open("rb") as input_stream:
            # CPython's bounded EOCD reader handles ZIP64 without materializing entries.
            # Limit the central directory before ZipFile builds its in-memory index.
            read_end = getattr(zipfile, "_EndRecData", None)
            if not callable(read_end):
                raise PackageError("bounded ZIP inspection is unavailable on this Python runtime")
            end_record = read_end(input_stream)
            if end_record is None:
                raise PackageError("archive has no end record")
            if end_record[4] > max_files or end_record[5] > max_files * 1024:
                raise PackageError("archive central directory exceeds its limit")
            input_stream.seek(0)
            with zipfile.ZipFile(input_stream, "r") as archive:
                infos = archive.infolist()
                if len(infos) > max_files:
                    raise PackageError("archive contains too many files")
                names: set[str] = set()
                advertised = 0
                for info in infos:
                    name = _safe_name(info.orig_filename)
                    if name in names:
                        raise PackageError(f"duplicate ZIP member: {name}")
                    names.add(name)
                    if (
                        info.is_dir()
                        or info.compress_type != zipfile.ZIP_STORED
                        or info.flag_bits & 0x1
                    ):
                        raise PackageError(f"unsafe ZIP member: {name}")
                    advertised += info.file_size
                if advertised > max_bytes:
                    raise PackageError("archive exceeds byte limit")
                used = [0]
                for info in infos:
                    _extract_member(archive, info, staging, max_bytes, used)
        artifact = inspect_package(staging)
        for directory in sorted(
            (path for path in staging.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            _sync_directory(directory)
        _sync_directory(staging)
        _rename_noreplace(staging, destination)
        _sync_directory(destination.parent)
        return PackageArtifact(
            destination, artifact.digest_sha256, artifact.document, artifact.files
        )
    except (OSError, zipfile.BadZipFile, UnicodeError, EOFError) as exc:
        raise PackageError("invalid package archive") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
