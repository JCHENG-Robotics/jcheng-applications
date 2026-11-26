"""M1.5 isolated validation of the candidate py_trees runtime.

Run with an interpreter that has ``py_trees==2.6.0`` and no ROS:

    /tmp/jcheng-m15-venv/bin/python scripts/m1_5_pytrees_probe.py --out report.json

What this proves, and what it does not:

- It proves the tree mechanics the design depends on: non-blocking ``RUNNING``
  leaves, one worker, sequence memory, ``terminate(INVALID)`` on stop, a late
  future never becoming success, a hung worker not blocking the tick loop, the
  app-side stop-race rule, and nominal gateway-call parity with the unmodified
  linear runner.
- It does not prove failure-path parity, gateway behaviour, concurrency or
  anything about hardware. It does not modify ``runner.py``; the Shared
  Execution Core extraction is a later stage.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib.metadata as metadata
import json
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import py_trees
except ModuleNotFoundError:
    print("ERROR: py_trees is not installed in this interpreter", file=sys.stderr)
    raise SystemExit(2)

from jcheng_applications.gateway import (  # noqa: E402
    ContractInfo,
    MotionResult,
    SafetySnapshot,
)
from jcheng_applications.model import (  # noqa: E402
    CONTRACT_HASH,
    Authorization,
    build_plan,
    load_recipe,
    requires_bus_verification,
)
from jcheng_applications.runner import TaskRunner, build_command  # noqa: E402

Status = py_trees.common.Status
NOW = "2026-11-26T00:00:00+00:00"


def _contract() -> ContractInfo:
    return ContractInfo(
        major=0,
        minor=1,
        patch=0,
        contract_hash=CONTRACT_HASH,
        profile_hash="profile-m15",
        system_version="0.1.0",
    )


def _safety() -> SafetySnapshot:
    return SafetySnapshot(
        state="ARMED_IDLE", stop_latched=False, seq=1, last_valid_state_at=NOW
    )


def _completed() -> MotionResult:
    return MotionResult(
        intent_id="intent",
        outcome="COMPLETED",
        reason_code="OK",
        message="",
        bus_verified=True,
        safety_state="ARMED_IDLE",
        stop_latched=False,
    )


class LoggingGateway:
    """A typed fake gateway that records submitted commands."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.stops: list[str] = []

    def lease_id(self) -> str:
        return "lease-m15"

    def contract(self) -> ContractInfo:
        return _contract()

    def safety(self) -> SafetySnapshot:
        return _safety()

    def submit(self, command: dict[str, Any]) -> MotionResult:
        self.calls.append(json.loads(json.dumps(command, sort_keys=True)))
        return _completed()

    def stop(self, reason: str) -> None:
        self.stops.append(reason)


class FutureLeaf(py_trees.behaviour.Behaviour):
    """A leaf that polls a future and never blocks the tick."""

    def __init__(self, name: str, executor: ThreadPoolExecutor, delay_s: float) -> None:
        super().__init__(name)
        self._executor = executor
        self._delay_s = delay_s
        self.future = None
        self.initialise_count = 0
        self.terminate_status: Status | None = None

    def initialise(self) -> None:
        self.initialise_count += 1
        self.future = self._executor.submit(time.sleep, self._delay_s)

    def update(self) -> Status:
        if self.future is not None and self.future.done():
            return Status.SUCCESS
        return Status.RUNNING

    def terminate(self, status: Status) -> None:
        self.terminate_status = status


class HangLeaf(py_trees.behaviour.Behaviour):
    """A leaf whose worker never returns; the deadline must halt the tree."""

    def __init__(self, name: str, delay_s: float) -> None:
        super().__init__(name)
        self._delay_s = delay_s
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at = 0.0

    def initialise(self) -> None:
        self.started_at = time.monotonic()

        def work() -> None:
            time.sleep(self._delay_s)
            self._done.set()

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def update(self) -> Status:
        return Status.SUCCESS if self._done.is_set() else Status.RUNNING


