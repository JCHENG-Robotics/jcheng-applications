"""Fail-closed runner tests against an in-process fake gateway.

These are L2 (mock integration) tests: no network, no ROS, no device and no
robot. They prove the refusal paths, the terminal-state mapping and the
no-retry rule, not any hardware behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from jcheng_applications.gateway import (
    ContractInfo,
    GatewayProtocolError,
    GatewayUnavailable,
    MotionResult,
    SafetySnapshot,
)
from jcheng_applications.model import (
    CONTRACT_HASH,
    Authorization,
    build_plan,
    load_recipe,
    recipe_from_text,
)
from jcheng_applications.runner import RunState, TaskRunner, build_command

ROOT = Path(__file__).resolve().parents[1]
RECIPE = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
STARTED = datetime(2026, 11, 26, 2, 0, 0, tzinfo=timezone.utc)
FINISHED = datetime(2026, 11, 26, 2, 0, 1, tzinfo=timezone.utc)

BASE_RECIPE = recipe_from_text(
    f"""
name: base-pulse-test
version: 0.1.0
description: Base pulse test.
contract_hash: "{CONTRACT_HASH}"
scopes: [base]
steps:
  - name: nudge
    kind: BASE_PULSE
    profile: base
    scopes: [base]
    params:
      base_pulse:
        duty: [0.1, 0.1, 0.1, 0.1]
        duration_s: 0.2
        require_stopped_after: true
