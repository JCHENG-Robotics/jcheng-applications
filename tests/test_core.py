"""Shared execution core: classification, submission and stop-once."""

from __future__ import annotations

from typing import Any

from jcheng_applications.core.classify import Decision, classify
from jcheng_applications.core.executor import StepExecutor
from jcheng_applications.core.stop import StopOnce
from jcheng_applications.gateway import MotionResult
from jcheng_applications.model import PlanStep


def _step(scopes: tuple[str, ...] = ("gripper",), index: int = 1) -> PlanStep:
    return PlanStep(
        index=index,
        name="grasp",
        kind="GRIPPER",
        profile="gripper",
        scopes=scopes,
        params={},
        idempotency_key="key-1",
    )


def _result(
    outcome: str = "COMPLETED",
    *,
    code: str = "OK",
    bus_verified: bool = True,
    safety_state: str = "ARMED_IDLE",
    stop_latched: bool = False,
) -> MotionResult:
    return MotionResult(
        intent_id="intent-1",
        outcome=outcome,
        reason_code=code,
        message="message",
        bus_verified=bus_verified,
        safety_state=safety_state,
        stop_latched=stop_latched,
    )


def test_classification_table() -> None:
    assert classify(_step(), _result()).decision is Decision.CONTINUE
    unverified = classify(_step(), _result(bus_verified=False))
    assert unverified.decision is Decision.INDETERMINATE
    assert "verified bus read" in str(unverified.reason)
    # The base has no encoder, so it does not require a bus read.
    assert classify(_step(("base",)), _result(bus_verified=False)).decision is Decision.CONTINUE

    rejected = classify(_step(), _result("REJECTED", code="X"))
    assert rejected.decision is Decision.FAILED
    assert "rejected" in str(rejected.reason)
    assert classify(_step(), _result("ABORTED", code="X")).decision is Decision.ABORTED
    assert classify(_step(), _result("TIMEOUT", code="X")).decision is Decision.INDETERMINATE
    assert classify(_step(), _result("UNKNOWN", code="X")).decision is Decision.INDETERMINATE


def test_safety_latch_wins_over_completed() -> None:
    latched = classify(_step(), _result(stop_latched=True))
    assert latched.decision is Decision.ABORTED
    assert "stop latched" in str(latched.reason)
    state_latched = classify(_step(), _result(safety_state="STOP_LATCHED"))
    assert state_latched.decision is Decision.ABORTED
    assert classify(_step(), _result(safety_state="FAULT")).decision is Decision.ABORTED


class _SubmitGateway:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0

    def submit(self, command: dict[str, Any]) -> Any:
        self.calls += 1
        return self.result


class _RaisingGateway:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, command: dict[str, Any]) -> Any:
        self.calls += 1
        raise RuntimeError("transport failed")


def test_step_executor_success() -> None:
    gateway = _SubmitGateway(_result())
    outcome = StepExecutor(gateway).submit(_step(), run_id="run-1", lease_id="lease-1")
    assert outcome.decision is Decision.CONTINUE
    assert outcome.report.outcome == "COMPLETED"
    assert outcome.report.intent_id == "intent-1"
    assert gateway.calls == 1


def test_step_executor_exception_is_unknown_and_not_retried() -> None:
    gateway = _RaisingGateway()
    outcome = StepExecutor(gateway).submit(_step(), run_id="run-1", lease_id="lease-1")
    assert outcome.decision is Decision.INDETERMINATE
    assert outcome.report.outcome == "UNKNOWN"
    assert outcome.report.reason_code == "RuntimeError"
    assert "submission outcome unknown" in str(outcome.reason)
    assert gateway.calls == 1


def test_step_executor_malformed_result_is_protocol_error() -> None:
    gateway = _SubmitGateway({"outcome": "COMPLETED"})
    outcome = StepExecutor(gateway).submit(_step(), run_id="run-1", lease_id="lease-1")
    assert outcome.decision is Decision.INDETERMINATE
    assert outcome.report.reason_code == "GatewayProtocolError"
    assert gateway.calls == 1


class _StopGateway:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def stop(self, reason: str) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("stop failed")


def test_stop_once_requests_at_most_one_stop() -> None:
    gateway = _StopGateway()
    once = StopOnce()
    once.request(gateway, "first")
    once.request(gateway, "second")
    assert gateway.calls == 1
    assert once.error is None


def test_stop_once_records_a_failure() -> None:
    gateway = _StopGateway(fail=True)
    once = StopOnce()
    once.request(gateway, "first")
    once.request(gateway, "second")
    assert gateway.calls == 1
    assert once.error == "RuntimeError: stop failed"
