from __future__ import annotations

import hashlib
import json
import ssl
from copy import deepcopy
from functools import partial
from pathlib import Path
from typing import Any

import httpx
import pytest
from gitferry import gitea_plan_adapter as adapter_module
from gitferry.connection_probe import LocalReferences
from gitferry.gitea_plan_adapter import GiteaPlanAdapter
from gitferry.plan_application import prepare_transfer_plan
from gitferry.plan_runner import PlanRunner
from gitferry.runtime import RuntimePaths
from gitferry.state_store import OperationBlocked, StateStore
from gitferry.transfer_plans import PlanService
from test_plan_application import configured_plan


class References(LocalReferences):
    def token(self, reference: str) -> str:
        return {
            "admin": "admin-token",
            "issue-credential": "issue-token",
            "comment-credential": "comment-token",
        }[reference]

    def tls(self, reference: str | None) -> tuple[ssl.SSLContext, str]:
        return ssl.create_default_context(), "system-trust"


class Remote:
    def __init__(self, state: StateStore):
        self.state = state
        self.writes: list[str] = []
        self.issue: dict[str, Any] | None = None
        self.comment: dict[str, Any] | None = None
        self.lose_comment_response = False
        self.wrong_author = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        token = request.headers["authorization"]
        actor = {
            "token admin-token": {"id": 1, "login": "technical", "is_admin": True},
            "token issue-token": {"id": 2, "login": "issue-author", "is_admin": False},
            "token comment-token": {"id": 3, "login": "comment-author", "is_admin": False},
        }[token]
        if self.wrong_author and actor["login"] == "comment-author":
            actor = {**actor, "id": 99}
        if path == "/api/v1/user":
            return httpx.Response(200, json=actor)
        if path == "/api/v1/repos/org/repo":
            return httpx.Response(
                200, json={"id": 8, "full_name": "org/repo", "archived": False, "has_issues": True}
            )
        if method == "POST":
            # A separate SQLite writer can acquire its lock during remote I/O.
            with self.state.transaction():
                pass
            self.writes.append(path)
            payload = json.loads(request.content)
            if path == "/api/v1/repos/org/repo/issues":
                self.issue = {
                    "id": 101,
                    "number": 9,
                    "title": payload["title"],
                    "body": payload["body"],
                    "state": "closed" if payload["closed"] else "open",
                    "labels": [],
                    "user": actor,
                    "created_at": "2026-01-01T00:00:00Z",
                }
                return httpx.Response(201, json=self.issue)
            if path == "/api/v1/repos/org/repo/issues/9/comments":
                assert self.issue is not None
                self.comment = {
                    "id": 201,
                    "body": payload["body"],
                    "issue_url": "https://target.invalid/api/v1/repos/org/repo/issues/9",
                    "user": actor,
                    "created_at": "2026-01-02T00:00:00Z",
                }
                if self.lose_comment_response:
                    raise httpx.ReadError("synthetic accepted response lost", request=request)
                return httpx.Response(201, json=self.comment)
        if path == "/api/v1/repos/org/repo/issues/9":
            return httpx.Response(200, json=self.issue)
        if path == "/api/v1/repos/org/repo/issues/9/comments":
            return httpx.Response(200, json=[self.comment] if self.comment else [])
        raise AssertionError(f"unexpected request {method} {path}")


def runner_for(
    state: StateStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PlanRunner, Remote]:
    paths = RuntimePaths(state.path, tmp_path / "packages", tmp_path / "work")
    paths.package_directory.mkdir(exist_ok=True)
    paths.work_directory.mkdir(exist_ok=True)
    remote = Remote(state)
    transport = httpx.MockTransport(remote)
    for name in ("GiteaProvisioningClient", "GiteaIssueClient", "GiteaCommentClient"):
        monkeypatch.setattr(
            adapter_module, name, partial(getattr(adapter_module, name), transport=transport)
        )
    with state.read_connection() as connection:
        identity = connection.execute(
            "SELECT approved_identity FROM connection_checks WHERE instance_key='target'"
        ).fetchone()[0]
    evidence = {
        "requires_approval": False,
        "observed_identity": {"ca_digest": "system-trust"},
        "account_id": "1",
        "login": "technical",
        "evidence_digest": hashlib.sha256(identity.encode()).hexdigest(),
    }
    monkeypatch.setattr(adapter_module, "probe_connection", lambda *args, **kwargs: evidence)
    return PlanRunner(
        PlanService(state), lambda _: GiteaPlanAdapter(state, paths, References())
    ), remote


