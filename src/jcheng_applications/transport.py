"""Read-only HTTP transport for the M2 gateway admission.

This is the only module allowed to open a network connection. It implements
four GET operations against ``jcheng-gateway.v1``:

```text
GET /v1/health    unauthenticated liveness
GET /v1/contract  deployed contract identity
GET /v1/state     measured/commanded state snapshot
GET /v1/safety    safety state
```

There is no POST, no intent submission, no stop, no lease mutation and no
retry in this module. A failed read raises; it is never repeated. Redirects
are not followed, responses are size-bounded, and the token is sent only to
authenticated endpoints and never logged.

M3 adds the write path in a separate adapter; this module stays read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import http.client
import json
import os
import re
from typing import Any, Mapping

from .gateway import (
    ContractInfo,
    GatewayContractMismatch,
    GatewayProtocolError,
    GatewayUnavailable,
    GatewayUnauthorized,
    SafetySnapshot,
    contract_from_mapping,
    contract_in_range,
    safety_from_mapping,
)

_URL_RE = re.compile(
    r"^(?P<scheme>https?)://(?P<host>[A-Za-z0-9._-]+)(?::(?P<port>[0-9]{1,5}))?/?$"
)
_MEASURED = "measured"
_COMMANDED = "commanded"
_MAX_TIMEOUT_S = 30.0
_MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True)
class HealthSnapshot:
    """The liveness fields the client may rely on; carries no motion authority."""

    status: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class StateSnapshot:
    """A gateway state read.

    ``truth`` is ``measured`` when the gateway took a bus read and
    ``commanded`` when it is only a command cache. A caller must not treat a
    commanded snapshot as measurement.
    """

    truth: str
    verification: str
    fresh: bool
    profile_hash: str
    stamp: str

    @property
    def is_measured(self) -> bool:
        return self.truth == _MEASURED


@dataclass(frozen=True)
class AdmissionReport:
    """The result of a read-only admission; it never authorizes motion."""

    health: HealthSnapshot
    contract: ContractInfo
    safety: SafetySnapshot
    ready: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": {
                "status": self.health.status,
                "capabilities": list(self.health.capabilities),
            },
            "contract": {
                "major": self.contract.major,
                "minor": self.contract.minor,
                "patch": self.contract.patch,
                "contract_hash": self.contract.contract_hash,
                "profile_hash": self.contract.profile_hash,
                "system_version": self.contract.system_version,
            },
            "safety": {
                "state": self.safety.state,
                "stop_latched": self.safety.stop_latched,
                "seq": self.safety.seq,
                "last_valid_state_at": self.safety.last_valid_state_at,
            },
            "ready": self.ready,
        }


@dataclass(frozen=True)
class GatewayEndpoint:
    """A validated base URL and credential.

    The URL must be a bare ``http(s)://host[:port]``. Credentials in the URL,
    a path, a query or a fragment are rejected so a token cannot leak into a
    log line or a proxy.
    """

    base_url: str
    token: str
    timeout_s: float = 2.0
    max_response_bytes: int = _MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        match = _URL_RE.fullmatch(self.base_url)
        if match is None:
            raise ValueError(
                "base_url must be http(s)://host[:port] with no path, query or userinfo"
            )
        if not self.token:
            raise ValueError("gateway token is required")
        if isinstance(self.timeout_s, bool) or not isinstance(
            self.timeout_s, (int, float)
        ):
            raise ValueError("timeout_s must be a number")
        if not 0.05 <= float(self.timeout_s) <= _MAX_TIMEOUT_S:
            raise ValueError(
                f"timeout_s must be between 0.05 and {_MAX_TIMEOUT_S}"
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("base_url port must be between 1 and 65535")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")

    @property
    def scheme(self) -> str:
        assert _URL_RE.fullmatch(self.base_url) is not None
        return self.base_url.split("://", 1)[0]

    @property
    def host(self) -> str:
        match = _URL_RE.fullmatch(self.base_url)
        assert match is not None
        return match.group("host")

    @property
    def port(self) -> int:
        match = _URL_RE.fullmatch(self.base_url)
        assert match is not None
        raw = match.group("port")
        if raw is not None:
            return int(raw)
        return 443 if self.scheme == "https" else 80

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GatewayEndpoint":
        values = os.environ if env is None else env
        url = values.get("JCHENG_GATEWAY_URL", "")
        token = values.get("JCHENG_GATEWAY_TOKEN", "")
        if not url or not token:
            raise ValueError(
                "JCHENG_GATEWAY_URL and JCHENG_GATEWAY_TOKEN are required"
            )
        timeout = float(values.get("JCHENG_GATEWAY_TIMEOUT_S", "2.0"))
        return cls(base_url=url, token=token, timeout_s=timeout)


class ReadOnlyGateway:
    """GET-only client for the M2 admission.

    Every method performs exactly one request. A timeout, connection error,
    non-200 status or malformed payload raises; nothing is retried.
    """

    def __init__(self, endpoint: GatewayEndpoint) -> None:
        self._endpoint = endpoint

    def __enter__(self) -> "ReadOnlyGateway":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    # ---- reads ----

    def health(self) -> HealthSnapshot:
        payload = self._get("/v1/health", authenticated=False)
        status = payload.get("status")
        if status != "ok":
            raise GatewayProtocolError(f"health.status: expected 'ok', got {status!r}")
        capabilities = payload.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item for item in capabilities
        ):
            raise GatewayProtocolError("health.capabilities: must be a list of strings")
        return HealthSnapshot(status="ok", capabilities=tuple(capabilities))

    def contract(self) -> ContractInfo:
        return contract_from_mapping(self._get("/v1/contract"))

    def state(self) -> StateSnapshot:
        return _state_from_mapping(self._get("/v1/state"))

    def safety(self) -> SafetySnapshot:
        return safety_from_mapping(self._get("/v1/safety"))

    def check_admission(self) -> AdmissionReport:
        """Read health, contract and safety; refuse on a contract mismatch."""

        health = self.health()
        contract = self.contract()
        if not contract_in_range(contract):
            raise GatewayContractMismatch(
                "contract: deployed identity is "
                f"{contract.major}.{contract.minor}.{contract.patch} "
                f"{contract.contract_hash}, expected 0.1.x with the pinned hash"
            )
        safety = self.safety()
        ready = safety.state == "ARMED_IDLE" and not safety.stop_latched
        return AdmissionReport(
            health=health, contract=contract, safety=safety, ready=ready
        )

    # ---- one bounded GET ----

    def _get(self, path: str, *, authenticated: bool = True) -> Mapping[str, Any]:
        endpoint = self._endpoint
        connection = self._connection()
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {endpoint.token}"
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            status = response.status
            raw = self._read_bounded(response)
        except TimeoutError as exc:
            raise GatewayUnavailable(f"GET {path}: timed out after {endpoint.timeout_s}s") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise GatewayUnavailable(f"GET {path}: {type(exc).__name__}: {exc}") from exc
        finally:
            connection.close()

        if status in (401, 403):
            raise GatewayUnauthorized(f"GET {path}: HTTP {status}")
        if 500 <= status < 600:
            raise GatewayUnavailable(f"GET {path}: HTTP {status}")
        if status != 200:
            raise GatewayProtocolError(f"GET {path}: unexpected HTTP {status}")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GatewayProtocolError(f"GET {path}: response is not JSON") from exc
        if not isinstance(decoded, Mapping):
            raise GatewayProtocolError(f"GET {path}: response must be an object")
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
                raise GatewayProtocolError("response Content-Length is not an integer") from exc
            if length < 0 or length > limit:
                raise GatewayProtocolError(
                    f"response length {length} exceeds the {limit}-byte bound"
                )
            raw = response.read(length)
        else:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise GatewayProtocolError(
                f"response exceeds the {limit}-byte bound"
            )
        return raw


def _state_from_mapping(payload: Any) -> StateSnapshot:
    if not isinstance(payload, Mapping):
        raise GatewayProtocolError("state: must be a mapping")
    truth = payload.get("truth")
    if truth not in {_MEASURED, _COMMANDED}:
        raise GatewayProtocolError(
            f"state.truth: must be '{_MEASURED}' or '{_COMMANDED}', got {truth!r}"
        )
    verification = payload.get("verification")
    if not isinstance(verification, str) or not verification.strip():
        raise GatewayProtocolError("state.verification: must be a non-empty string")
    fresh = payload.get("fresh")
    if not isinstance(fresh, bool):
        raise GatewayProtocolError("state.fresh: must be a boolean")
    profile_hash = payload.get("profile_hash")
    if not isinstance(profile_hash, str) or not profile_hash.strip():
        raise GatewayProtocolError("state.profile_hash: must be a non-empty string")
    header = payload.get("header")
    if not isinstance(header, Mapping):
        raise GatewayProtocolError("state.header: must be a mapping")
    stamp = header.get("stamp")
    if not isinstance(stamp, str) or not stamp.strip():
        raise GatewayProtocolError(
            "state.header.stamp: must be a non-empty ISO 8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GatewayProtocolError(
            "state.header.stamp: must be an ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise GatewayProtocolError("state.header.stamp: must include a timezone")
    return StateSnapshot(
        truth=truth,
        verification=verification,
        fresh=fresh,
        profile_hash=profile_hash,
        stamp=stamp,
    )
