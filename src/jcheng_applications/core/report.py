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