class CountingLeaf(py_trees.behaviour.Behaviour):
    def __init__(self, name: str, ticks_before_success: int) -> None:
        super().__init__(name)
        self._remaining = ticks_before_success
        self.ticks = 0
        self.initialise_count = 0

    def initialise(self) -> None:
        self.initialise_count += 1

    def update(self) -> Status:
        self.ticks += 1
        if self._remaining <= 0:
            return Status.SUCCESS
        self._remaining -= 1
        return Status.RUNNING


class SubmitLeaf(py_trees.behaviour.Behaviour):
    """Submit one pre-built command through an executor and poll the future."""

    def __init__(
        self,
        name: str,
        command: dict[str, Any],
        executor: ThreadPoolExecutor,
        gateway: LoggingGateway,
    ) -> None:
        super().__init__(name)
        self._command = command
        self._executor = executor
        self._gateway = gateway
        self.future = None

    def initialise(self) -> None:
        self.future = self._executor.submit(self._gateway.submit, self._command)

    def update(self) -> Status:
        if self.future is None or not self.future.done():
            return Status.RUNNING
        return Status.SUCCESS


class RaceLeaf(py_trees.behaviour.Behaviour):
    """A slow transport call whose late completion must never become success."""

    def __init__(
        self,
        name: str,
        intent: str,
        executor: ThreadPoolExecutor,
        transport: "RaceTransport",
    ) -> None:
        super().__init__(name)
        self._intent = intent
        self._executor = executor
        self._transport = transport
        self.future = None
        self.late = False
        self.terminate_status: Status | None = None

    def initialise(self) -> None:
        self.future = self._executor.submit(self._transport.execute, self._intent)

    def update(self) -> Status:
        if self.future is None or not self.future.done():
            return Status.RUNNING
        if self._transport.stopped.is_set():
            self.late = True
            return Status.FAILURE
        return Status.SUCCESS

    def terminate(self, status: Status) -> None:
        self.terminate_status = status
        if status is Status.INVALID and self.future is not None and not self.future.done():
            self.future.add_done_callback(lambda _future: setattr(self, "late", True))


def _tick_until(
    tree: py_trees.trees.BehaviourTree,
    *,
    timeout_s: float,
    period_s: float = 0.02,
) -> tuple[list[float], Status]:
    gaps: list[float] = []
    previous = time.monotonic()
    deadline = previous + timeout_s
    while time.monotonic() < deadline:
        tree.tick()
        now = time.monotonic()
        gaps.append(now - previous)
        previous = now
        if tree.root.status in (Status.SUCCESS, Status.FAILURE):
            return gaps, tree.root.status
        time.sleep(period_s)
    return gaps, tree.root.status


def check_metadata() -> dict[str, Any]:
    md = metadata.metadata("py_trees")
    requires = md.get_all("Requires-Dist") or []
    version = metadata.version("py_trees")
    license_text = md.get("License") or ""
    passed = (
        version.startswith("2.6.")
        and "BSD-3-Clause" in license_text
        and any(item.startswith("pydot") for item in requires)
    )
    return {
        "id": "M15-A",
        "name": "candidate metadata and transitive dependency",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "version": version,
            "license": license_text,
            "requires_dist": requires,
        },
        "detail": "pydot is a transitive runtime dependency and enters the license review",
    }


def check_non_blocking() -> dict[str, Any]:
    executor = ThreadPoolExecutor(max_workers=1)
    leaf = FutureLeaf("submit", executor, 0.3)
    tree = py_trees.trees.BehaviourTree(leaf)
    started = time.monotonic()
    gaps, status = _tick_until(tree, timeout_s=2.0)
    elapsed = time.monotonic() - started
    executor.shutdown(wait=True)
    passed = (
        status is Status.SUCCESS
        and leaf.initialise_count == 1
        and len(gaps) >= 5
        and max(gaps) < 0.2
        and elapsed < 1.0
    )
    return {
        "id": "M15-B",
        "name": "non-blocking RUNNING leaf",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "final_status": status.value,
            "ticks": len(gaps),
            "initialise_count": leaf.initialise_count,
            "max_tick_gap_s": round(max(gaps), 4),
            "elapsed_s": round(elapsed, 4),
        },
    }


