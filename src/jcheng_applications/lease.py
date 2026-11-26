"""Lease acquisition, renewal and release for one supervised run.

The system layer owns the lease; this class only holds a client reference. A
renewal failure marks the lease lost, blocks further submissions and issues
one stop. The renewal thread is independent of the step loop.
"""

from __future__ import annotations

import threading
from typing import Any

from .write_transport import LeaseInfo


class LeaseSession:
    """Acquire a lease, renew it at ``ttl/3`` and release it on exit."""

    def __init__(
        self,
        gateway: Any,
        *,
        holder: str,
        scope: str,
        ttl_ms: int = 30_000,
    ) -> None:
        if ttl_ms < 100:
            raise ValueError("ttl_ms must be at least 100")
        if not holder.strip():
            raise ValueError("holder is required")
        self._gateway = gateway
        self._holder = holder
        self._scope = scope
        self._ttl_ms = int(ttl_ms)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lease_id = ""
        self.info: LeaseInfo | None = None
        self.renewals = 0
        self.lost = False
        self.error: str | None = None
        self.release_error: str | None = None

    def acquire(self) -> LeaseInfo:
        info = self._gateway.acquire_lease(
            holder=self._holder, scope=self._scope, ttl_ms=self._ttl_ms
        )
        self.info = info
        self.lease_id = info.lease_id
        self._gateway.bind_lease(info.lease_id)
        self._thread = threading.Thread(
            target=self._renew_loop, name="m3-lease-renew", daemon=True
        )
        self._thread.start()
        return info

    def _renew_loop(self) -> None:
        interval = max(0.02, self._ttl_ms / 1000.0 / 3.0)
        while not self._stop.wait(interval):
            try:
                self._gateway.renew_lease(self.lease_id, self._ttl_ms)
                self.renewals += 1
            except Exception as exc:  # noqa: BLE001 - a lost lease stops the run
                self.lost = True
                self.error = f"{type(exc).__name__}: {exc}"
                self._gateway.mark_lease_lost(self.error)
                try:
                    self._gateway.stop(f"lease renewal failed: {self.error}")
                except Exception:
                    pass
                return

    def release(self) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if not self.lease_id:
            return False
        try:
            return bool(self._gateway.release_lease(self.lease_id))
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            self.release_error = f"{type(exc).__name__}: {exc}"
            return False
