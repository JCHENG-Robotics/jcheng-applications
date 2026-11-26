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
from .core.executor import StepExecutor, StepOutcome
from .core.preflight import (
    check_authorization,
    check_contract,
    check_lease,
    check_safety,
)
from .core.report import SelectionRecord, StepReport  # re-exported for callers and tests
from .core.stop import StopOnce, receipt_fields
from .core.strategy import select_branch
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
    selections: tuple[SelectionRecord, ...] = ()
    operator_required: bool = False
    operator_reason: str | None = None
    stop_confirmation: str | None = None
    physical_stop: str | None = None


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
    selections: tuple[SelectionRecord, ...] = (),
    operator_required: bool = False,
    operator_reason: str | None = None,
    stop_confirmation: str | None = None,
    physical_stop: str | None = None,
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
        selections=selections,
        operator_required=operator_required,
        operator_reason=operator_reason,
        stop_confirmation=stop_confirmation,
        physical_stop=physical_stop,
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
        selections: list[SelectionRecord] = []
        main_outcomes: dict[str, StepOutcome] = {}
        state = RunState.SUCCEEDED
        abort_reason: str | None = None
        operator_required = False
        operator_reason: str | None = None

        for step in plan.steps:
            outcome = executor.submit(step, run_id=run_id, lease_id=lease_id)
            step_reports.append(outcome.report)
            if outcome.decision is not Decision.CONTINUE:
                state = {
                    Decision.FAILED: RunState.FAILED,
                    Decision.ABORTED: RunState.ABORTED,
                    Decision.INDETERMINATE: RunState.INDETERMINATE,
                }[outcome.decision]
                abort_reason = outcome.reason
                if outcome.decision in (
                    Decision.ABORTED,
                    Decision.INDETERMINATE,
                ):
                    operator_required = True
                    operator_reason = outcome.reason
                break
            main_outcomes[step.name] = outcome

            record, branch = select_branch(plan, step.name, main_outcomes)
            if record is None:
                continue
            selections.append(record)
            if branch is None:
                if any(
                    candidate.after == step.name and candidate.required
                    for candidate in plan.branches
                ):
                    state = RunState.ABORTED
                    abort_reason = (
                        f"required branch after {step.name!r} has no eligible "
                        "candidate"
                    )
                    operator_required = True
                    operator_reason = abort_reason
                    break
                continue

            failed = False
            for branch_step in branch.steps:
                branch_outcome = executor.submit(
                    branch_step, run_id=run_id, lease_id=lease_id
                )
                step_reports.append(branch_outcome.report)
                if branch_outcome.decision is not Decision.CONTINUE:
                    state = {
                        Decision.FAILED: RunState.FAILED,
                        Decision.ABORTED: RunState.ABORTED,
                        Decision.INDETERMINATE: RunState.INDETERMINATE,
                    }[branch_outcome.decision]
                    abort_reason = branch_outcome.reason
                    if branch_outcome.decision in (
                        Decision.ABORTED,
                        Decision.INDETERMINATE,
                    ):
                        operator_required = True
                        operator_reason = branch_outcome.reason
                    failed = True
                    break
            if failed:
                break

        stop_confirmation: str | None = None
        physical_stop: str | None = None
        if state is not RunState.SUCCEEDED and step_reports:
            stop_once.request(self._gateway, abort_reason or state.value)
            stop_confirmation, physical_stop = receipt_fields(stop_once.receipt)
            if stop_once.error is not None:
                operator_required = True
                operator_reason = f"stop request failed: {stop_once.error}"

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
            selections=tuple(selections),
            operator_required=operator_required,
            operator_reason=operator_reason,
            stop_confirmation=stop_confirmation,
            physical_stop=physical_stop,
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
        selections: tuple[SelectionRecord, ...] = (),
        operator_required: bool = False,
        operator_reason: str | None = None,
        stop_confirmation: str | None = None,
        physical_stop: str | None = None,
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
            selections=selections,
            operator_required=operator_required,
            operator_reason=operator_reason,
            stop_confirmation=stop_confirmation,
            physical_stop=physical_stop,
        )
