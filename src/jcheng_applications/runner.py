"""Fail-closed task runner over the abstract gateway port.

The runner has no transport. It acquires nothing, opens nothing and executes
nothing: it asks the gateway to evaluate one intent at a time and maps the
terminal result to a run state. A rejected, aborted, timed-out or unverified
step stops the run; there is no automatic retry, because a retried motion with
an unknown outcome is a second motion.

The preflight, command, submission, classification and stop-once logic lives in
``jcheng_applications.core`` and is shared with the tree driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from .core.classify import Decision
from .core.command import build_command  # re-exported for callers and tests
from .core.executor import StepExecutor
from .core.preflight import (
    check_authorization,
    check_contract,
    check_lease,
    check_safety,
)
from .core.report import StepReport  # re-exported for callers and tests
from .core.stop import StopOnce
from .model import Authorization, Recipe, build_plan


class RunState(str, Enum):
    """Terminal state of one orchestration run."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    INDETERMINATE = "INDETERMINATE"


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return moment.isoformat()


def make_report(
    *,
    recipe: Recipe,
    run_id: str,
    operator: str,
    session_id: str,
    state: RunState,
    started_at: str,
    finished_at: str,
    steps: tuple[StepReport, ...],
    abort_reason: str | None,
    stop_error: str | None,
) -> RunReport:
    """Assemble a report without inferring or interpolating a value."""

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


def aborted_report(
    recipe: Recipe,
    authorization: Authorization,
    run_id: str,
    reason: str,
    *,
    clock: Callable[[], datetime] = _utcnow,
) -> RunReport:
    """A preflight refusal: nothing was submitted."""

    started_at = _stamp(clock())
    finished_at = _stamp(clock())
    return make_report(
        recipe=recipe,
        run_id=run_id,
        operator=authorization.operator if isinstance(authorization, Authorization) else "",
        session_id=authorization.session_id if isinstance(authorization, Authorization) else "",
        state=RunState.ABORTED,
        started_at=started_at,
        finished_at=finished_at,
        steps=(),
        abort_reason=reason,
        stop_error=None,
    )


class TaskRunner:
    """Run a recipe step by step against a :class:`GatewayPort`."""

    def __init__(
        self,
        gateway: object,
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
            check_authorization(recipe, authorization)
            plan = build_plan(recipe, run_id)
            check_contract(self._gateway.contract())
            check_safety(self._gateway.safety())
            lease_id = check_lease(self._gateway.lease_id())
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

        executor = StepExecutor(self._gateway)
        stop_once = StopOnce()
        step_reports: list[StepReport] = []
        state = RunState.SUCCEEDED
        abort_reason: str | None = None

        for step in plan.steps:
            outcome = executor.submit(step, run_id=run_id, lease_id=lease_id)
            step_reports.append(outcome.report)
            if outcome.decision is Decision.CONTINUE:
                continue
            state = {
                Decision.FAILED: RunState.FAILED,
                Decision.ABORTED: RunState.ABORTED,
                Decision.INDETERMINATE: RunState.INDETERMINATE,
            }[outcome.decision]
            abort_reason = outcome.reason
            break

        if state is not RunState.SUCCEEDED and step_reports:
            stop_once.request(self._gateway, abort_reason or state.value)

        return self._report(
            recipe=recipe,
            run_id=run_id,
            operator=operator,
            session_id=session_id,
            state=state,
            started_at=started_at,
            steps=tuple(step_reports),
            abort_reason=abort_reason,
            stop_error=stop_once.error,
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
        return make_report(
            recipe=recipe,
            run_id=run_id,
            operator=operator,
            session_id=session_id,
            state=state,
            started_at=started_at,
            finished_at=_stamp(self._clock()),
            steps=steps,
            abort_reason=abort_reason,
            stop_error=stop_error,
        )
