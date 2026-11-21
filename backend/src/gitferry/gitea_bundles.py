"""Verified Git-bundle publication to a Gitea repository."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import ssl
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Final

from .connection_profiles import ProfileValidationError, normalize_url
from .git_bundle import (
    GitBundleError,
    GitBundleResult,
    GitImportResult,
    create_git_bundle,
    import_git_bundle,
)
from .provisioning import BundleIntent
from .source_snapshot import SourceSnapshotError, read_repository_identity
from .state_store import TargetConfirmation


class GiteaBundleError(RuntimeError):
    """Bundle validation or publication failed; a write outcome may be unknown."""


_REPOSITORY: Final = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}/[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z"
)
_GIT_TIMEOUT: Final = 60


class GiteaBundlePusher(AbstractContextManager["GiteaBundlePusher"]):
    """Preflight and publish one verified bundle through authenticated Git HTTP."""

    def __init__(
        self,
        package_root: Path,
        work_root: Path,
        base_url: str,
        username: str,
        token: str,
        *,
        ca_file: Path | None = None,
    ) -> None:
        self.package_root = Path(package_root)
        self.work_root = Path(work_root)
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.token = token
        self.ca_file = Path(ca_file) if ca_file is not None else None
        self._prepared: dict[tuple[str, str, int], GitImportResult] = {}
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._validate_roots()

    def _validate_roots(self) -> None:
        if self.package_root.is_symlink() or not self.package_root.is_dir():
            raise GiteaBundleError("package root must be an owned directory")
        if self.work_root.exists() and (self.work_root.is_symlink() or not self.work_root.is_dir()):
            raise GiteaBundleError("work root must be an owned directory")
        self.work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.ca_file is not None and (self.ca_file.is_symlink() or not self.ca_file.is_file()):
            raise GiteaBundleError("CA file must be a regular file")
        try:
            self.base_url = normalize_url(self.base_url)
        except ProfileValidationError:
            raise GiteaBundleError("base URL must be HTTPS without credentials or query") from None
        if not self.username or not self.token:
            raise GiteaBundleError("local Git credentials are required")

    def _repository_url(self, repository: str) -> str:
        if not _REPOSITORY.fullmatch(repository):
            raise GiteaBundleError("invalid repository name")
        return f"{self.base_url}/{repository}.git"

    def _wiki_url(self, repository: str) -> str:
        if not _REPOSITORY.fullmatch(repository):
            raise GiteaBundleError("invalid repository name")
        return f"{self.base_url}/{repository}.wiki.git"

    def __enter__(self) -> GiteaBundlePusher:
        self._ensure_work_directory()
        return self

    def __exit__(self, *exc: object) -> None:
        self._prepared.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def _ensure_work_directory(self) -> Path:
        if self._temporary is None:
            self._temporary = tempfile.TemporaryDirectory(
                prefix=f"gitferry-bundles-{os.getpid()}-", dir=self.work_root
            )
        return Path(self._temporary.name)

    def preflight(self, bundle: BundleIntent) -> GitImportResult:
        """Verify and import ``bundle`` once into an owned temporary bare repository."""

        key = (bundle.member, bundle.sha256, bundle.size)
        existing = self._prepared.get(key)
        if existing is not None:
            return existing
        work_directory = self._ensure_work_directory()
        destination_member = f"{hashlib.sha256(bundle.member.encode()).hexdigest()}.git"
        try:
            imported = import_git_bundle(
                self.package_root,
                bundle.member,
                bundle.sha256,
                bundle.size,
                work_directory,
                destination_member,
            )
        except (GitBundleError, OSError):
            raise GiteaBundleError("bundle preflight failed") from None
        self._prepared[key] = imported
        return imported

    def check_remote(
        self, repository: str, *, bundle: BundleIntent, expected_empty: bool
    ) -> dict[str, str]:
        """Read the target's refs; never alters the remote repository."""

        expected = self._local_refs(self.preflight(bundle).destination)
        refs = self._git_remote("ls-remote", self._repository_url(repository), None)
        if expected_empty and refs:
            raise GiteaBundleError("target repository is not empty")
        if not expected_empty and refs != expected:
            raise GiteaBundleError("target repository has divergent refs")
        return refs

    def push(
        self,
        repository: str,
        *,
        bundle_member: str,
        bundle_sha256: str,
        bundle_size: int,
        expected_repository_id: str,
    ) -> TargetConfirmation:
        """Publish only to an empty target, or accept an exact previously completed replay."""

        bundle = BundleIntent(bundle_member, bundle_sha256, bundle_size)
        imported = self.preflight(bundle)
        expected = self._local_refs(imported.destination)
        self._verify_repository_id(repository, expected_repository_id)
        observed = self._git_remote("ls-remote", self._repository_url(repository), None)
        if observed:
            self.check_remote(repository, bundle=bundle, expected_empty=False)
            return TargetConfirmation(expected_repository_id, None)
        self._push_refs(self._repository_url(repository), expected, imported.destination)
        self.check_remote(repository, bundle=bundle, expected_empty=False)
        return TargetConfirmation(expected_repository_id, None)

    def export_bundle(
        self,
        repository: str,
        *,
        member: str,
        expected_repository_id: str,
        max_bytes: int | None = None,
        wiki: bool = False,
    ) -> GitBundleResult | None:
        """Capture a stable, bounded source mirror into the owned package root.

        Every remote read is authenticated through :meth:`_git_remote`, and
        identity/ref checks bracket the clone and bundle publication.  A
        source with no refs is a valid structure-only repository and therefore
        produces no synthetic bundle.
        """

        result: GitBundleResult | None = None
        try:
            with self.source_mirror(
                repository, expected_repository_id, max_bytes=max_bytes, wiki=wiki
            ) as clone_directory:
                if clone_directory is None:
                    return None
                result = create_git_bundle(
                    clone_directory, self.package_root, member, max_bytes=max_bytes
                )
                return result
        except GiteaBundleError:
            if result is not None and result.path.is_file() and not result.path.is_symlink():
                result.path.unlink(missing_ok=True)
            raise
        except (GitBundleError, OSError):
            if result is not None and result.path.is_file() and not result.path.is_symlink():
                result.path.unlink(missing_ok=True)
            raise GiteaBundleError("source bundle export failed") from None

    @contextmanager
    def source_mirror(
        self,
        repository: str,
        expected_repository_id: str,
        max_bytes: int | None = None,
        *,
        wiki: bool = False,
    ) -> Iterator[Path | None]:
        """Yield one stable authenticated source mirror, or ``None`` if empty."""

        if max_bytes is not None and max_bytes <= 0:
            raise GiteaBundleError("max_bytes must be positive")
        self._verify_repository_id(repository, expected_repository_id)
        url = self._wiki_url(repository) if wiki else self._repository_url(repository)
        before = self._git_remote("ls-remote", url, None)
        if not before:
            self._verify_repository_id(repository, expected_repository_id)
            if self._git_remote("ls-remote", url, None):
                raise GiteaBundleError("source refs changed during export")
            yield None
            self._verify_repository_id(repository, expected_repository_id)
            if self._git_remote("ls-remote", url, None):
                raise GiteaBundleError("source refs changed during export")
            return

        work_directory = self._ensure_work_directory()
        clone_directory = Path(tempfile.mkdtemp(prefix="gitferry-source-", dir=work_directory))
        try:
            self._run_git(["clone", "--mirror", url, os.fspath(clone_directory)], cwd=None)
            if max_bytes is not None:
                used = 0
                for path in clone_directory.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        used += path.stat().st_size
                        if used > max_bytes:
                            raise GiteaBundleError(
                                "source clone exceeds the configured capture limit"
                            )
            if self._local_refs(clone_directory) != before:
                raise GiteaBundleError("source refs changed during export")
            if self._git_remote("ls-remote", url, None) != before:
                raise GiteaBundleError("source refs changed during export")
            self._verify_repository_id(repository, expected_repository_id)
            yield clone_directory
            if self._git_remote("ls-remote", url, None) != before:
                raise GiteaBundleError("source refs changed during export")
            self._verify_repository_id(repository, expected_repository_id)
        except GiteaBundleError:
            raise
        except OSError:
            raise GiteaBundleError("source mirror failed") from None
        finally:
            shutil.rmtree(clone_directory, ignore_errors=True)

    def push_wiki_bundle(
        self,
        repository: str,
        bundle_member: str,
        bundle_sha256: str,
        bundle_size: int,
        expected_repository_id: str,
        expected_refs: Mapping[str, str],
    ) -> TargetConfirmation:
        """Publish wiki refs without adopting or deleting an existing ref."""

        imported = self.preflight(BundleIntent(bundle_member, bundle_sha256, bundle_size))
        self._verify_repository_id(repository, expected_repository_id)
        url = self._wiki_url(repository)
        current = self._git_remote("ls-remote", url, None)
        if current != dict(expected_refs):
            raise GiteaBundleError("target wiki refs differ from the expected state")
        desired = self._local_refs(imported.destination)
        if not set(current).issubset(desired):
            raise GiteaBundleError("wiki push would delete an existing ref")
        args = ["push", "--atomic"]
        for ref in sorted(desired):
            args.append(f"--force-with-lease={ref}:{current.get(ref, '')}")
        args.append(url)
        args.extend(f"{desired[ref]}:{ref}" for ref in sorted(desired))
        self._run_git(args, cwd=imported.destination)
        observed = self._git_remote("ls-remote", url, None)
        if observed != desired:
            raise GiteaBundleError("target wiki refs differ after push")
        self._verify_repository_id(repository, expected_repository_id)
        return TargetConfirmation(expected_repository_id, None, observed_state={"refs": observed})

    def _verify_repository_id(self, repository: str, expected: str) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise GiteaBundleError("invalid repository name")
        try:
            verify = ssl.create_default_context(
                cafile=str(self.ca_file) if self.ca_file else os.environ.get("SSL_CERT_FILE")
            )
            actual = read_repository_identity(self.base_url, self.token, verify, repository)
        except (SourceSnapshotError, ValueError, OSError):
            raise GiteaBundleError("repository identity check failed") from None
        if actual != expected:
            raise GiteaBundleError("repository id does not match the expected mapping")

    def _push_refs(self, url: str, refs: Mapping[str, str], repository: Path) -> None:
        if not refs:
            raise GiteaBundleError("bundle contains no refs")
        args = ["push", "--atomic"]
        for ref in refs:
            args.append(f"--force-with-lease={ref}:")
        args.append(url)
        args.extend(f"{sha}:{ref}" for ref, sha in refs.items())
        self._run_git(args, cwd=repository)

    def _local_refs(self, repository: Path) -> dict[str, str]:
        try:
            result = self._run_git(["show-ref"], cwd=repository)
        except GiteaBundleError as exc:
            if "exit status 1" in str(exc):
                return {}
            raise
        return {
            ref: sha
            for sha, ref in (line.split(" ", 1) for line in result.stdout.splitlines())
            if ref != "HEAD"
        }

    def _git_remote(self, command: str, url: str, cwd: Path | None) -> dict[str, str]:
        if command != "ls-remote":
            raise GiteaBundleError("only remote ref reads are allowed here")
        args = [command, "--refs", url]
        result = self._run_git(args, cwd=cwd)
        return {
            ref: sha
            for sha, ref in (line.split("\t", 1) for line in result.stdout.splitlines())
            if ref != "HEAD"
        }

    def _run_git(self, args: list[str], *, cwd: Path | None) -> subprocess.CompletedProcess[str]:
        script_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", prefix="gitferry-askpass-", delete=False, dir=self.work_root
            ) as script:
                script.write("#!/bin/sh\nprintf '%s\\n' \"$GITFERRY_ASKPASS_TOKEN\"\n")
                script_path = Path(script.name)
            script_path.chmod(0o700)
            env = {
                "PATH": os.environ.get("PATH", ""),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(script_path),
                "GITFERRY_ASKPASS_TOKEN": self.token,
            }
            if self.ca_file is not None:
                env["GIT_SSL_CAINFO"] = str(self.ca_file)
            elif os.environ.get("SSL_CERT_FILE"):
                env["GIT_SSL_CAINFO"] = os.environ["SSL_CERT_FILE"]
            command = [
                "git",
                "-c",
                f"credential.username={self.username}",
                "-c",
                "credential.helper=",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "http.followRedirects=false",
                "-c",
                "http.sslVerify=true",
                "--no-optional-locks",
                *args,
            ]
            try:
                return subprocess.run(  # noqa: S603
                    command,
                    cwd=cwd,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_GIT_TIMEOUT,
                )
            except subprocess.CalledProcessError as exc:
                reason = "command failure"
                diagnostic = (exc.stderr or "").lower()
                for marker, category in (
                    ("certificate", "TLS verification failed"),
                    ("authentication", "authentication rejected"),
                    ("could not read", "credentials unavailable"),
                    ("not found", "repository unavailable"),
                    ("unable to access", "remote unavailable"),
                ):
                    if marker in diagnostic:
                        reason = category
                        break
                raise GiteaBundleError(
                    f"git {args[0]} failed (exit status {exc.returncode}; {reason})"
                ) from None
            except (OSError, subprocess.TimeoutExpired):
                raise GiteaBundleError(f"git {args[0]} failed") from None
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)


__all__ = ["GiteaBundleError", "GiteaBundlePusher"]
