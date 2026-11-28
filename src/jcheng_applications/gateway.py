"""The abstract gateway port and strict contract payload parsing.

``GatewayPort`` is the only path from the application to the robot. It is a
protocol, not a transport: v0.1.0 ships no HTTP/WS client, no network endpoint
and no DDS participant. The M1 adapter implements this protocol against the
published ``jcheng-gateway.v1`` contract and is the only place a network call
may appear.

The parsers below validate the fields the orchestrator relies on. A future
adapter should validate the full JSON Schema before calling them; the core does
not need a schema library to enforce its own safety decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any, Mapping, Protocol

from .model import CONTRACT_HASH, CONTRACT_VERSION, MOTION_OUTCOMES, SAFETY_STATES

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class GatewayError(RuntimeError):
    """Base class for gateway failures."""


class GatewayUnavailable(GatewayError):
    """The gateway could not be reached or did not answer in time."""


class GatewayProtocolError(GatewayError):
    """The gateway answered with a payload that violates the contract."""


class GatewayUnauthorized(GatewayError):
    """The gateway rejected the credential; there is no retry loop."""


class GatewayContractMismatch(GatewayProtocolError):
    """The deployed contract is outside the pinned range or hash."""


@dataclass(frozen=True)
class ContractInfo:
    """The deployed contract identity returned by ``GET /v1/contract``."""

    major: int
    minor: int
    patch: int
    contract_hash: str
    profile_hash: str
    system_version: str


STOP_CONFIRMATION_REQUEST_ACCEPTED = "request_accepted"
STOP_CONFIRMATION_BACKEND_CONFIRMED = "backend_confirmed"
STOP_CONFIRMATIONS = frozenset(
    {STOP_CONFIRMATION_REQUEST_ACCEPTED, STOP_CONFIRMATION_BACKEND_CONFIRMED}
)
PHYSICAL_STOP_UNVERIFIED = "unverified"


@dataclass(frozen=True)
class StopReceipt:
    """What a stop request established.

    ``confirmation`` is S1 (``request_accepted``) or S2 (``backend_confirmed``)
    only. The platform cannot measure a physical stop, so ``physical_stop`` is
    always ``unverified``. Missing optional fields default to S1: HTTP 200 plus
    ``accepted: true`` proves the request was accepted, nothing more.
    """

    accepted: bool
    confirmation: str
    physical_stop: str
    message: str


@dataclass(frozen=True)
class SafetySnapshot:
    """The subset of ``SafetyState`` the orchestrator decides on."""

    state: str
    stop_latched: bool
    seq: int
    last_valid_state_at: str


@dataclass(frozen=True)
class MotionResult:
    """The subset of ``MotionResult`` the orchestrator decides on."""

    intent_id: str
    outcome: str
    reason_code: str
    message: str
    bus_verified: bool
    safety_state: str | None = None
    stop_latched: bool | None = None
    measured_positions: tuple[float, ...] | None = None


class GatewayPort(Protocol):
    """The transport-neutral boundary the runner depends on.

    Implementations must not add a device, ROS or SSH path. The lease is owned
    by the system layer; ``lease_id`` only reports the client session's lease
    reference, which the system layer may revoke at any time.
    """

    def lease_id(self) -> str:
        """Return the current client-session lease reference."""

    def contract(self) -> ContractInfo:
        """Return the deployed contract identity."""

    def safety(self) -> SafetySnapshot:
        """Return the current safety state."""

    def submit(self, command: Mapping[str, Any]) -> MotionResult:
        """Submit one ``MotionCommand`` and return its terminal result."""

    def stop(self, reason: str) -> StopReceipt | None:
        """Request a software stop after a failed or indeterminate step.

        Return the gateway receipt when the adapter can provide one. ``None``
        means the adapter reports no confirmation; it is never read as S2.
        """


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GatewayProtocolError(f"{where}: must be a mapping")
    return value


def _text(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GatewayProtocolError(f"{where}: must be a string")
    if not allow_empty and not value.strip():
        raise GatewayProtocolError(f"{where}: must be a non-empty string")
    return value


def _int(value: Any, where: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GatewayProtocolError(f"{where}: must be an integer")
    if value < 0:
        raise GatewayProtocolError(f"{where}: must not be negative")
    if maximum is not None and value > maximum:
        raise GatewayProtocolError(f"{where}: must not exceed {maximum}")
    return value


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise GatewayProtocolError(f"{where}: must be a boolean")
    return value


def _timestamp(value: Any, where: str) -> str:
    text = _text(value, where)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GatewayProtocolError(f"{where}: must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise GatewayProtocolError(f"{where}: must include a timezone")
    return text


def contract_from_mapping(payload: Any) -> ContractInfo:
    """Parse a ``GET /v1/contract`` response."""

    root = _mapping(payload, "contract")
    try:
        contract = _mapping(root["contract"], "contract.contract")
        profile_hash = _text(root["profile_hash"], "contract.profile_hash")
        system_version = _text(root["system_version"], "contract.system_version")
    except KeyError as exc:
        raise GatewayProtocolError(f"contract: missing field {exc.args[0]}") from exc

    major = _int(contract.get("major"), "contract.contract.major", maximum=65535)
    minor = _int(contract.get("minor"), "contract.contract.minor", maximum=65535)
    patch = _int(contract.get("patch"), "contract.contract.patch", maximum=65535)
    contract_hash = _text(
        contract.get("contract_hash"), "contract.contract.contract_hash"
    )
    if not _HASH_RE.fullmatch(contract_hash):
        raise GatewayProtocolError(
            "contract.contract.contract_hash: must match sha256:<64 lowercase hex>"
        )
    return ContractInfo(
        major=major,
        minor=minor,
        patch=patch,
        contract_hash=contract_hash,
        profile_hash=profile_hash,
        system_version=system_version,
    )


def safety_from_mapping(payload: Any) -> SafetySnapshot:
    """Parse the safety fields the orchestrator decides on."""

    root = _mapping(payload, "safety")
    state = _text(root.get("state"), "safety.state")
    if state not in SAFETY_STATES:
        raise GatewayProtocolError(f"safety.state: unknown state {state!r}")
    return SafetySnapshot(
        state=state,
        stop_latched=_bool(root.get("stop_latched"), "safety.stop_latched"),
        seq=_int(root.get("seq"), "safety.seq"),
        last_valid_state_at=_timestamp(
            root.get("last_valid_state_at"), "safety.last_valid_state_at"
        ),
    )


def motion_result_from_mapping(payload: Any) -> MotionResult:
    """Parse the terminal ``MotionResult`` fields the orchestrator decides on."""

    root = _mapping(payload, "result")
    outcome = _text(root.get("outcome"), "result.outcome")
    if outcome not in MOTION_OUTCOMES:
        raise GatewayProtocolError(f"result.outcome: unknown outcome {outcome!r}")

    safety = root.get("safety")
    safety_state = None
    stop_latched = None
    if safety is not None:
        snapshot = safety_from_mapping(safety)
        safety_state = snapshot.state
        stop_latched = snapshot.stop_latched

    raw_positions = root.get("measured_positions")
    measured_positions = None
    if raw_positions is not None:
        if not isinstance(raw_positions, list):
            raise GatewayProtocolError("result.measured_positions: must be a list")
        values: list[float] = []
        for index, value in enumerate(raw_positions):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GatewayProtocolError(
                    f"result.measured_positions[{index}]: must be a number"
                )
            number = float(value)
            if not math.isfinite(number):
                raise GatewayProtocolError(
                    f"result.measured_positions[{index}]: must be finite"
                )
            values.append(number)
        measured_positions = tuple(values)

    return MotionResult(
        intent_id=_text(root.get("intent_id"), "result.intent_id"),
        outcome=outcome,
        reason_code=_text(
            root.get("reason_code"), "result.reason_code", allow_empty=True
        ),
        message=_text(root.get("message"), "result.message", allow_empty=True),
        bus_verified=_bool(root.get("bus_verified"), "result.bus_verified"),
        safety_state=safety_state,
        stop_latched=stop_latched,
        measured_positions=measured_positions,
    )


def stop_receipt_from_mapping(payload: Any) -> StopReceipt:
    """Parse a ``POST /v1/motion/stop`` response.

    The R5 fields are optional until the contract review closes:

    - ``confirmation`` absent means S1; it may never be inferred as S2.
    - ``physical_stop`` must be absent or ``unverified``; any other value is a
      protocol error, because the platform cannot measure a physical stop.
    """

    root = _mapping(payload, "stop")
    accepted = _bool(root.get("accepted"), "stop.accepted")
    if accepted is not True:
        raise GatewayProtocolError("stop: gateway did not accept the request")

    confirmation = root.get("confirmation", STOP_CONFIRMATION_REQUEST_ACCEPTED)
    if not isinstance(confirmation, str) or confirmation not in STOP_CONFIRMATIONS:
        raise GatewayProtocolError(
            "stop.confirmation: must be request_accepted or backend_confirmed"
        )

    physical_stop = root.get("physical_stop", PHYSICAL_STOP_UNVERIFIED)
    if physical_stop != PHYSICAL_STOP_UNVERIFIED:
        raise GatewayProtocolError(
            "stop.physical_stop: must be unverified; a physical stop is not "
            "measurable on this platform"
        )

    message = root.get("message", "")
    if not isinstance(message, str):
        raise GatewayProtocolError("stop.message: must be a string")
    return StopReceipt(
        accepted=accepted,
        confirmation=confirmation,
        physical_stop=physical_stop,
        message=message,
    )


def contract_in_range(info: ContractInfo) -> bool:
    """Return whether the contract lies in ``>=0.3,<0.4`` and matches the pin."""

    return (
        info.major == 0
        and info.minor == CONTRACT_VERSION["minor"]
        and info.contract_hash == CONTRACT_HASH
    )
