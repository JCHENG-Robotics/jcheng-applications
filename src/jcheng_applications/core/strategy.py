"""Deterministic, fail-closed selection among declared branches.

Only branches anchored after the current main step are considered. Every
condition is three-valued; a branch is eligible only when all conditions are
PASS. The order is author-declared priority, then branch id. No execution
outcome is an input.
"""

from __future__ import annotations

from typing import Mapping

from ..model import Plan, PlanBranch
from .executor import StepOutcome
from .facts import FactValue, evaluate_fact
from .report import Candidate, SelectionRecord

_PASS = "PASS"
_FAIL = "FAIL"
_UNKNOWN = "UNKNOWN"


def select_branch(
    plan: Plan,
    after_step: str,
    outcomes: Mapping[str, StepOutcome],
) -> tuple[SelectionRecord | None, PlanBranch | None]:
    anchored = [branch for branch in plan.branches if branch.after == after_step]
    if not anchored:
        return None, None

    evaluated: list[tuple[PlanBranch, str]] = []
    for branch in anchored:
        guard = _PASS
        for fact_name, expected in branch.conditions:
            fact = plan.facts[fact_name]
            value = evaluate_fact(fact, outcomes)
            if value is FactValue.UNKNOWN:
                guard = _UNKNOWN
                break
            if (value is FactValue.TRUE) != expected:
                guard = _FAIL
                break
        evaluated.append((branch, guard))

    eligible = [branch for branch, guard in evaluated if guard == _PASS]
    eligible.sort(key=lambda branch: (-branch.priority, branch.id))
    selected = eligible[0] if eligible else None
    record = SelectionRecord(
        after_step=after_step,
        candidates=tuple(
            Candidate(branch_id=branch.id, guard=guard, selected=branch is selected)
            for branch, guard in evaluated
        ),
        selected=selected.id if selected is not None else None,
        reason=(
            f"selected {selected.id}"
            if selected is not None
            else "no branch has all conditions PASS"
        ),
    )
    return record, selected
