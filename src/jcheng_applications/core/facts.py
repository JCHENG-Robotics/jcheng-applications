"""Three-valued evaluation of declared measured facts.

A fact is only TRUE or FALSE when its source main step completed with a
verified bus read and the gateway returned enough measured positions. Every
missing, stale or out-of-range input is UNKNOWN, which never selects a branch.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from ..model import PlanFact
from .classify import Decision
from .executor import StepOutcome


class FactValue(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


def evaluate_fact(
    fact: PlanFact, outcomes: Mapping[str, StepOutcome]
) -> FactValue:
    outcome = outcomes.get(fact.from_step)
    if outcome is None or outcome.decision is not Decision.CONTINUE:
        return FactValue.UNKNOWN
    report = outcome.report
    if report.bus_verified is not True:
        return FactValue.UNKNOWN
    positions = report.measured_positions
    if positions is None or fact.index >= len(positions):
        return FactValue.UNKNOWN
    value = positions[fact.index]
    if fact.at_least_rad is not None and value < fact.at_least_rad:
        return FactValue.FALSE
    if fact.at_most_rad is not None and value > fact.at_most_rad:
        return FactValue.FALSE
    return FactValue.TRUE
