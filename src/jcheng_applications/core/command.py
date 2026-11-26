"""The only place a ``MotionCommand`` payload is constructed."""

from __future__ import annotations

from typing import Any

from ..model import CONTRACT_VERSION, PlanStep


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
