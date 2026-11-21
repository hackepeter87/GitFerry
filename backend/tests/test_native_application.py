from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from gitferry.native_application import NativePayloadApplication
from gitferry.native_operations import prepare_native_operations
from gitferry.operations import OperationBlocked, OperationExecutor, OutcomeUnknownError
from gitferry.state_store import TargetConfirmation
from test_native_operations import _selected, _setup


class FakeLFS:
    def __init__(self, *, lost: bool = False) -> None:
        self.lost = lost
        self.uploads = 0
        self.verifications: list[str] = []

    def __enter__(self) -> FakeLFS:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def upload(self, pointer, path: Path, *, max_bytes: int) -> None:
        self.uploads += 1
        if self.lost:
            raise OutcomeUnknownError("synthetic response lost after upload")

    def verify_remote(self, pointer, *, max_bytes: int) -> None:
        self.verifications.append(pointer.oid)


class FakeGit:
    def __init__(self, root: Path) -> None:
        self.package_root = root
        self.identities: list[tuple[str, str]] = []
        self.pushes = 0

    def _verify_repository_id(self, repository: str, expected: str) -> None:
        self.identities.append((repository, expected))

    def push_wiki_bundle(self, *args: object) -> TargetConfirmation:
        self.pushes += 1
        return TargetConfirmation("42", None, observed_state={"refs": {}})


class FakeWiki:
    def __init__(self, commit: str = "a" * 40) -> None:
        self.commit = commit
        self.page_calls: list[tuple[str, int, str]] = []

    def bootstrap(self, *args: object) -> dict:
        return {
            "title": "Home",
            "sub_url": "Home",
            "content_base64": "",
            "last_commit": {"sha": self.commit},
        }

    def get_page(self, repository: str, parent: int, title: str) -> dict:
        self.page_calls.append((repository, parent, title))
        return {"title": title, "last_commit": {"sha": self.commit}}


def _app(
    tmp_path: Path,
    *,
    lfs: FakeLFS | None = None,
    wiki: FakeWiki | None = None,
    parent_scope: str = "org/repo",
):
    state, plan = _setup(tmp_path)
    state.add_object_mapping(plan.repository_origin_id, "target", parent_scope, "42")
    fake_lfs = lfs or FakeLFS()
    git = FakeGit(tmp_path / "package")
    git.package_root.mkdir()
    return (
        state,
        plan,
        NativePayloadApplication(
            state,
            git,
            wiki or FakeWiki(),
            lambda _: fake_lfs,
            max_bytes=1024,
        ),
        fake_lfs,
        git,
    )


def _lfs_selected() -> dict:
    selected = _selected()
    selected["wiki"] = None
    return selected


def test_successful_lfs_write_followed_by_hook_crash_recovers_unknown_without_retry(
    tmp_path: Path,
) -> None:
    state, plan, app, lfs, _ = _app(tmp_path)
    operations, _ = prepare_native_operations(state, plan, _lfs_selected())
    operation = operations[0]
    with pytest.raises(RuntimeError, match="hook crash"):

        def crash() -> None:
            raise RuntimeError("hook crash")

        with OperationExecutor(state, "first", after_remote_hook=crash) as executor:
            app.apply(operation, executor)
    with OperationExecutor(state, "recovery"):
        pass
    assert state.get_operation(operation.operation_id).status == "outcome_unknown"
    with OperationExecutor(state, "retry") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            app.apply(operation, executor)
    assert lfs.uploads == 1


def test_lfs_lost_response_is_unknown_and_retry_is_blocked(tmp_path: Path) -> None:
    state, plan, app, lfs, _ = _app(tmp_path, lfs=FakeLFS(lost=True))
    operation = prepare_native_operations(state, plan, _lfs_selected())[0][0]
    with OperationExecutor(state, "first") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            app.apply(operation, executor)
    with OperationExecutor(state, "retry") as executor:
        with pytest.raises(OperationBlocked, match="reconciliation"):
            app.apply(operation, executor)
    assert state.get_operation(operation.operation_id).status == "outcome_unknown"
    assert lfs.uploads == 1


