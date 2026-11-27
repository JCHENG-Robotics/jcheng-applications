"""Write-path HTTP gateway for the M3 supervised run.

This is the second and last network module. It implements the runner's
``GatewayPort`` plus lease acquire/renew/release against ``jcheng-gateway.v1``:

```text
GET    /v1/health, /v1/contract, /v1/state, /v1/safety   (read, delegated)
POST   /v1/leases                                       (acquire)
POST   /v1/leases/{id}/renew                            (renew)
DELETE /v1/leases/{id}                                  (release)
POST   /v1/motion/execute                               (submit)
POST   /v1/motion/stop                                  (stop)
```

No retry, no redirect, bounded timeouts, strict parsing. The module refuses to
be used against a gateway whose `/v1/health` does not advertise the ``mock``
capability, so M3 cannot submit to a real robot.
"""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
from typing import Any, Mapping

from .gateway import (
    ContractInfo,
    GatewayError,
    GatewayProtocolError,
    GatewayUnauthorized,
    GatewayUnavailable,
    MotionResult,
    SafetySnapshot,
    StopReceipt,
    motion_result_from_mapping,
    stop_receipt_from_mapping,
)
from .model import CONTRACT_VERSION, SCOPES
from .transport import (
    GatewayEndpoint,
    HealthSnapshot,
    ReadOnlyGateway,
    StateSnapshot,
)


class NonMockGateway(GatewayError):
    """The gateway does not advertise the mock capability; M3 refuses it."""


class NonHardwareGateway(GatewayError):
    """The gateway does not advertise the hardware capability; M4 refuses it."""


@dataclass(frozen=True)
class LeaseInfo:
    lease_id: str
    holder: str
    scope: str
    expires_at: str


