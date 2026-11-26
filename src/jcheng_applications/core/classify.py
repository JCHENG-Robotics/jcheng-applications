"""Terminal-result classification shared by every driver.

The decision table is the fail-closed table from v0.1.0. A ``COMPLETED`` arm
step without a verified bus read is ``INDETERMINATE``; a rejected step is
``FAILED``; an abort, safety latch or unknown outcome stops the run. Nothing
here retries.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..gateway import MotionResult
from ..model import PlanStep, requires_bus_verification


class Decision(str, Enum):
    """The driver action after one step."""

    CONTINUE = "CONTINUE"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class Classification:
    decision: Decision
    reason: str | None


def classify(step: PlanStep, result: MotionResult) -> Classification:
    """Map one terminal gateway result to a driver decision."""

    where = f"step {step.index} {step.name!r}"
    if result.stop_latched or result.safety_state in {
        "STOP_LATCHED",
        "FAULT",
        "DISARMED",
    }:
        reason = (
            f"{where}: safety state {result.safety_state or 'unknown'}"
            + (" (stop latched)" if result.stop_latched else "")
        )
        return Classification(Decision.ABORTED, reason)

    if result.outcome == "COMPLETED":
        if requires_bus_verification(step.scopes) and not result.bus_verified:
            return Classification(
                Decision.INDETERMINATE,
                f"{where}: completed without a verified bus read",
            )
        return Classification(Decision.CONTINUE, None)

    if result.outcome == "REJECTED":
        return Classification(
            Decision.FAILED,
            f"{where}: rejected ({result.reason_code}): {result.message}",
        )

    if result.outcome == "ABORTED":
        return Classification(
            Decision.ABORTED,
            f"{where}: aborted ({result.reason_code}): {result.message}",
        )

    # TIMEOUT and UNKNOWN are never retried: the motion may have happened.
    return Classification(
        Decision.INDETERMINATE,
        f"{where}: outcome {result.outcome} "
        f"({result.reason_code}): {result.message}",
    )
