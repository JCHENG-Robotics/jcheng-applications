"""Fail-closed task runner over the abstract gateway port.

The runner has no transport. It acquires nothing, opens nothing and executes
nothing: it asks the gateway to evaluate one intent at a time and maps the
terminal result to a run state. A rejected, aborted, timed-out or unverified
step stops the run; there is no automatic retry, because a retried motion with
an unknown outcome is a second motion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .gateway import (
    ContractInfo,
    GatewayProtocolError,
    MotionResult,
    SafetySnapshot,
    contract_in_range,
)
from .model import (
    CONTRACT_VERSION,
    SAFETY_STATES,
    SCOPES,
    Authorization,
    PlanStep,
    Recipe,
    build_plan,
    requires_bus_verification,
)


class RunState(str, Enum):
    """Terminal state of one orchestration run."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class StepReport:
    """What happened to one step, including the gateway's own reason."""

    index: int
    name: str
    outcome: str
    reason_code: str
    message: str
    intent_id: str
    idempotency_key: str
    bus_verified: bool | None
    safety_state: str | None


@dataclass(frozen=True)
class RunReport:
    """The auditable result of one run; no value is inferred or interpolated."""

    run_id: str
    recipe_name: str
    recipe_version: str
    contract_hash: str
    operator: str
    session_id: str
    state: RunState
    started_at: str
    finished_at: str
    steps: tuple[StepReport, ...]
    abort_reason: str | None
    stop_error: str | None