class HttpGateway:
    """The only write path from the application to the gateway."""

    def __init__(self, endpoint: GatewayEndpoint, *, operator_id: str) -> None:
        if not operator_id.strip():
            raise ValueError("operator_id is required")
        self._endpoint = endpoint
        self._read = ReadOnlyGateway(endpoint)
        self._operator_id = operator_id
        self._lease_id = ""
        self._lease_lost: str | None = None

    # ---- reads ----

    def health(self) -> HealthSnapshot:
        return self._read.health()

    def contract(self) -> ContractInfo:
        return self._read.contract()

    def safety(self) -> SafetySnapshot:
        return self._read.safety()

    def state(self) -> StateSnapshot:
        return self._read.state()

    def check_mock(self) -> None:
        """Refuse anything that is not the mock gateway; M3 is mock-only."""

        health = self.health()
        if "mock" not in health.capabilities:
            raise NonMockGateway(
                "gateway does not advertise the mock capability; "
                "M3 refuses to submit motion"
            )

    def check_hardware(self) -> None:
        """Refuse a gateway that does not advertise hardware; M4 is opt-in."""

        health = self.health()
        if "hardware" not in health.capabilities:
            raise NonHardwareGateway(
                "gateway does not advertise the hardware capability; "
                "the M4 target must be the hardware-backed system gateway"
            )

    # ---- lease ----

    def acquire_lease(
        self,
        *,
        holder: str,
        scope: str,
        ttl_ms: int = 30_000,
        priority: int = 100,
        deadman_required: bool = False,
    ) -> LeaseInfo:
        if scope not in SCOPES:
            raise ValueError(f"unknown lease scope {scope!r}")
        body = {
            "holder": holder,
            "scope": scope,
            "mode": "exclusive",
            "priority": int(priority),
            "ttl_ms": int(ttl_ms),
            "deadman_required": bool(deadman_required),
            "contract": dict(CONTRACT_VERSION),
        }
        payload = self._request("POST", "/v1/leases", body)
        info = _lease_from_mapping(payload)
        self._lease_id = info.lease_id
        self._lease_lost = None
        return info

    def bind_lease(self, lease_id: str) -> None:
        self._lease_id = lease_id

    def renew_lease(self, lease_id: str, ttl_ms: int) -> LeaseInfo:
        payload = self._request(
            "POST",
            f"/v1/leases/{lease_id}/renew",
            {"ttl_ms": int(ttl_ms), "contract": dict(CONTRACT_VERSION)},
        )
        return _lease_from_mapping(payload)

    def release_lease(self, lease_id: str) -> bool:
        payload = self._request("DELETE", f"/v1/leases/{lease_id}")
        released = payload.get("released")
        if not isinstance(released, bool):
            raise GatewayProtocolError("lease.released: must be a boolean")
        return released

    def mark_lease_lost(self, reason: str) -> None:
        self._lease_lost = reason

    # ---- runner port ----

    def lease_id(self) -> str:
        return self._lease_id

    def submit(self, command: Mapping[str, Any]) -> MotionResult:
        if self._lease_lost is not None:
            raise GatewayUnavailable(f"lease lost: {self._lease_lost}")
        key = command.get("idempotency_key")
        if not isinstance(key, str) or not key:
            raise GatewayProtocolError("command has no idempotency_key")
        payload = self._request(
            "POST",
            "/v1/motion/execute",
            dict(command),
            extra_headers={"Idempotency-Key": key},
        )
        return motion_result_from_mapping(payload)

    def stop(self, reason: str) -> StopReceipt:
        payload = self._request(
            "POST",
            "/v1/motion/stop",
            {"operator_id": self._operator_id, "reason": reason},
        )
        return stop_receipt_from_mapping(payload)

    # ---- one bounded request ----

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        endpoint = self._endpoint
        connection = self._connection()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {endpoint.token}",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        if extra_headers:
            headers.update(extra_headers)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            status = response.status
            raw = self._read_bounded(response)
        except TimeoutError as exc:
            raise GatewayUnavailable(
                f"{method} {path}: timed out after {endpoint.timeout_s}s"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise GatewayUnavailable(
                f"{method} {path}: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            connection.close()

        if status in (401, 403):
            raise GatewayUnauthorized(f"{method} {path}: HTTP {status}")
        if 500 <= status < 600:
            raise GatewayUnavailable(f"{method} {path}: HTTP {status}")
        if status != 200:
            raise GatewayProtocolError(f"{method} {path}: unexpected HTTP {status}")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GatewayProtocolError(f"{method} {path}: response is not JSON") from exc
        if not isinstance(decoded, Mapping):
            raise GatewayProtocolError(f"{method} {path}: response must be an object")
        return decoded

    def _connection(self) -> http.client.HTTPConnection:
        endpoint = self._endpoint
        if endpoint.scheme == "https":
            return http.client.HTTPSConnection(
                endpoint.host, endpoint.port, timeout=endpoint.timeout_s
            )
        return http.client.HTTPConnection(
            endpoint.host, endpoint.port, timeout=endpoint.timeout_s
        )

    def _read_bounded(self, response: http.client.HTTPResponse) -> bytes:
        limit = self._endpoint.max_response_bytes
        declared = response.getheader("Content-Length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError as exc:
                raise GatewayProtocolError(
                    "response Content-Length is not an integer"
                ) from exc
            if length < 0 or length > limit:
                raise GatewayProtocolError(
                    f"response length {length} exceeds the {limit}-byte bound"
                )
            raw = response.read(length)
        else:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise GatewayProtocolError(f"response exceeds the {limit}-byte bound")
        return raw


def _lease_from_mapping(payload: Mapping[str, Any]) -> LeaseInfo:
    lease_id = payload.get("lease_id")
    holder = payload.get("holder")
    scope = payload.get("scope")
    expires_at = payload.get("expires_at")
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise GatewayProtocolError("lease.lease_id: must be a non-empty string")
    if not isinstance(holder, str) or not holder.strip():
        raise GatewayProtocolError("lease.holder: must be a non-empty string")
    if not isinstance(scope, str) or scope not in SCOPES:
        raise GatewayProtocolError(f"lease.scope: unknown scope {scope!r}")
    if not isinstance(expires_at, str) or not expires_at.strip():
        raise GatewayProtocolError("lease.expires_at: must be a non-empty timestamp")
    return LeaseInfo(
        lease_id=lease_id, holder=holder, scope=scope, expires_at=expires_at
    )