def check_sequence_memory() -> dict[str, Any]:
    first = CountingLeaf("first", ticks_before_success=3)
    second = CountingLeaf("second", ticks_before_success=0)
    root = py_trees.composites.Sequence("seq", memory=True, children=[first, second])
    tree = py_trees.trees.BehaviourTree(root)
    gaps, status = _tick_until(tree, timeout_s=2.0)
    passed = (
        status is Status.SUCCESS
        and first.initialise_count == 1
        and second.ticks == 1
        and second.initialise_count == 1
    )
    return {
        "id": "M15-C",
        "name": "sequence memory re-ticks only the running child",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "final_status": status.value,
            "first_initialise": first.initialise_count,
            "first_ticks": first.ticks,
            "second_ticks": second.ticks,
            "second_initialise": second.initialise_count,
        },
    }


def check_cancel_terminate() -> dict[str, Any]:
    executor = ThreadPoolExecutor(max_workers=1)
    leaf = FutureLeaf("submit", executor, 0.4)
    tree = py_trees.trees.BehaviourTree(leaf)
    tree.tick()
    tree.tick()
    tree.root.stop(Status.INVALID)
    terminated = leaf.terminate_status
    executor.shutdown(wait=True)
    passed = terminated is Status.INVALID
    return {
        "id": "M15-D",
        "name": "stop calls terminate(INVALID) and drains the worker",
        "status": "PASS" if passed else "FAIL",
        "observed": {"terminate_status": terminated.value if terminated else None},
    }


def check_late_future() -> dict[str, Any]:
    transport = RaceTransport()
    executor = ThreadPoolExecutor(max_workers=1)
    leaf = RaceLeaf("submit", "late-1", executor, transport)
    tree = py_trees.trees.BehaviourTree(leaf)
    tree.tick()
    transport.stopped.set()
    tree.root.stop(Status.INVALID)
    time.sleep(0.5)
    executor.shutdown(wait=True)
    passed = leaf.late is True and leaf.status is Status.INVALID
    return {
        "id": "M15-E",
        "name": "late future after stop is not success",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "late_flag": leaf.late,
            "leaf_status": leaf.status.value,
            "terminate_status": leaf.terminate_status.value if leaf.terminate_status else None,
            "transport_terminal": transport.terminal,
            "supervisor_terminal": "INDETERMINATE",
        },
    }


def check_worker_hang() -> dict[str, Any]:
    leaf = HangLeaf("hang", 5.0)
    tree = py_trees.trees.BehaviourTree(leaf)
    started = time.monotonic()
    gaps, status = _tick_until(tree, timeout_s=0.3)
    tree.root.stop(Status.INVALID)
    elapsed = time.monotonic() - started
    passed = status is Status.RUNNING and elapsed < 1.0
    return {
        "id": "M15-F",
        "name": "hung worker does not block the tick loop",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "final_status": status.value,
            "elapsed_s": round(elapsed, 4),
            "ticks": len(gaps),
            "note": "the worker thread is daemon; a wedged real call still needs a transport timeout and process restart",
        },
    }


class RaceTransport:
    def __init__(self) -> None:
        self.stopped = threading.Event()
        self.submissions = 0
        self.terminal: str | None = None
        self.stop_thread: str | None = None

    def execute(self, intent: str) -> str:
        self.submissions += 1
        time.sleep(0.25)
        if self.stopped.is_set():
            self.terminal = "COMPLETED_AFTER_STOP"
            return "COMPLETED"
        self.terminal = "COMPLETED"
        return "COMPLETED"

    def stop(self, reason: str) -> None:
        self.stop_thread = threading.current_thread().name
        self.stopped.set()


