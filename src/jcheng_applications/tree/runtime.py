"""Production tree execution engine (opt-in, py_trees 2.6).

The engine is a thin driver over the shared core: every leaf calls the same
``StepExecutor`` as the linear runner, and the independent ``RunSupervisor``
owns the deadline, cancellation and the one stop. py_trees is imported here
only; the default linear engine never imports it.
"""

from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timezone
import queue
import threading
import time
from typing import Any, Callable

import py_trees
from py_trees.common import Status

from ..core.classify import Decision
from ..core.executor import StepExecutor, StepOutcome
from ..core.preflight import (
    check_authorization,
    check_contract,
    check_lease,
    check_safety,
)
from ..core.report import SelectionRecord, StepReport
from ..core.stop import receipt_fields
from ..core.strategy import select_branch
from ..model import Authorization, Plan, PlanStep, Recipe
from ..runner import RunReport, RunState, make_report


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return moment.isoformat()


class _DaemonWorker:
    """One daemon worker: a single in-flight step, no process-exit block."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Future, Callable[..., Any], tuple, dict]] = (
            queue.Queue()
        )
        self._thread = threading.Thread(
            target=self._run, name="m1-5-step-worker", daemon=True
        )
        self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
        future: Future = Future()
        self._jobs.put((future, fn, args, kwargs))
        return future

    def _run(self) -> None:
        while True:
            future, fn, args, kwargs = self._jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 - surfaced through the future
                future.set_exception(exc)


class StepLog:
    """Completed and late step outcomes; late is evidence only."""

    def __init__(self) -> None:
        self.outcomes: list[StepOutcome] = []
        self.late: list[StepOutcome] = []
        self._lock = threading.Lock()

    def append(self, outcome: StepOutcome) -> None:
        with self._lock:
            self.outcomes.append(outcome)

    def add_late(self, future: Future) -> None:
        try:
            outcome = future.result()
        except Exception:  # noqa: BLE001 - a late failure is still late
            return
        with self._lock:
            self.late.append(outcome)


class RunSupervisor:
    """Independent deadline/cancel/stop supervisor.

    The tree loop only reads ``halted``. The stop is issued on this thread,
    once. Lease renewal is owned by ``LeaseSession``; this supervisor owns the
    run deadline, cancellation and the stop path.
    """

    def __init__(
        self,
        stop_sink: Callable[[str], None],
        deadline_s: float | None = None,
    ) -> None:
        self._stop_sink = stop_sink
        self._deadline = (
            time.monotonic() + float(deadline_s) if deadline_s is not None else None
        )
        self._halt = threading.Event()
        self._done = threading.Event()
        self._pending = threading.Event()
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._issued = False
        self.stop_error: str | None = None
        self.stop_receipt: object | None = None
        self._thread = threading.Thread(
            target=self._run, name="m1-5-supervisor", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    @property
    def halted(self) -> bool:
        return self._halt.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def request_stop(self, reason: str) -> None:
        with self._lock:
            if self._reason is None:
                self._reason = reason or "stopped"
        self._halt.set()
        self._pending.set()

    def _run(self) -> None:
        while not self._done.wait(0.02):
            if self._deadline is not None and time.monotonic() >= self._deadline:
                with self._lock:
                    if self._reason is None:
                        self._reason = "deadline"
                self._issue()
            if self._pending.is_set():
                self._pending.clear()
                self._issue()

    def _issue(self) -> None:
        with self._lock:
            if self._issued:
                return
            self._issued = True
            reason = self._reason or "stopped"
        self._halt.set()
        try:
            self.stop_receipt = self._stop_sink(reason)
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            self.stop_error = f"{type(exc).__name__}: {exc}"
        finally:
            self._stopped.set()

    def close(self) -> None:
        if self._halt.is_set():
            self._stopped.wait(timeout=2.0)
        self._done.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


class SubmitLeaf(py_trees.behaviour.Behaviour):
    """Submit one plan step through the shared core and poll the future."""

    def __init__(
        self,
        step: PlanStep,
        executor: StepExecutor,
        worker: _DaemonWorker,
        log: StepLog,
        supervisor: RunSupervisor,
        run_id: str,
        lease_id: str,
        *,
        record_main: bool = True,
        main_outcomes: dict[str, StepOutcome] | None = None,
    ) -> None:
        super().__init__(step.name)
        self._step = step
        self._executor = executor
        self._worker = worker
        self._log = log
        self._supervisor = supervisor
        self._run_id = run_id
        self._lease_id = lease_id
        self._record_main = record_main
        self._main_outcomes = main_outcomes if main_outcomes is not None else {}
        self.future: Future | None = None
        self.started = False
        self.outcome: StepOutcome | None = None

    @property
    def step(self) -> PlanStep:
        return self._step

    def initialise(self) -> None:
        self.started = True
        self.future = self._worker.submit(
            self._executor.submit,
            self._step,
            run_id=self._run_id,
            lease_id=self._lease_id,
        )

    def update(self) -> Status:
        if self.future is None or not self.future.done():
            return Status.RUNNING
        try:
            outcome = self.future.result()
        except Exception as exc:  # noqa: BLE001 - the core should never leak this
            outcome = StepOutcome(
                report=StepReport(
                    index=self._step.index,
                    name=self._step.name,
                    outcome="UNKNOWN",
                    reason_code=type(exc).__name__,
                    message=str(exc),
                    intent_id=f"app-{self._step.idempotency_key[:24]}",
                    idempotency_key=self._step.idempotency_key,
                    bus_verified=None,
                    safety_state=None,
                ),
                decision=Decision.INDETERMINATE,
                reason=(
                    f"step {self._step.index} {self._step.name!r}: "
                    f"tree worker error: {type(exc).__name__}: {exc}"
                ),
            )
        self.outcome = outcome
        self._log.append(outcome)
        if outcome.decision is Decision.CONTINUE:
            if self._record_main:
                self._main_outcomes[self._step.name] = outcome
            return Status.SUCCESS
        self._supervisor.request_stop(
            outcome.reason or outcome.decision.value
        )
        return Status.FAILURE

    def terminate(self, status: Status) -> None:
        if (
            status is Status.INVALID
            and self.future is not None
            and not self.future.done()
        ):
            self.future.add_done_callback(self._log.add_late)


class SelectionState:
    """The chosen branch for one anchor; shared by the selector children."""

    def __init__(self) -> None:
        self.selected: str | None = None


class SelectionLeaf(py_trees.behaviour.Behaviour):
    """Evaluate the declared branches once after their anchor main step."""

    def __init__(
        self,
        after_step: str,
        plan: Plan,
        main_outcomes: dict[str, StepOutcome],
        state: SelectionState,
        selections: list[SelectionRecord],
        supervisor: RunSupervisor,
    ) -> None:
        super().__init__(f"select-after-{after_step}")
        self._after = after_step
        self._plan = plan
        self._main_outcomes = main_outcomes
        self._state = state
        self._selections = selections
        self._supervisor = supervisor

    def update(self) -> Status:
        record, branch = select_branch(
            self._plan, self._after, self._main_outcomes
        )
        if record is not None:
            self._selections.append(record)
        self._state.selected = branch.id if branch is not None else None
        if branch is None:
            required = any(
                candidate.after == self._after and candidate.required
                for candidate in self._plan.branches
            )
            if required:
                reason = (
                    f"required branch after {self._after!r} has no eligible "
                    "candidate"
                )
                self._supervisor.request_stop(reason)
                return Status.FAILURE
        return Status.SUCCESS


class BranchCondition(py_trees.behaviour.Behaviour):
    """SUCCESS only for the selected branch; the None child is the fallback."""

    def __init__(self, state: SelectionState, branch_id: str | None) -> None:
        super().__init__(f"is-{branch_id or 'none'}")
        self._state = state
        self._branch_id = branch_id

    def update(self) -> Status:
        if self._branch_id is None:
            return (
                Status.SUCCESS
                if self._state.selected is None
                else Status.FAILURE
            )
        return (
            Status.SUCCESS
            if self._state.selected == self._branch_id
            else Status.FAILURE
        )


def _state_for(decision: Decision) -> RunState:
    return {
        Decision.FAILED: RunState.FAILED,
        Decision.ABORTED: RunState.ABORTED,
        Decision.INDETERMINATE: RunState.INDETERMINATE,
    }[decision]


def _stopped_step(step: PlanStep, reason: str) -> StepReport:
    lowered = reason.lower()
    if "cancel" in lowered:
        code = "CANCELED"
    elif "deadline" in lowered:
        code = "DEADLINE"
    else:
        code = "STOPPED"
    return StepReport(
        index=step.index,
        name=step.name,
        outcome="UNKNOWN",
        reason_code=code,
        message=reason,
        intent_id=f"app-{step.idempotency_key[:24]}",
        idempotency_key=step.idempotency_key,
        bus_verified=None,
        safety_state=None,
    )


def run_tree(
    gateway: Any,
    recipe: Recipe,
    authorization: Authorization,
    *,
    plan: Plan,
    run_id: str,
    lease_id: str,
    deadline_s: float | None = None,
    clock: Callable[[], datetime] = _now,
) -> RunReport:
    """Execute a prebuilt plan with the tree engine.

    Preflight refusals raise; ``run_supervised`` turns them into an ``ABORTED``
    report without a submission.
    """

    check_authorization(recipe, authorization)
    check_contract(gateway.contract())
    check_safety(gateway.safety())
    check_lease(lease_id)

    started_at = _stamp(clock())
    log = StepLog()
    main_outcomes: dict[str, StepOutcome] = {}
    selections: list[SelectionRecord] = []
    selection_state = SelectionState()
    worker = _DaemonWorker()
    executor = StepExecutor(gateway)
    supervisor = RunSupervisor(gateway.stop, deadline_s)
    all_leaves: list[SubmitLeaf] = []
    children: list[py_trees.behaviour.Behaviour] = []
    for step in plan.steps:
        main_leaf = SubmitLeaf(
            step,
            executor,
            worker,
            log,
            supervisor,
            run_id,
            lease_id,
            record_main=True,
            main_outcomes=main_outcomes,
        )
        all_leaves.append(main_leaf)
        children.append(main_leaf)

        anchored = [
            branch for branch in plan.branches if branch.after == step.name
        ]
        if not anchored:
            continue
        children.append(
            SelectionLeaf(
                step.name,
                plan,
                main_outcomes,
                selection_state,
                selections,
                supervisor,
            )
        )
        branch_children: list[py_trees.behaviour.Behaviour] = []
        for branch in anchored:
            branch_leaves: list[py_trees.behaviour.Behaviour] = []
            for branch_step in branch.steps:
                leaf = SubmitLeaf(
                    branch_step,
                    executor,
                    worker,
                    log,
                    supervisor,
                    run_id,
                    lease_id,
                    record_main=False,
                    main_outcomes=main_outcomes,
                )
                all_leaves.append(leaf)
                branch_leaves.append(leaf)
            branch_children.append(
                py_trees.composites.Sequence(
                    branch.id,
                    memory=True,
                    children=[
                        BranchCondition(selection_state, branch.id),
                        *branch_leaves,
                    ],
                )
            )
        branch_children.append(BranchCondition(selection_state, None))
        children.append(
            py_trees.composites.Selector(
                f"branches-after-{step.name}",
                memory=True,
                children=branch_children,
            )
        )

    tree = py_trees.trees.BehaviourTree(
        py_trees.composites.Sequence("plan", memory=True, children=children)
    )
    supervisor.start()
    try:
        while True:
            if supervisor.halted:
                tree.root.stop(Status.INVALID)
                break
            tree.tick()
            if tree.root.status in (Status.SUCCESS, Status.FAILURE):
                break
            time.sleep(0.02)
    finally:
        supervisor.close()

    state = RunState.SUCCEEDED
    abort_reason: str | None = None
    operator_required = False
    operator_reason: str | None = None
    in_flight = False
    reports: list[StepReport] = []
    for outcome in list(log.outcomes):
        reports.append(outcome.report)
        if outcome.decision is not Decision.CONTINUE:
            state = _state_for(outcome.decision)
            abort_reason = outcome.reason
            if outcome.decision in (
                Decision.ABORTED,
                Decision.INDETERMINATE,
            ):
                operator_required = True
                operator_reason = outcome.reason
    for leaf in all_leaves:
        if leaf.started and leaf.outcome is None:
            in_flight = True
            reason = supervisor.reason or "run stopped"
            reports.append(_stopped_step(leaf.step, reason))
            state = RunState.INDETERMINATE
            abort_reason = reason

    if supervisor.halted:
        if in_flight:
            state = RunState.INDETERMINATE
        elif state is RunState.SUCCEEDED:
            # A required branch had no candidate, or a stop/deadline arrived
            # after the last completed step; the run is not a success.
            state = RunState.ABORTED
        if abort_reason is None:
            abort_reason = supervisor.reason or "run stopped"
        reason = supervisor.reason or ""
        if (
            "required branch" in reason
            or reason == "deadline"
            or "cancel" in reason.lower()
        ):
            operator_required = True
            operator_reason = reason
    if supervisor.stop_error is not None:
        operator_required = True
        operator_reason = f"stop request failed: {supervisor.stop_error}"
    stop_confirmation, physical_stop = receipt_fields(supervisor.stop_receipt)

    return make_report(
        recipe=recipe,
        run_id=run_id,
        operator=authorization.operator,
        session_id=authorization.session_id,
        state=state,
        started_at=started_at,
        finished_at=_stamp(clock()),
        steps=tuple(reports),
        abort_reason=abort_reason,
        stop_error=supervisor.stop_error,
        selections=tuple(selections),
        operator_required=operator_required,
        operator_reason=operator_reason,
        stop_confirmation=stop_confirmation,
        physical_stop=physical_stop,
    )
