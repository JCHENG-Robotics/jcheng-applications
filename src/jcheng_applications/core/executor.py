"""One submission per step, shared by every driver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..gateway import MotionResult
from ..model import PlanStep
from .classify import Classification, Decision, classify
from .command import build_command
from .report import StepReport


@dataclass(frozen=True)
class StepOutcome:
    """The step report and the driver decision for one submission."""

    report: StepReport
    decision: Decision
    reason: str | None


class StepExecutor:
    """Build, submit exactly once and classify one plan step.

    A transport exception is an unknown outcome, not a retryable error. A
    non-``MotionResult`` payload is a protocol error. Neither is ever retried.
    """

    def __init__(self, gateway: Any) -> None:
        self._gateway = gateway

    def submit(
        self, step: PlanStep, *, run_id: str, lease_id: str
    ) -> StepOutcome:
        command = build_command(step, run_id=run_id, lease_id=lease_id)
        intent_id = str(command["intent_id"])
        try:
            result = self._gateway.submit(command)
        except Exception as exc:  # noqa: BLE001 - the outcome is unknown, not retryable
            report = StepReport(
                index=step.index,
                name=step.name,
                outcome="UNKNOWN",
                reason_code=type(exc).__name__,
                message=str(exc),
                intent_id=intent_id,
                idempotency_key=step.idempotency_key,
                bus_verified=None,
                safety_state=None,
            )
            reason = (
                f"step {step.index} {step.name!r}: submission outcome unknown: "
                f"{type(exc).__name__}: {exc}"
            )
            return StepOutcome(report, Decision.INDETERMINATE, reason)

        if not isinstance(result, MotionResult):
            report = StepReport(
                index=step.index,
                name=step.name,
                outcome="UNKNOWN",
                reason_code="GatewayProtocolError",
                message="gateway returned an unexpected result type",
                intent_id=intent_id,
                idempotency_key=step.idempotency_key,
                bus_verified=None,
                safety_state=None,
            )
            reason = (
                f"step {step.index} {step.name!r}: unexpected result type "
                f"{type(result).__name__}"
            )
            return StepOutcome(report, Decision.INDETERMINATE, reason)

        report = StepReport(
            index=step.index,
            name=step.name,
            outcome=result.outcome,
            reason_code=result.reason_code,
            message=result.message,
            intent_id=result.intent_id,
            idempotency_key=step.idempotency_key,
            bus_verified=result.bus_verified,
            safety_state=result.safety_state,
            measured_positions=result.measured_positions,
        )
        classification: Classification = classify(step, result)
        return StepOutcome(report, classification.decision, classification.reason)
