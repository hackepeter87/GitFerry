"""One local background executor for the existing bounded transfer operations."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from gitferry.operations import ExecutorBusy, OperationExecutor, RemoteWrite
from gitferry.plan_diagnostics import PreflightBlocked
from gitferry.state_store import OperationBlocked, OperationRecord
from gitferry.transfer_plans import PlanConflict, PlanService


class PlanAdapter(Protocol):
    def preflight(self, plan: dict[str, Any]) -> None: ...
    def apply(self, plan: dict[str, Any], executor: OperationExecutor) -> None: ...


class PlanCancelled(RuntimeError):
    """Cancellation is observed only before an external operation starts."""


class PlanYielded(RuntimeError):
    """A service shutdown leaves confirmed work resumable at the next safe boundary."""


class _PlanExecutor(OperationExecutor):
    def __init__(
        self,
        service: PlanService,
        plan_id: str,
        stop: threading.Event,
        after_remote_hook: Callable[[], None] | None,
    ):
        super().__init__(
            service.store,
            f"plan-{os.getpid()}-{uuid.uuid4().hex}",
            after_remote_hook=after_remote_hook,
        )
        self.service, self.plan_id, self.stop = service, plan_id, stop

    def execute(self, operation_id: str, remote_write: RemoteWrite) -> OperationRecord:
        plan = self.service.get(self.plan_id)
        if operation_id not in {step["operation_id"] for step in plan["operations"]}:
            raise PlanConflict("executor cannot write an operation outside the reviewed plan")
        if plan["cancel_requested"]:
            raise PlanCancelled()
        if self.stop.is_set():
            raise PlanYielded()
        parents = plan["document"]["dependencies"].get(operation_id, [])
        parent_operations = [self.state.get_operation(parent) for parent in parents]
        current = self.state.get_operation(operation_id)
        if any(parent.status != "confirmed" for parent in parent_operations):
            if current.status == "prepared":
                self.state.block_operation_on_parent(
                    operation_id, "planned parent operation is not confirmed"
                )
            raise OperationBlocked("planned parent operation is not confirmed")
        if current.status == "blocked_parent" and parent_operations:
            self.state.unblock_operation_on_mappings(
                operation_id, tuple(parent.origin_id for parent in parent_operations)
            )
        # A local profile edit/revocation cannot silently authorize later writes.
        with self.state.read_connection() as connection:
            self.service._validate(connection, self.service._get_row(connection, self.plan_id))
        return super().execute(operation_id, remote_write)


AdapterFactory = Callable[[dict[str, Any]], AbstractContextManager[PlanAdapter]]


class PlanRunner:
    def __init__(
        self,
        service: PlanService,
        adapter_factory: AdapterFactory,
        *,
        stop: threading.Event | None = None,
        after_remote_hook: Callable[[], None] | None = None,
    ):
        self.service, self.adapter_factory = service, adapter_factory
        self.stop = stop or threading.Event()
        self.after_remote_hook = after_remote_hook

    def run(self, plan_id: str) -> dict[str, Any]:
        if self.service.get(plan_id)["status"] == "confirmed":
            return self.service.get(plan_id)
        # Existing flock + journal lease excludes CLI/API/other worker processes.
        with _PlanExecutor(self.service, plan_id, self.stop, self.after_remote_hook) as executor:
            plan = self.service.begin(plan_id)
            if plan["status"] != "preflighting":
                return plan
            try:
                if self.stop.is_set():
                    raise PlanYielded()
                with self.adapter_factory(plan) as adapter:
                    adapter.preflight(plan)
                    if self.service.get(plan_id)["cancel_requested"]:
                        raise PlanCancelled()
                    self.service.finish(plan_id, "running")
                    adapter.apply(plan, executor)
                return self.service.finish(plan_id, "confirmed")
            except PlanCancelled:
                return self.service.finish(
                    plan_id, "cancelled", "cancelled at a safe operation boundary"
                )
            except PlanYielded:
                self.service.record_event(
                    plan_id, "service stopped at safe boundary; restart will preflight again"
                )
                return self.service.get(plan_id)
            except PreflightBlocked as exc:
                return self.service.finish(plan_id, "blocked", exc.check.value)
            except (OperationBlocked, PlanConflict):
                return self.service.finish(
                    plan_id, "blocked", "preflight or operation requires local resolution"
                )
            except Exception:
                # Remote response text and credentials never enter public plan diagnostics.
                return self.service.finish(
                    plan_id, "failed", "local execution failed; inspect selected operation evidence"
                )

    def run_next(self) -> dict[str, Any] | None:
        with self.service.store.read_connection() as connection:
            row = connection.execute(
                "SELECT plan_id FROM transfer_plans "
                "WHERE status IN ('queued','preflighting','running') "
                "ORDER BY created_at,plan_id LIMIT 1"
            ).fetchone()
        return None if row is None else self.run(row["plan_id"])


class BackgroundPlanWorker:
    """Process-owned thread; HTTP clients only persist commands and read status."""

    def __init__(
        self, runner: PlanRunner, *, run_export: Callable[[], dict[str, Any] | None] | None = None
    ):
        self.runner = runner
        self.run_export = run_export
        self.last_error: str | None = None
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="gitferry-plan-worker", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.runner.stop.set()
        self.wake.set()
        self.thread.join(timeout=2)

    def _loop(self) -> None:
        while not self.runner.stop.is_set():
            try:
                result = self.runner.run_next()
                exported = None
                if self.run_export is not None and not self.runner.stop.is_set():
                    exported = self.run_export()
                self.last_error = None
                if result is not None or exported is not None:
                    continue
            except ExecutorBusy:
                pass
            except Exception as exc:
                # Retry local availability only. No remote call occurs until the
                # existing journal and full preflight permit it on the next pass.
                error_type = type(exc).__name__
                if self.last_error != error_type:
                    logging.getLogger(__name__).error("plan_worker_local_state_unavailable")
                self.last_error = error_type
            self.wake.wait(timeout=1)
            self.wake.clear()
