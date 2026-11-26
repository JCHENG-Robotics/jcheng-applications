"""Stop-once policy shared by every driver."""

from __future__ import annotations

from typing import Any

from ..gateway import PHYSICAL_STOP_UNVERIFIED, STOP_CONFIRMATIONS


def receipt_fields(receipt: Any | None) -> tuple[str | None, str | None]:
    """Return ``(confirmation, physical_stop)`` from a gateway receipt.

    Only S1/S2 are recorded. An adapter that reports no receipt yields
    ``(None, None)``; a malformed receipt never upgrades to a confirmation and
    a physical stop is only recorded as the constant ``unverified``.
    """

    if receipt is None:
        return None, None
    confirmation = getattr(receipt, "confirmation", None)
    physical_stop = getattr(receipt, "physical_stop", None)
    if (
        confirmation not in STOP_CONFIRMATIONS
        or physical_stop != PHYSICAL_STOP_UNVERIFIED
    ):
        # A malformed receipt is not partially trusted; fail closed.
        return None, None
    return confirmation, physical_stop


class StopOnce:
    """Request at most one stop per run and preserve a stop failure."""

    def __init__(self) -> None:
        self.requested = False
        self.error: str | None = None
        self.receipt: Any | None = None

    def request(self, gateway: Any, reason: str) -> None:
        if self.requested:
            return
        self.requested = True
        try:
            self.receipt = gateway.stop(reason)
        except Exception as exc:  # noqa: BLE001 - a stop failure is recorded
            self.error = f"{type(exc).__name__}: {exc}"
