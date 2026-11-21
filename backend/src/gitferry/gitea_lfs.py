"""Bounded Gitea 1.27.2 LFS basic transfers on the approved origin only."""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import tempfile
from pathlib import Path
from typing import Any, Literal

import httpx

from .connection_profiles import normalize_url
from .gitea_provisioning import _repository_segments
from .lfs_payloads import LFSPointer, publish_lfs_object, verify_lfs_object

_MEDIA = "application/vnd.git-lfs+json"
_JSON_LIMIT = 64 * 1024


class GiteaLFSError(RuntimeError):
    """An LFS prerequisite or transfer failed; never includes remote response bodies."""


class GiteaLFSClient:
    """No redirects, automatic retries, discovery URLs or external storage actions.

    Upload belongs inside the existing durable operation executor. Any exception
    after starting it must be treated as a potentially unknown remote outcome.
    Batch download is a protocol POST with read-only semantics, not an upload.
    """

    def __init__(
        self,
        base_url: str,
        repository: str,
        username: str,
        token: str,
        *,
        verify: ssl.SSLContext | bool = True,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_url(base_url).rstrip("/")
        owner, name = _repository_segments(repository)
        if not username or not token or verify is False:
            raise GiteaLFSError("local credentials and verified TLS are required")
        self.root = f"{self.base_url}/{owner}/{name}.git/info/lfs"
        self.client = httpx.Client(
            auth=httpx.BasicAuth(username, token),
            verify=verify,
            transport=transport,
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
            headers={"Accept": _MEDIA, "Accept-Encoding": "identity"},
        )

    def __enter__(self) -> GiteaLFSClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()

    def _json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with self.client.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    raise GiteaLFSError("LFS protocol request was rejected")
                data = bytearray()
                for chunk in response.iter_bytes(8192):
                    data.extend(chunk)
                    if len(data) > _JSON_LIMIT:
                        raise GiteaLFSError("LFS protocol response exceeds its bound")
            value = json.loads(data, object_pairs_hook=_unique)
            if not isinstance(value, dict):
                raise GiteaLFSError("LFS protocol response is not an object")
            return value
        except (httpx.HTTPError, ValueError, RecursionError):
            raise GiteaLFSError("LFS protocol response was unavailable or invalid") from None

    def _batch(
        self, operation: Literal["download", "upload"], pointer: LFSPointer
    ) -> dict[str, Any]:
        value = self._json(
            "POST",
            self.root + "/objects/batch",
            headers={"Content-Type": _MEDIA},
            json={
                "operation": operation,
                "transfers": ["basic"],
                "objects": [{"oid": pointer.oid, "size": pointer.size}],
            },
        )
        objects = value.get("objects")
        if (
            value.get("transfer", "basic") != "basic"
            or not isinstance(objects, list)
            or len(objects) != 1
        ):
            raise GiteaLFSError("LFS batch did not confirm the requested basic object")
        item = objects[0]
        if (
            not isinstance(item, dict)
            or item.get("oid") != pointer.oid
            or type(item.get("size")) is not int
            or item["size"] != pointer.size
            or item.get("error") is not None
            or not isinstance(item.get("actions", {}), dict)
        ):
            raise GiteaLFSError("LFS object is missing, inconsistent or inaccessible")
        return dict(item.get("actions", {}))

    def _action(
        self,
        actions: dict[str, Any],
        kind: Literal["download", "upload", "verify"],
        pointer: LFSPointer,
    ) -> tuple[str, dict[str, str]]:
        action = actions.get(kind)
        expected = {
            "download": f"{self.root}/objects/{pointer.oid}",
            "upload": f"{self.root}/objects/{pointer.oid}/{pointer.size}",
            "verify": f"{self.root}/verify",
        }[kind]
        if not isinstance(action, dict) or action.get("href") != expected:
            raise GiteaLFSError("LFS action is outside the qualified repository endpoint")
        supplied = action.get("header", {})
        if not isinstance(supplied, dict) or len(supplied) > 3:
            raise GiteaLFSError("LFS action headers are outside the qualified contract")
        headers: dict[str, str] = {}
        seen: set[str] = set()
        for key, value in supplied.items():
            lower = key.lower()
            if (
                lower in seen
                or not isinstance(value, str)
                or not value
                or len(value) > 8192
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise GiteaLFSError("LFS action headers are invalid")
            seen.add(lower)
            if lower == "authorization":
                headers["Authorization"] = value
            elif lower == "accept" and value == _MEDIA:
                headers["Accept"] = value
            elif lower == "transfer-encoding" and kind == "upload" and value == "chunked":
                # Gitea advertises this for git-lfs; our verified size chooses
                # Content-Length instead. Never forward conflicting framing.
                continue
            else:
                raise GiteaLFSError("LFS action headers are outside the qualified contract")
        return expected, headers

    def inspect(self, pointer: LFSPointer) -> None:
        """Check repository-specific availability without transferring or writing bytes."""
        self._action(self._batch("download", pointer), "download", pointer)

    def _download(self, pointer: LFSPointer, max_bytes: int, sink: Any = None) -> None:
        if max_bytes <= 0 or pointer.size > max_bytes:
            raise GiteaLFSError("LFS object exceeds the configured byte bound")
        url, headers = self._action(self._batch("download", pointer), "download", pointer)
        size = 0
        digest = hashlib.sha256()
        try:
            # Action-specific authorization supersedes local Basic authentication.
            with self.client.stream(
                "GET",
                url,
                headers=headers,
                auth=None if "Authorization" in headers else self.client.auth,
            ) as response:
                if response.status_code != 200:
                    raise GiteaLFSError("LFS download was rejected")
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > pointer.size or size > max_bytes:
                        raise GiteaLFSError("LFS download exceeds the declared size")
                    digest.update(chunk)
                    if sink is not None:
                        sink.write(chunk)
        except httpx.HTTPError:
            raise GiteaLFSError("LFS download response was lost") from None
        if size != pointer.size or digest.hexdigest() != pointer.oid:
            raise GiteaLFSError("LFS download digest or size mismatch")

    def verify_remote(self, pointer: LFSPointer, *, max_bytes: int) -> None:
        self._download(pointer, max_bytes)

    def fetch(self, pointer: LFSPointer, root: Path, member: str, *, max_bytes: int) -> None:
        if root.is_symlink() or not root.is_dir():
            raise GiteaLFSError("LFS package root must be an owned directory")
        with tempfile.NamedTemporaryFile(dir=root, prefix=".lfs-", delete=True) as temporary:
            self._download(pointer, max_bytes, temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
            publish_lfs_object(Path(temporary.name), root, member, pointer, max_bytes=max_bytes)

    def upload(self, pointer: LFSPointer, path: Path, *, max_bytes: int) -> None:
        """Upload once and verify; the caller journals and controls unknown outcomes."""
        verify_lfs_object(path, pointer, max_bytes=max_bytes)
        actions = self._batch("upload", pointer)
        if "upload" in actions:
            url, headers = self._action(actions, "upload", pointer)
            try:
                with (
                    path.open("rb") as source,
                    self.client.stream(
                        "PUT",
                        url,
                        content=source,
                        headers={**headers, "Content-Length": str(pointer.size)},
                        auth=None if "Authorization" in headers else self.client.auth,
                    ) as response,
                ):
                    if response.status_code not in (200, 201, 204):
                        raise GiteaLFSError("LFS upload was rejected; reconciliation required")
            except (httpx.HTTPError, OSError):
                raise GiteaLFSError("LFS upload outcome is unknown") from None
        if "verify" in actions:
            url, headers = self._action(actions, "verify", pointer)
            # Verification is also never retried by this adapter.
            self._json(
                "POST",
                url,
                headers={**headers, "Content-Type": _MEDIA},
                auth=None if "Authorization" in headers else self.client.auth,
                json={"oid": pointer.oid, "size": pointer.size},
            )
        self.verify_remote(pointer, max_bytes=max_bytes)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate LFS JSON key")
        result[key] = value
    return result
