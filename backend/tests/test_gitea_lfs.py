from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from gitferry.gitea_lfs import GiteaLFSClient, GiteaLFSError
from gitferry.lfs_payloads import LFSPointer

DATA = b"Synthetic binary\x00\xff\n" * 31
POINTER = LFSPointer(hashlib.sha256(DATA).hexdigest(), len(DATA))
ROOT = "https://gitea.example.invalid/owner/repo.git/info/lfs"


def response_actions(operation):
    if operation == "download":
        return {
            "download": {
                "href": ROOT + "/objects/" + POINTER.oid,
                "header": {"Authorization": "Bearer synthetic-action"},
            }
        }
    return {
        "upload": {
            "href": ROOT + f"/objects/{POINTER.oid}/{POINTER.size}",
            "header": {"Authorization": "Bearer synthetic-action", "Transfer-Encoding": "chunked"},
        },
        "verify": {
            "href": ROOT + "/verify",
            "header": {
                "Authorization": "Bearer synthetic-action",
                "Accept": "application/vnd.git-lfs+json;q=0.9, */*;q=0.8",
            },
        },
    }


def client(handler):
    return GiteaLFSClient(
        "https://gitea.example.invalid",
        "owner/repo",
        "technical",
        "synthetic-token",
        transport=httpx.MockTransport(handler),
    )


def handler_for(data=DATA, actions=None, lost_upload=False):
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/batch"):
            operation = json.loads(request.content)["operation"]
            return httpx.Response(
                200,
                json={
                    "objects": [
                        {
                            "oid": POINTER.oid,
                            "size": POINTER.size,
                            "actions": actions
                            if actions is not None
                            else response_actions(operation),
                        }
                    ]
                },
            )
        assert request.headers["authorization"] == "Bearer synthetic-action"
        if request.method == "PUT":
            assert request.content == DATA
            assert "transfer-encoding" not in request.headers
            if lost_upload:
                raise httpx.ReadError("untrusted secret response", request=request)
            return httpx.Response(200, json={"message": "OK"})
        if path.endswith("/verify"):
            return httpx.Response(200, json={"message": "OK"})
        return httpx.Response(200, content=data)

    return handler, requests


def test_basic_upload_fetch_and_verify_preserve_bytes(tmp_path):
    handler, requests = handler_for()
    source = tmp_path / "source"
    source.write_bytes(DATA)
    with client(handler) as adapter:
        adapter.inspect(POINTER)
        adapter.upload(POINTER, source, max_bytes=10000)
        adapter.fetch(POINTER, tmp_path, "lfs/object", max_bytes=10000)
    assert (tmp_path / "lfs/object").read_bytes() == DATA
    assert sum(r.method == "PUT" for r in requests) == 1
    assert all(str(r.url).startswith(ROOT) for r in requests)


@pytest.mark.parametrize(
    "href",
    [
        "https://other.invalid/object",
        ROOT + "/objects/other",
        ROOT.replace("owner/repo", "owner/other") + "/objects/" + POINTER.oid,
        ROOT + "/objects/" + POINTER.oid + "?redirect=elsewhere",
    ],
)
def test_foreign_action_rejected_before_fetch(href):
    handler, requests = handler_for(actions={"download": {"href": href}})
    with client(handler) as adapter, pytest.raises(GiteaLFSError, match="outside"):
        adapter.verify_remote(POINTER, max_bytes=10000)
    assert len(requests) == 1


@pytest.mark.parametrize("data", [b"wrong", DATA[:-1], DATA + b"x", b"x" * len(DATA)])
def test_bad_remote_bytes_are_not_published(tmp_path, data):
    handler, _ = handler_for(data=data)
    with client(handler) as adapter, pytest.raises(GiteaLFSError):
        adapter.fetch(POINTER, tmp_path, "object", max_bytes=10000)
    assert not list(tmp_path.iterdir())


def test_lost_upload_response_is_not_retried_or_verified(tmp_path):
    handler, requests = handler_for(lost_upload=True)
    source = tmp_path / "source"
    source.write_bytes(DATA)
    with client(handler) as adapter, pytest.raises(GiteaLFSError, match="unknown") as error:
        adapter.upload(POINTER, source, max_bytes=10000)
    assert "secret" not in str(error.value)
    assert [r.method for r in requests] == ["POST", "PUT"]


@pytest.mark.parametrize(
    "value",
    [
        {"objects": []},
        {"objects": [{"oid": POINTER.oid, "size": True}]},
        {"objects": [{"oid": POINTER.oid, "size": POINTER.size, "error": {"code": 404}}]},
        {"objects": [{"oid": "f" * 64, "size": POINTER.size}]},
    ],
)
def test_bad_batch_identity_is_blocked(value):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=value)

    with client(handler) as adapter, pytest.raises(GiteaLFSError):
        adapter.inspect(POINTER)
    assert len(requests) == 1


def test_redirect_and_oversized_json_never_follow_or_parse():
    for response in (
        httpx.Response(302, headers={"Location": "https://other.invalid"}),
        httpx.Response(200, content=b"x" * 70000),
        httpx.Response(200, content=b'{"objects":[],"objects":[]}'),
    ):
        requests = []

        def handler(request, requests=requests, response=response):
            requests.append(request)
            return response

        with client(handler) as adapter, pytest.raises(GiteaLFSError):
            adapter.inspect(POINTER)
        assert len(requests) == 1


@pytest.mark.parametrize("replacement", ["symlink", "in-place"])
def test_upload_uses_verified_snapshot_when_path_is_replaced_after_batch(tmp_path, replacement):
    source = tmp_path / "source"
    source.write_bytes(DATA)
    private = tmp_path / "unrelated-private-file"
    private.write_bytes(b"x" * len(DATA))
    handler, requests = handler_for()

    def replacing_handler(request):
        response = handler(request)
        if (
            request.url.path.endswith("/batch")
            and json.loads(request.content)["operation"] == "upload"
        ):
            if replacement == "symlink":
                source.unlink()
                source.symlink_to(private)
            else:
                source.write_bytes(private.read_bytes())
        return response

    with client(replacing_handler) as adapter:
        adapter.upload(POINTER, source, max_bytes=10000)
    assert [request.content for request in requests if request.method == "PUT"] == [DATA]


def test_corrupt_upload_snapshot_is_rejected_before_batch(tmp_path):
    from gitferry.lfs_payloads import LFSPayloadError

    source = tmp_path / "source"
    source.write_bytes(b"x" * len(DATA))
    handler, requests = handler_for()
    with client(handler) as adapter, pytest.raises(LFSPayloadError):
        adapter.upload(POINTER, source, max_bytes=10000)
    assert requests == []