"""
)


def authorization(**overrides) -> Authorization:
    values = {
        "operator": "operator",
        "session_id": "session-001",
        "scopes": frozenset({"arm", "gripper"}),
        "approved": True,
        "approved_at": "2026-11-26T10:00:00+08:00",
    }
    values.update(overrides)
    return Authorization(**values)


def contract(**overrides) -> ContractInfo:
    values = {
        "major": 0,
        "minor": 1,
        "patch": 0,
        "contract_hash": CONTRACT_HASH,
        "profile_hash": "sha256:" + "b" * 64,
        "system_version": "0.1.0",
    }
    values.update(overrides)
    return ContractInfo(**values)


def safety(**overrides) -> SafetySnapshot:
    values = {
        "state": "ARMED_IDLE",
        "stop_latched": False,
        "seq": 3,
        "last_valid_state_at": "2026-11-26T10:00:00+08:00",
    }
    values.update(overrides)
    return SafetySnapshot(**values)


def completed(bus_verified: bool = True, **overrides) -> MotionResult:
    values = {
        "intent_id": "intent-1",
        "outcome": "COMPLETED",
        "reason_code": "OK",
        "message": "",
        "bus_verified": bus_verified,
        "safety_state": "ARMED_IDLE",
        "stop_latched": False,
    }
    values.update(overrides)
    return MotionResult(**values)


class FakeGateway:
    def __init__(
        self,
        *,
        contract_value: ContractInfo | None = None,
        safety_value: SafetySnapshot | None = None,
        lease_id: str = "lease-001",
        results: list[object] | None = None,
        submit_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self._contract = contract_value or contract()
        self._safety = safety_value or safety()
        self._lease_id = lease_id
        self._results = list(results or [])
        self._submit_error = submit_error
        self._stop_error = stop_error
        self.calls: list[dict] = []
        self.stops: list[str] = []

    def lease_id(self) -> str:
        return self._lease_id

    def contract(self) -> ContractInfo:
        return self._contract

    def safety(self) -> SafetySnapshot:
        return self._safety

    def submit(self, command):
        self.calls.append(dict(command))
        if self._submit_error is not None:
            raise self._submit_error
        if self._results:
            return self._results.pop(0)
        return completed()

    def stop(self, reason: str) -> None:
        self.stops.append(reason)
        if self._stop_error is not None:
            raise self._stop_error


def run(gateway: FakeGateway, **overrides):
    clock_values = iter([STARTED, FINISHED])
    runner = TaskRunner(gateway, clock=lambda: next(clock_values))
    return runner.run(
        overrides.pop("recipe", RECIPE),
        overrides.pop("auth", authorization()),
        run_id=overrides.pop("run_id", "run-001"),
        **overrides,
    )


def test_successful_run_records_every_step() -> None:
    gateway = FakeGateway(results=[completed(), completed()])
    report = run(gateway)

    assert report.state is RunState.SUCCEEDED
    assert len(report.steps) == 2
    assert [step.outcome for step in report.steps] == ["COMPLETED", "COMPLETED"]
    assert report.operator == "operator"
    assert report.session_id == "session-001"
    assert report.started_at == STARTED.isoformat()
    assert report.finished_at == FINISHED.isoformat()
    assert report.abort_reason is None
    assert report.stop_error is None
    assert gateway.stops == []
    assert len(gateway.calls) == 2


def test_command_is_a_contract_shaped_request() -> None:
    gateway = FakeGateway(results=[completed(), completed()])
    run(gateway)
    plan = build_plan(RECIPE, "run-001")

    first = gateway.calls[0]
    assert first["intent_id"] == f"app-{plan.steps[0].idempotency_key[:24]}"
    assert first["idempotency_key"] == plan.steps[0].idempotency_key
    assert first["lease_id"] == "lease-001"
    assert first["source"] == "jcheng-applications/run-001"
    assert first["kind"] == "JOINT_STEP"
    assert first["profile"] == "step"
    assert first["contract"] == {
        "major": 0,
        "minor": 1,
        "patch": 0,
        "contract_hash": CONTRACT_HASH,
    }
    assert first["joint_names"] == ["joint1", "joint2", "joint3", "joint4", "joint5"]
    assert first["verify_bus"] is True

    second = gateway.calls[1]
    assert second["kind"] == "GRIPPER"
    assert second["gripper"]["position_rad"] == 0.35


def test_idempotency_keys_are_stable_across_runs() -> None:
    first = FakeGateway(results=[completed(), completed()])
    second = FakeGateway(results=[completed(), completed()])
    run(first)
    run(second)
    assert [call["idempotency_key"] for call in first.calls] == [
        call["idempotency_key"] for call in second.calls
    ]


def test_unapproved_authorization_refuses_before_any_call() -> None:
    gateway = FakeGateway()
    report = run(gateway, auth=authorization(approved=False))
    assert report.state is RunState.ABORTED
    assert "not approved" in report.abort_reason
    assert gateway.calls == []
    assert gateway.stops == []


def test_missing_scope_refuses_before_any_call() -> None:
    gateway = FakeGateway()
    report = run(gateway, auth=authorization(scopes=frozenset({"arm"})))
    assert report.state is RunState.ABORTED
    assert "scope" in report.abort_reason
    assert gateway.calls == []


def test_naive_approval_timestamp_is_refused() -> None:
    gateway = FakeGateway()
    report = run(
        gateway,
        auth=authorization(approved_at="2026-11-26T10:00:00"),
    )
    assert report.state is RunState.ABORTED
    assert "timezone" in report.abort_reason
    assert gateway.calls == []


def test_contract_mismatch_refuses_before_any_call() -> None:
    gateway = FakeGateway(
        contract_value=contract(contract_hash="sha256:" + "0" * 64)
    )
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert "contract" in report.abort_reason
    assert gateway.calls == []


def test_contract_outside_the_range_refuses() -> None:
    gateway = FakeGateway(contract_value=contract(minor=2))
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert gateway.calls == []


def test_safety_must_be_armed_idle() -> None:
    for state, stop_latched in (
        ("DISARMED", False),
        ("MOTION_ACTIVE", False),
        ("STOP_LATCHED", True),
        ("FAULT", False),
        ("ARMED_IDLE", True),
    ):
        gateway = FakeGateway(
            safety_value=safety(state=state, stop_latched=stop_latched)
        )
        report = run(gateway)
        assert report.state is RunState.ABORTED, state
        assert gateway.calls == [], state


def test_missing_lease_refuses_before_any_call() -> None:
    gateway = FakeGateway(lease_id="")
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert "lease_id" in report.abort_reason
    assert gateway.calls == []


def test_rejected_step_fails_and_requests_stop() -> None:
    gateway = FakeGateway(
        results=[
            completed(
                outcome="REJECTED",
                reason_code="LIMIT_VIOLATION",
                message="target outside the workspace",
                bus_verified=False,
            )
        ]
    )
    report = run(gateway)
    assert report.state is RunState.FAILED
    assert "LIMIT_VIOLATION" in report.abort_reason
    assert len(report.steps) == 1
    assert report.steps[0].reason_code == "LIMIT_VIOLATION"
    assert gateway.calls and len(gateway.calls) == 1
    assert len(gateway.stops) == 1


def test_aborted_step_aborts() -> None:
    gateway = FakeGateway(
        results=[completed(outcome="ABORTED", reason_code="PREEMPTED", message="")]
    )
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert len(gateway.calls) == 1
    assert len(gateway.stops) == 1


@pytest.mark.parametrize("outcome", ["TIMEOUT", "UNKNOWN"])
def test_timeout_and_unknown_are_indeterminate_and_not_retried(outcome: str) -> None:
    gateway = FakeGateway(
        results=[completed(outcome=outcome, reason_code="NO_RESULT", message="")]
    )
    report = run(gateway)
    assert report.state is RunState.INDETERMINATE
    assert report.steps[0].outcome == outcome
    assert len(gateway.calls) == 1
    assert len(gateway.stops) == 1


def test_completed_without_bus_verification_is_indeterminate() -> None:
    gateway = FakeGateway(results=[completed(bus_verified=False)])
    report = run(gateway)
    assert report.state is RunState.INDETERMINATE
    assert "verified bus read" in report.abort_reason
    assert len(gateway.calls) == 1
    assert len(gateway.stops) == 1


def test_base_step_does_not_require_bus_verification() -> None:
    gateway = FakeGateway(results=[completed(bus_verified=False)])
    report = run(
        gateway,
        recipe=BASE_RECIPE,
        auth=authorization(scopes=frozenset({"base"})),
    )
    assert report.state is RunState.SUCCEEDED
    assert len(gateway.calls) == 1


def test_submit_failure_is_indeterminate_and_not_retried() -> None:
    gateway = FakeGateway(submit_error=GatewayUnavailable("connection refused"))
    report = run(gateway)
    assert report.state is RunState.INDETERMINATE
    assert "outcome unknown" in report.abort_reason
    assert len(gateway.calls) == 1
    assert len(gateway.stops) == 1


def test_stop_failure_does_not_hide_the_run_state() -> None:
    gateway = FakeGateway(
        results=[completed(outcome="REJECTED", bus_verified=False)],
        stop_error=GatewayUnavailable("stop path down"),
    )
    report = run(gateway)
    assert report.state is RunState.FAILED
    assert report.stop_error is not None
    assert "stop path down" in report.stop_error


def test_result_stop_latch_aborts() -> None:
    gateway = FakeGateway(
        results=[completed(safety_state="STOP_LATCHED", stop_latched=True)]
    )
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert "stop latched" in report.abort_reason
    assert len(gateway.stops) == 1


def test_result_disarmed_aborts() -> None:
    gateway = FakeGateway(results=[completed(safety_state="DISARMED")])
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert len(gateway.stops) == 1


def test_unexpected_result_type_is_indeterminate() -> None:
    gateway = FakeGateway(results=[{"outcome": "COMPLETED"}])
    report = run(gateway)
    assert report.state is RunState.INDETERMINATE
    assert "unexpected result type" in report.abort_reason
    assert len(gateway.stops) == 1


def test_invalid_run_id_aborts_before_any_call() -> None:
    gateway = FakeGateway()
    report = run(gateway, run_id="")
    assert report.state is RunState.ABORTED
    assert gateway.calls == []


def test_preflight_failure_does_not_request_stop() -> None:
    gateway = FakeGateway(contract_value=contract(minor=9))
    report = run(gateway)
    assert report.state is RunState.ABORTED
    assert gateway.stops == []


def test_protocol_error_in_submit_is_unknown() -> None:
    gateway = FakeGateway(
        submit_error=GatewayProtocolError("malformed MotionResult")
    )
    report = run(gateway)
    assert report.state is RunState.INDETERMINATE
    assert report.steps[0].reason_code == "GatewayProtocolError"
    assert len(gateway.calls) == 1
    assert len(gateway.stops) == 1


def test_build_command_requires_a_lease() -> None:
    plan = build_plan(RECIPE, "run-001")
    with pytest.raises(ValueError, match="lease_id"):
        build_command(plan.steps[0], run_id="run-001", lease_id=" ")