def check_stop_race() -> dict[str, Any]:
    transport = RaceTransport()
    executor = ThreadPoolExecutor(max_workers=1)
    first = RaceLeaf("first", "race-1", executor, transport)
    second = RaceLeaf("second", "race-2", executor, transport)
    root = py_trees.composites.Sequence("race", memory=True, children=[first, second])
    tree = py_trees.trees.BehaviourTree(root)
    tree.tick()
    threading.Timer(0.05, transport.stopped.set).start()
    _, status = _tick_until(tree, timeout_s=2.0)
    executor.shutdown(wait=True)
    passed = (
        status is Status.FAILURE
        and first.late is True
        and transport.submissions == 1
        and transport.terminal == "COMPLETED_AFTER_STOP"
    )
    return {
        "id": "M15-G",
        "name": "app-side stop race: late COMPLETED cannot advance the plan",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "final_status": status.value,
            "late_flag": first.late,
            "submissions": transport.submissions,
            "transport_terminal": transport.terminal,
            "claimed_run_state": "INDETERMINATE",
        },
    }


def check_single_worker() -> dict[str, Any]:
    active = 0
    peak = 0
    lock = threading.Lock()

    def work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.1)
        with lock:
            active -= 1

    executor = ThreadPoolExecutor(max_workers=1)

    class RunWork(py_trees.behaviour.Behaviour):
        def __init__(self, name: str) -> None:
            super().__init__(name)
            self.future = None

        def initialise(self) -> None:
            self.future = executor.submit(work)

        def update(self) -> Status:
            return Status.SUCCESS if self.future and self.future.done() else Status.RUNNING

    left = RunWork("left")
    right = RunWork("right")
    root = py_trees.composites.Parallel(
        "parallel",
        policy=py_trees.common.ParallelPolicy.SuccessOnAll(),
        children=[left, right],
    )
    tree = py_trees.trees.BehaviourTree(root)
    _tick_until(tree, timeout_s=2.0)
    executor.shutdown(wait=True)
    passed = peak == 1
    return {
        "id": "M15-H",
        "name": "one worker means one in-flight submission",
        "status": "PASS" if passed else "FAIL",
        "observed": {"peak_concurrency": peak},
    }


def check_golden_trace() -> dict[str, Any]:
    recipe = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
    plan = build_plan(recipe, "m15-run")
    auth = Authorization(
        operator="operator",
        session_id="m15-session",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )
    linear_gateway = LoggingGateway()
    linear = TaskRunner(
        linear_gateway,
        clock=lambda: datetime.fromisoformat(NOW),
    ).run(recipe, auth, run_id="m15-run")

    tree_gateway = LoggingGateway()
    executor = ThreadPoolExecutor(max_workers=1)
    leaves = []
    for step in plan.steps:
        command = build_command(step, run_id="m15-run", lease_id="lease-m15")
        leaves.append(SubmitLeaf(step.name, command, executor, tree_gateway))
    tree = py_trees.trees.BehaviourTree(
        py_trees.composites.Sequence("plan", memory=True, children=leaves)
    )
    _, status = _tick_until(tree, timeout_s=3.0)
    executor.shutdown(wait=True)

    def canonical(commands: list[dict[str, Any]]) -> list[str]:
        return [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in commands]

    passed = (
        status is Status.SUCCESS
        and canonical(linear_gateway.calls) == canonical(tree_gateway.calls)
        and linear.steps[0].idempotency_key
        == (tree_gateway.calls[0]["idempotency_key"] if tree_gateway.calls else None)
        and linear_gateway.stops == tree_gateway.stops == []
    )
    return {
        "id": "M15-I",
        "name": "nominal gateway-call parity with the unmodified linear runner",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "linear_submissions": len(linear_gateway.calls),
            "tree_submissions": len(tree_gateway.calls),
            "linear_keys": [step.idempotency_key[:16] for step in linear.steps],
            "tree_keys": [
                str(item.get("idempotency_key", ""))[:16]
                for item in tree_gateway.calls
            ],
            "stop_calls": {"linear": len(linear_gateway.stops), "tree": len(tree_gateway.stops)},
        },
        "detail": "failure-path parity and report parity are deferred to Shared Core Regression",
    }


