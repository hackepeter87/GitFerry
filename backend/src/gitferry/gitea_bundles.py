"""Verified Git-bundle publication to a Gitea repository."""

from __future__ import annotations

import hashlib
import os
import re
import ssl
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Final

import httpx

from .connection_profiles import ProfileValidationError, normalize_url
from .git_bundle import GitBundleError, GitImportResult, import_git_bundle
from .provisioning import BundleIntent
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

    def _verify_repository_id(self, repository: str, expected: str) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise GiteaBundleError("invalid repository name")
        owner, name = repository.split("/", 1)
        try:
            verify = ssl.create_default_context(
                cafile=str(self.ca_file) if self.ca_file else os.environ.get("SSL_CERT_FILE")
            )
            with httpx.Client(
                base_url=self.base_url + "/api/v1",
                headers={"Authorization": "token " + self.token},
                verify=verify,
                timeout=20,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get(f"/repos/{owner}/{name}")
                response.raise_for_status()
                value = response.json()
        except (httpx.HTTPError, ValueError):
            raise GiteaBundleError("target repository identity check failed") from None
        if not isinstance(value, dict) or str(value.get("id")) != expected:
            raise GiteaBundleError("target repository id does not match the journal mapping")

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