def test_explicit_lfs_resolution_verifies_remote_and_preserves_repository_mapping(
    tmp_path: Path,
) -> None:
    state, plan, app, lfs, _ = _app(tmp_path, lfs=FakeLFS(lost=True))
    operation = prepare_native_operations(state, plan, _lfs_selected())[0][0]
    with OperationExecutor(state, "first") as executor:
        with pytest.raises(OperationBlocked):
            app.apply(operation, executor)
    resolved = app.resolve_unknown(operation.operation_id, evidence="operator verified object")
    assert resolved.status == "confirmed"
    assert lfs.verifications == ["a" * 64]
    mapping = state.get_object_mapping(plan.repository_origin_id, "target")
    assert mapping.target_global_id == "42"


def test_unknown_bootstrap_blocks_dependent_wiki_push(tmp_path: Path) -> None:
    state, plan, app, _, git = _app(tmp_path)
    operations, _ = prepare_native_operations(state, plan, _selected())
    bootstrap = next(item for item in operations if item.operation_type == "bootstrap_wiki")
    push = next(item for item in operations if item.operation_type == "push_wiki_bundle")
    state.start_executor_session("bootstrap")
    state.claim_operation(bootstrap.operation_id, "bootstrap")
    state.mark_outcome_unknown(bootstrap.operation_id, "synthetic bootstrap response lost")
    state.end_executor_session("bootstrap")
    with OperationExecutor(state, "push") as executor:
        with pytest.raises(OperationBlocked, match="confirmed result"):
            app.apply(push, executor)
    assert git.pushes == 0


def test_wrong_wiki_commit_and_wrong_parent_are_rejected_without_writes(tmp_path: Path) -> None:
    state, plan, app, _, git = _app(tmp_path, wiki=FakeWiki("a" * 40))
    bootstrap = next(
        item
        for item in prepare_native_operations(state, plan, _selected())[0]
        if item.operation_type == "bootstrap_wiki"
    )
    state.start_executor_session("bootstrap")
    state.claim_operation(bootstrap.operation_id, "bootstrap")
    state.mark_outcome_unknown(bootstrap.operation_id, "synthetic unknown")
    state.end_executor_session("bootstrap")
    with pytest.raises(OperationBlocked, match="differs"):
        app.resolve_unknown(bootstrap.operation_id, evidence="wrong", wiki_commit="b" * 40)
    wrong_root = tmp_path / "wrong"
    wrong_root.mkdir()
    wrong_state, wrong_plan, wrong_app, _, _ = _app(wrong_root, parent_scope="other/repo")
    operation = prepare_native_operations(wrong_state, wrong_plan, _lfs_selected())[0][0]
    with pytest.raises(OperationBlocked, match="parent mapping"):
        with OperationExecutor(wrong_state, "parent") as executor:
            wrong_app.apply(operation, executor)
    assert git.pushes == 0


def test_bootstrap_root_guard_rejects_non_root_history_without_force_push(tmp_path: Path) -> None:
    state, plan, app, _, git = _app(tmp_path)
    operation = next(
        item
        for item in prepare_native_operations(state, plan, _selected())[0]
        if item.operation_type == "bootstrap_wiki"
    )
    sha = "a" * 40

    @contextmanager
    def mirror(*args: object, **kwargs: object):
        yield tmp_path

    app.git.source_mirror = mirror
    app.git._local_refs = lambda _: {"refs/heads/main": sha}
    app.git._run_git = lambda args, cwd: SimpleNamespace(
        stdout=f"{sha} {'b' * 40}\n" if args[0] == "rev-list" else "Home.md\n"
    )
    page = {"last_commit": {"sha": sha}}
    with pytest.raises(OperationBlocked, match="preexisting"):
        app.verify_bootstrap("org/repo", "42", page, operation.intent)
    assert git.pushes == 0
