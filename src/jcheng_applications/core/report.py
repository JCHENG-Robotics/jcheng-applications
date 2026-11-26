"""Step report shared by every driver."""

from __future__ import annotations

from dataclasses import dataclass


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
    measured_positions: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Candidate:
    """One declared branch considered at a selection point."""

    branch_id: str
    guard: str
    selected: bool


@dataclass(frozen=True)
class SelectionRecord:
    """The deterministic selection result for one anchor step."""

    after_step: str
    candidates: tuple[Candidate, ...]
    selected: str | None
    reason: str | None = None
