"""Stop-once policy shared by every driver."""

from __future__ import annotations

from typing import Any


class StopOnce:
    """Request at most one stop per run and preserve a stop failure."""

    def __init__(self) -> None:
        self.requested = False
        self.error: str | None = None

    def request(self, gateway: Any, reason: str) -> None:
        if self.requested:
            return
        self.requested = True
        try:
            gateway.stop(reason)
        except Exception as exc:  # noqa: BLE001 - a stop failure is recorded
            self.error = f"{type(exc).__name__}: {exc}"
