"""Controlled execution of the small set of currently supported Gitea writes."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gitferry.state_store import (
    ExecutorLeaseConflict,
    OperationBlocked,
    OperationRecord,
    StateStore,
    TargetConfirmation,
)

RemoteWrite = Callable[[dict[str, Any]], TargetConfirmation]


class ExecutorBusy(RuntimeError):
    """Raised when the local state volume already has an active writer."""


class OutcomeUnknownError(RuntimeError):
    """Signals that a remote write may have succeeded without a usable response."""


class OperationExecutor:
    """Own the local writer lock while external calls run outside SQLite transactions."""

    def __init__(
        self,
        state: StateStore,
        executor_id: str,
        *,
        after_remote_hook: Callable[[], None] | None = None,
    ):
        self.state = state
        self.executor_id = executor_id
        self.after_remote_hook = after_remote_hook
        self._lock_fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self.state.path.with_name(self.state.path.name + ".executor.lock")

    def __enter__(self) -> OperationExecutor:
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(file_descriptor)
            raise ExecutorBusy(f"another GitFerry writer is active for {self.state.path}") from exc
        self._lock_fd = file_descriptor
        try:
            self.state.start_executor_session(self.executor_id, recover_abandoned=True)
        except ExecutorLeaseConflict as exc:
            self._unlock()
            raise ExecutorBusy(str(exc)) from exc
        except Exception:
            self._unlock()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        try:
            # On an exceptional exit the durable lease is the successor's proof that
            # an executing remote write was interrupted and needs reconciliation.
            if exc_type is None:
                self.state.end_executor_session(self.executor_id)
        finally:
            self._unlock()

    def _unlock(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    def execute(self, operation_id: str, remote_write: RemoteWrite) -> OperationRecord:
        current = self.state.get_operation(operation_id)
        if current.status == "confirmed":
            return current
        if current.status == "outcome_unknown":
            raise OperationBlocked(
                f"operation {operation_id} has an unknown outcome; reconciliation required"
            )
        operation = self.state.claim_operation(operation_id, self.executor_id)
        if operation.status == "confirmed":
            return operation
        try:
            target = remote_write(operation.intent)
        except Exception as exc:
            reason = f"remote write outcome is unknown: {exc}"
            self.state.mark_outcome_unknown(operation_id, reason)
            raise OperationBlocked(
                f"operation {operation_id} outcome is unknown; reconciliation required"
            ) from exc
        if self.after_remote_hook is not None:
            self.after_remote_hook()
        return self.state.confirm_operation(operation_id, target)


__all__ = [
    "ExecutorBusy",
    "OperationBlocked",
    "OperationExecutor",
    "OutcomeUnknownError",
    "TargetConfirmation",
]