class Supervisor:
    """A minimal independent supervision thread for the M1.5 mechanics probe."""

    def __init__(self) -> None:
        self._events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.deadline: float | None = None
        self.latched = False
        self.state = "INIT"
        self.stop_sink = None
        self.stop_calls: list[dict[str, Any]] = []

    def start(self, deadline_s: float | None = None) -> None:
        self.state = "RUNNING"
        self.deadline = time.monotonic() + deadline_s if deadline_s is not None else None
        self._thread = threading.Thread(
            target=self._run, name="m15-supervisor", daemon=True
        )
        self._thread.start()

    def request_stop(self, reason: str) -> None:
        self._events.put(("stop", reason))

    def _run(self) -> None:
        while not self._halt.is_set():
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self._issue_stop("deadline")
            try:
                item = self._events.get(timeout=0.02)
            except queue.Empty:
                continue
            if item[0] == "stop":
                self._issue_stop(item[1])

    def _issue_stop(self, reason: str) -> None:
        with self._lock:
            if self.latched:
                return
            self.latched = True
            self.state = "STOPPING"
            error = None
            try:
                if self.stop_sink is not None:
                    self.stop_sink(reason)
            except Exception as exc:  # noqa: BLE001 - the probe records it
                error = type(exc).__name__
            self.stop_calls.append(
                {
                    "reason": reason,
                    "thread": threading.current_thread().name,
                    "error": error,
                }
            )
            self.state = "WAITING_FOR_OPERATOR"

    def close(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.state = "CLOSED"


def check_supervisor_thread() -> dict[str, Any]:
    transport = RaceTransport()
    executor = ThreadPoolExecutor(max_workers=1)
    leaf = RaceLeaf("submit", "sup-1", executor, transport)
    tree = py_trees.trees.BehaviourTree(leaf)
    tree.tick()
    supervisor = Supervisor()
    supervisor.stop_sink = transport.stop
    supervisor.start(deadline_s=0.05)
    _, status = _tick_until(tree, timeout_s=2.0)
    state_before_close = supervisor.state
    supervisor.close()
    executor.shutdown(wait=True)
    passed = (
        status is Status.FAILURE
        and supervisor.latched
        and len(supervisor.stop_calls) == 1
        and supervisor.stop_calls[0]["thread"] == "m15-supervisor"
        and supervisor.stop_calls[0]["error"] is None
        and state_before_close == "WAITING_FOR_OPERATOR"
        and leaf.late is True
        and transport.submissions == 1
    )
    return {
        "id": "M15-J",
        "name": "independent supervisor thread issues the stop on deadline",
        "status": "PASS" if passed else "FAIL",
        "observed": {
            "final_status": status.value,
            "supervisor_latched": supervisor.latched,
            "stop_calls": supervisor.stop_calls,
            "supervisor_state_before_close": state_before_close,
            "late_flag": leaf.late,
            "submissions": transport.submissions,
            "supervisor_terminal": "INDETERMINATE",
        },
    }


class ScriptedGateway:
    """A typed fake gateway with scripted results and a call log."""

    def __init__(
        self,
        results: list[Any],
        *,
        submit_error_at: int | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.results = list(results)
        self.submit_error_at = submit_error_at
        self.stop_error = stop_error
        self.calls: list[dict[str, Any]] = []
        self.stops: list[str] = []

    def lease_id(self) -> str:
        return "lease-m15"

    def contract(self) -> ContractInfo:
        return _contract()

    def safety(self) -> SafetySnapshot:
        return _safety()

    def submit(self, command: dict[str, Any]) -> Any:
        self.calls.append(json.loads(json.dumps(command, sort_keys=True)))
        if self.submit_error_at is not None and len(self.calls) - 1 == self.submit_error_at:
            raise RuntimeError("scripted submit failure")
        if not self.results:
            raise RuntimeError("script exhausted")
        return self.results.pop(0)

    def stop(self, reason: str) -> None:
        self.stops.append(reason)
        if self.stop_error is not None:
            raise self.stop_error


def _result(
    outcome: str = "COMPLETED",
    *,
    code: str = "OK",
    bus_verified: bool = True,
    safety_state: str = "ARMED_IDLE",
    stop_latched: bool = False,
) -> MotionResult:
    return MotionResult(
        intent_id="intent",
        outcome=outcome,
        reason_code=code,
        message="",
        bus_verified=bus_verified,
        safety_state=safety_state,
        stop_latched=stop_latched,
    )


def decide(
    step: Any,
    outcome: str,
    bus_verified: bool | None,
    safety_state: str | None,
    stop_latched: bool | None,
) -> str:
    """Mirror of the linear runner's post-submission decision table.

    This is a temporary mirror for the differential; the Shared Execution Core
    extraction will replace it with the real implementation.
    """

    if stop_latched or safety_state in {"STOP_LATCHED", "FAULT", "DISARMED"}:
        return "ABORTED"
    if outcome == "COMPLETED":
        if requires_bus_verification(step.scopes) and not bus_verified:
            return "INDETERMINATE"
        return "CONTINUE"
    if outcome == "REJECTED":
        return "FAILED"
    if outcome == "ABORTED":
        return "ABORTED"
    return "INDETERMINATE"


class ClassifiedLeaf(py_trees.behaviour.Behaviour):
    def __init__(
        self,
        step: Any,
        executor: ThreadPoolExecutor,
        gateway: ScriptedGateway,
        run_id: str,
        lease_id: str,
    ) -> None:
        super().__init__(step.name)
        self._step = step
        self._executor = executor
        self._gateway = gateway
        self._run_id = run_id
        self._lease_id = lease_id
        self.future = None
        self.submitted = False
        self.outcome: str | None = None
        self.reason_code: str | None = None
        self.bus_verified: bool | None = None
        self.decision: str | None = None

    def initialise(self) -> None:
        self.submitted = True
        self.future = self._executor.submit(self._work)

    def _work(self) -> tuple[str, str, str, bool | None, str | None, bool | None]:
        try:
            command = build_command(
                self._step, run_id=self._run_id, lease_id=self._lease_id
            )
            result = self._gateway.submit(command)
        except Exception as exc:  # noqa: BLE001 - mirror the runner's UNKNOWN
            return ("UNKNOWN", type(exc).__name__, str(exc), None, None, None)
        if not isinstance(result, MotionResult):
            return (
                "UNKNOWN",
                "GatewayProtocolError",
                "gateway returned an unexpected result type",
                None,
                None,
                None,
            )
        return (
            result.outcome,
            result.reason_code,
            result.message,
            result.bus_verified,
            result.safety_state,
            result.stop_latched,
        )

    def update(self) -> Status:
        if self.future is None or not self.future.done():
            return Status.RUNNING
        outcome, code, _message, bus, safety, latched = self.future.result()
        self.outcome = outcome
        self.reason_code = code
        self.bus_verified = bus
        self.decision = decide(self._step, outcome, bus, safety, latched)
        return Status.SUCCESS if self.decision == "CONTINUE" else Status.FAILURE


def run_tree_plan(
    plan: Any,
    gateway: ScriptedGateway,
    *,
    run_id: str,
    lease_id: str,
) -> tuple[str, list[ClassifiedLeaf], str | None]:
    executor = ThreadPoolExecutor(max_workers=1)
    leaves = [
        ClassifiedLeaf(step, executor, gateway, run_id, lease_id)
        for step in plan.steps
    ]
    tree = py_trees.trees.BehaviourTree(
        py_trees.composites.Sequence("plan", memory=True, children=leaves)
    )
    _tick_until(tree, timeout_s=5.0)
    executor.shutdown(wait=True)
    state = "SUCCEEDED"
    for leaf in leaves:
        if leaf.decision is not None and leaf.decision != "CONTINUE":
            state = leaf.decision
            break
    submitted = sum(1 for leaf in leaves if leaf.submitted)
    stop_error = None
    if state != "SUCCEEDED" and submitted:
        try:
            gateway.stop(f"step failure: {state}")
        except Exception as exc:  # noqa: BLE001
            stop_error = f"{type(exc).__name__}: {exc}"
    return state, leaves, stop_error


def check_failure_differential() -> dict[str, Any]:
    recipe = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
    plan = build_plan(recipe, "m15-run")
    auth = Authorization(
        operator="operator",
        session_id="m15-session",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )
    cases = [
        ("all-success", [_result(), _result()], None, None),
        ("unverified", [_result(), _result(bus_verified=False)], None, None),
        ("rejected-first", [_result("REJECTED", code="X")], None, None),
        ("rejected-second", [_result(), _result("REJECTED", code="X")], None, None),
        ("aborted", [_result("ABORTED", code="X")], None, None),
        ("timeout", [_result("TIMEOUT", code="X", bus_verified=False)], None, None),
        ("unknown", [_result("UNKNOWN", code="X", bus_verified=False)], None, None),
        ("submit-raises", [], 0, None),
        (
            "safety-latch-mid",
            [_result(), _result(safety_state="STOP_LATCHED", stop_latched=True)],
            None,
            None,
        ),
        ("stop-raises", [_result("REJECTED", code="X")], None, RuntimeError("stop")),
        ("malformed", [_result(), {"outcome": "COMPLETED"}], None, None),
    ]
    failures: list[dict[str, Any]] = []
    for name, results, submit_error_at, stop_error in cases:
        linear_gateway = ScriptedGateway(
            results, submit_error_at=submit_error_at, stop_error=stop_error
        )
        linear = TaskRunner(
            linear_gateway, clock=lambda: datetime.fromisoformat(NOW)
        ).run(recipe, auth, run_id="m15-run")
        tree_gateway = ScriptedGateway(
            results, submit_error_at=submit_error_at, stop_error=stop_error
        )
        tree_state, leaves, tree_stop_error = run_tree_plan(
            plan, tree_gateway, run_id="m15-run", lease_id="lease-m15"
        )

        def canonical(commands: list[dict[str, Any]]) -> list[str]:
            return [
                json.dumps(item, sort_keys=True, separators=(",", ":"))
                for item in commands
            ]

        linear_steps = [
            (step.outcome, step.reason_code, step.bus_verified)
            for step in linear.steps
        ]
        tree_steps = [
            (leaf.outcome, leaf.reason_code, leaf.bus_verified)
            for leaf in leaves
            if leaf.submitted
        ]
        checks = {
            "state": linear.state.value == tree_state,
            "commands": canonical(linear_gateway.calls) == canonical(tree_gateway.calls),
            "step_count": len(linear_steps) == len(tree_steps),
            "steps": linear_steps == tree_steps,
            "stop_count": len(linear_gateway.stops) == len(tree_gateway.stops),
            "stop_error": (linear.stop_error is not None) == (tree_stop_error is not None),
        }
        if not all(checks.values()):
            failures.append({"case": name, "checks": checks})
    return {
        "id": "M15-K",
        "name": "failure-path differential: tree mirror vs the unmodified linear runner",
        "status": "PASS" if not failures else "FAIL",
        "observed": {
            "cases": len(cases),
            "failures": failures,
            "compared": [
                "final state",
                "submitted command sequence, parameters and idempotency keys",
                "step outcome, reason code and bus verification",
                "stop count and stop-error presence",
            ],
            "note": "the decision mirror is temporary and is replaced by the Shared Execution Core (T-C06)",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    checks = [
        check_metadata(),
        check_non_blocking(),
        check_sequence_memory(),
        check_cancel_terminate(),
        check_late_future(),
        check_worker_hang(),
        check_stop_race(),
        check_single_worker(),
        check_golden_trace(),
        check_supervisor_thread(),
        check_failure_differential(),
    ]
    summary = Counter(check["status"] for check in checks)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.version.split()[0],
        "runtime": "py_trees (candidate, isolated; not a repository dependency)",
        "runner_modified": False,
        "shared_core_extracted": False,
        "checks": checks,
        "summary": dict(summary),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    return 0 if summary.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