class _Abort(Exception):
    """A preflight refusal. Nothing has been submitted."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return moment.isoformat()


def _parse_timestamp(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{where} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{where} must include a timezone")
    return parsed


def _check_authorization(recipe: Recipe, authorization: Authorization) -> None:
    if not isinstance(authorization, Authorization):
        raise _Abort("authorization: missing or invalid authorization record")
    if authorization.approved is not True:
        raise _Abort("authorization: the operator has not approved this session")
    if not isinstance(authorization.operator, str) or not authorization.operator.strip():
        raise _Abort("authorization: operator must be named")
    if not isinstance(authorization.session_id, str) or not authorization.session_id.strip():
        raise _Abort("authorization: session_id must be set")
    _parse_timestamp(authorization.approved_at, "authorization.approved_at")

    scopes = set(authorization.scopes)
    if not scopes:
        raise _Abort("authorization: no scope was authorized")
    unknown = scopes - SCOPES
    if unknown:
        raise _Abort(f"authorization: unknown scope(s): {', '.join(sorted(unknown))}")
    missing = set(recipe.scopes) - scopes
    if missing:
        raise _Abort(
            "authorization: session does not cover recipe scope(s): "
            + ", ".join(sorted(missing))
        )


def _check_contract(contract: ContractInfo) -> None:
    if not isinstance(contract, ContractInfo):
        raise GatewayProtocolError("contract: gateway returned an unexpected type")
    if not contract_in_range(contract):
        raise GatewayProtocolError(
            "contract: deployed identity is "
            f"{contract.major}.{contract.minor}.{contract.patch} "
            f"{contract.contract_hash}, expected 0.1.x with the pinned hash"
        )
    if not contract.profile_hash.strip():
        raise GatewayProtocolError("contract: profile_hash is empty")
    if not contract.system_version.strip():
        raise GatewayProtocolError("contract: system_version is empty")


def _check_safety(safety: SafetySnapshot) -> None:
    if not isinstance(safety, SafetySnapshot):
        raise GatewayProtocolError("safety: gateway returned an unexpected type")
    if safety.state not in SAFETY_STATES:
        raise GatewayProtocolError(f"safety: unknown state {safety.state!r}")
    if safety.stop_latched:
        raise _Abort("safety: stop is latched; reset requires the system layer")
    if safety.state != "ARMED_IDLE":
        raise _Abort(
            f"safety: state is {safety.state}; a run requires ARMED_IDLE"
        )
    _parse_timestamp(safety.last_valid_state_at, "safety.last_valid_state_at")


def _check_lease(lease_id: Any) -> str:
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise _Abort("gateway session has no lease_id")
    return lease_id


def build_command(step: PlanStep, *, run_id: str, lease_id: str) -> dict[str, Any]:
    """Build the ``MotionCommand`` for one plan step.

    The command is a request to the gateway, not a direct execution path. The
    ``intent_id`` is deterministic from the idempotency key so a retried
    submission is recognisable as the same intent.
    """

    if not isinstance(lease_id, str) or not lease_id.strip():
        raise ValueError("lease_id must be a non-empty string")
    key = step.idempotency_key
    command: dict[str, Any] = {
        "intent_id": f"app-{key[:24]}",
        "idempotency_key": key,
        "lease_id": lease_id,
        "source": f"jcheng-applications/{run_id}",
        "kind": step.kind,
        "profile": step.profile,
        "contract": dict(CONTRACT_VERSION),
    }
    command.update(step.params)
    return command


class TaskRunner:
    """Run a recipe step by step against a :class:`GatewayPort`."""

    def __init__(
        self,
        gateway: Any,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._gateway = gateway
        self._clock = clock

    def run(
        self,
        recipe: Recipe,
        authorization: Authorization,
        *,
        run_id: str,
    ) -> RunReport:
        """Run the recipe. Operational failures produce a report, not a raise."""

        started = self._clock()
        started_at = _stamp(started)
        operator = (
            authorization.operator if isinstance(authorization, Authorization) else ""
        )
        session_id = (
            authorization.session_id
            if isinstance(authorization, Authorization)
            else ""
        )

        try:
            _check_authorization(recipe, authorization)
            plan = build_plan(recipe, run_id)
            _check_contract(self._gateway.contract())
            _check_safety(self._gateway.safety())
            lease_id = _check_lease(self._gateway.lease_id())
        except Exception as exc:
            return self._report(
                recipe=recipe,
                run_id=run_id,
                operator=operator,
                session_id=session_id,
                state=RunState.ABORTED,
                started_at=started_at,
                steps=(),
                abort_reason=f"{type(exc).__name__}: {exc}",
                stop_error=None,
            )

        step_reports: list[StepReport] = []
        state = RunState.SUCCEEDED
        abort_reason: str | None = None
        stop_error: str | None = None
        submitted = 0

        for step in plan.steps:
            command = build_command(step, run_id=run_id, lease_id=lease_id)
            try:
                result = self._gateway.submit(command)
            except Exception as exc:
                # The submission may have reached the gateway. Its outcome is
                # unknown, so this is never retried.
                submitted += 1
                step_reports.append(
                    StepReport(
                        index=step.index,
                        name=step.name,
                        outcome="UNKNOWN",
                        reason_code=type(exc).__name__,
                        message=str(exc),
                        intent_id=str(command["intent_id"]),
                        idempotency_key=step.idempotency_key,
                        bus_verified=None,
                        safety_state=None,
                    )
                )
                state = RunState.INDETERMINATE
                abort_reason = (
                    f"step {step.index} {step.name!r}: submission outcome unknown: "
                    f"{type(exc).__name__}: {exc}"
                )
                break

            submitted += 1
            if not isinstance(result, MotionResult):
                step_reports.append(
                    StepReport(
                        index=step.index,
                        name=step.name,
                        outcome="UNKNOWN",
                        reason_code="GatewayProtocolError",
                        message="gateway returned an unexpected result type",
                        intent_id=str(command["intent_id"]),
                        idempotency_key=step.idempotency_key,
                        bus_verified=None,
                        safety_state=None,
                    )
                )
                state = RunState.INDETERMINATE
                abort_reason = (
                    f"step {step.index} {step.name!r}: unexpected result type "
                    f"{type(result).__name__}"
                )
                break

            step_reports.append(
                StepReport(
                    index=step.index,
                    name=step.name,
                    outcome=result.outcome,
                    reason_code=result.reason_code,
                    message=result.message,
                    intent_id=result.intent_id,
                    idempotency_key=step.idempotency_key,
                    bus_verified=result.bus_verified,
                    safety_state=result.safety_state,
                )
            )

            if result.stop_latched or result.safety_state in {
                "STOP_LATCHED",
                "FAULT",
                "DISARMED",
            }:
                state = RunState.ABORTED
                abort_reason = (
                    f"step {step.index} {step.name!r}: safety state "
                    f"{result.safety_state or 'unknown'}"
                    + (" (stop latched)" if result.stop_latched else "")
                )
                break

            if result.outcome == "COMPLETED":
                if requires_bus_verification(step.scopes) and not result.bus_verified:
                    state = RunState.INDETERMINATE
                    abort_reason = (
                        f"step {step.index} {step.name!r}: completed without a "
                        "verified bus read"
                    )
                    break
                continue

            if result.outcome == "REJECTED":
                state = RunState.FAILED
                abort_reason = (
                    f"step {step.index} {step.name!r}: rejected "
                    f"({result.reason_code}): {result.message}"
                )
                break

            if result.outcome == "ABORTED":
                state = RunState.ABORTED
                abort_reason = (
                    f"step {step.index} {step.name!r}: aborted "
                    f"({result.reason_code}): {result.message}"
                )
                break

            # TIMEOUT and UNKNOWN are never retried: the motion may have
            # happened, so the operator must inspect the bus state first.
            state = RunState.INDETERMINATE
            abort_reason = (
                f"step {step.index} {step.name!r}: outcome {result.outcome} "
                f"({result.reason_code}): {result.message}"
            )
            break

        if state is not RunState.SUCCEEDED and submitted > 0:
            try:
                self._gateway.stop(abort_reason or state.value)
            except Exception as exc:
                stop_error = f"{type(exc).__name__}: {exc}"

        return self._report(
            recipe=recipe,
            run_id=run_id,
            operator=operator,
            session_id=session_id,
            state=state,
            started_at=started_at,
            steps=tuple(step_reports),
            abort_reason=abort_reason,
            stop_error=stop_error,
        )

    def _report(
        self,
        *,
        recipe: Recipe,
        run_id: str,
        operator: str,
        session_id: str,
        state: RunState,
        started_at: str,
        steps: tuple[StepReport, ...],
        abort_reason: str | None,
        stop_error: str | None,
    ) -> RunReport:
        finished_at = _stamp(self._clock())
        return RunReport(
            run_id=run_id,
            recipe_name=recipe.name,
            recipe_version=recipe.version,
            contract_hash=recipe.contract_hash,
            operator=operator,
            session_id=session_id,
            state=state,
            started_at=started_at,
            finished_at=finished_at,
            steps=steps,
            abort_reason=abort_reason,
            stop_error=stop_error,
        )