def test_fixed_adapter_creates_native_authors_and_replays_same_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, request = configured_plan(tmp_path)
    runner, remote = runner_for(state, tmp_path, monkeypatch)
    plan = prepare_transfer_plan(state, request)
    runner.service.queue(plan["plan_id"], plan["digest"])
    result = runner.run(plan["plan_id"])
    assert result["status"] == "confirmed", result
    assert remote.issue is not None and remote.comment is not None
    assert remote.issue["user"]["id"] == 2 and remote.comment["user"]["id"] == 3
    assert remote.comment["body"] == request["metadata"][0]["values"]["body"]
    assert [step["target_global_id"] for step in result["operations"]] == ["101", "201"]
    assert runner.run(plan["plan_id"])["status"] == "confirmed"
    request["round_key"] = "new-packaging"
    repackaged = prepare_transfer_plan(state, request)
    runner.service.queue(repackaged["plan_id"], repackaged["digest"])
    assert runner.run(repackaged["plan_id"])["status"] == "confirmed"
    assert len(remote.writes) == 2
    moved = deepcopy(request)
    moved["metadata"][0]["repository"] = "other/repo"
    with pytest.raises(OperationBlocked, match="explicit baseline"):
        prepare_transfer_plan(state, moved)


@pytest.mark.parametrize("failure", ["wrong-author", "unsupported", "missing-credential"])
def test_all_selected_prerequisites_checked_before_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    state, request = configured_plan(tmp_path)
    runner, remote = runner_for(state, tmp_path, monkeypatch)
    if failure == "wrong-author":
        remote.wrong_author = True
    elif failure == "unsupported":
        request["unsupported_items"] = ["attachments"]
    else:
        request["author_credential_refs"]["comment-author"] = "unavailable"
    plan = prepare_transfer_plan(state, request)
    runner.service.queue(plan["plan_id"], plan["digest"])
    result = runner.run(plan["plan_id"])
    assert result["status"] in ("blocked", "failed")
    assert remote.writes == []
    assert all(step["status"] in ("prepared", "blocked_parent") for step in result["operations"])


def test_lost_comment_response_is_not_reposted_by_new_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, request = configured_plan(tmp_path)
    runner, remote = runner_for(state, tmp_path, monkeypatch)
    remote.lose_comment_response = True
    plan = prepare_transfer_plan(state, request)
    runner.service.queue(plan["plan_id"], plan["digest"])
    result = runner.run(plan["plan_id"])
    assert result["status"] == "blocked"
    assert result["operations"][0]["status"] == "confirmed"
    assert result["operations"][1]["status"] == "outcome_unknown"
    runner.service.queue(plan["plan_id"], plan["digest"])
    assert runner.run(plan["plan_id"])["status"] == "blocked"
    request["round_key"] = "new-packaging"
    with pytest.raises(OperationBlocked, match="reconciliation"):
        prepare_transfer_plan(state, request)
    assert len(remote.writes) == 2
    assert remote.comment is not None


def test_changed_ca_after_probe_blocks_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, request = configured_plan(tmp_path)
    runner, remote = runner_for(state, tmp_path, monkeypatch)
    monkeypatch.setattr(
        References, "tls", lambda self, reference: (ssl.create_default_context(), "changed-ca")
    )
    plan = prepare_transfer_plan(state, request)
    runner.service.queue(plan["plan_id"], plan["digest"])
    assert runner.run(plan["plan_id"])["status"] == "blocked"
    assert remote.writes == []
