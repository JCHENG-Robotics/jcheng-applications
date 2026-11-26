"""Preflight checks shared by every driver.

Each function raises on refusal; the caller turns the exception into an
``ABORTED`` run report before anything is submitted. The messages are the
v0.1.0 messages on purpose: the refactor must not change behaviour.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..gateway import (
    ContractInfo,
    GatewayProtocolError,
    SafetySnapshot,
    contract_in_range,
)
from ..model import SCOPES, SAFETY_STATES, Authorization, Recipe


class PreflightRefusal(RuntimeError):
    """A preflight refusal. Nothing has been submitted."""


def parse_timestamp(value: Any, where: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{where} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{where} must include a timezone")
    return parsed


def check_authorization(recipe: Recipe, authorization: Authorization) -> None:
    if not isinstance(authorization, Authorization):
        raise PreflightRefusal("authorization: missing or invalid authorization record")
    if authorization.approved is not True:
        raise PreflightRefusal("authorization: the operator has not approved this session")
    if not isinstance(authorization.operator, str) or not authorization.operator.strip():
        raise PreflightRefusal("authorization: operator must be named")
    if not isinstance(authorization.session_id, str) or not authorization.session_id.strip():
        raise PreflightRefusal("authorization: session_id must be set")
    parse_timestamp(authorization.approved_at, "authorization.approved_at")

    scopes = set(authorization.scopes)
    if not scopes:
        raise PreflightRefusal("authorization: no scope was authorized")
    unknown = scopes - SCOPES
    if unknown:
        raise PreflightRefusal(
            f"authorization: unknown scope(s): {', '.join(sorted(unknown))}"
        )
    missing = set(recipe.scopes) - scopes
    if missing:
        raise PreflightRefusal(
            "authorization: session does not cover recipe scope(s): "
            + ", ".join(sorted(missing))
        )


def check_contract(contract: ContractInfo) -> None:
    if not isinstance(contract, ContractInfo):
        raise GatewayProtocolError("contract: gateway returned an unexpected type")
    if not contract_in_range(contract):
        raise GatewayProtocolError(
            "contract: deployed identity is "
            f"{contract.major}.{contract.minor}.{contract.patch} "
            f"{contract.contract_hash}, expected 0.1.x with the pinned hash"
        )
    if not contract.profile_hash.strip():
        raise GatewayProtocolError("contract: profile_hash is empty")
    if not contract.system_version.strip():
        raise GatewayProtocolError("contract: system_version is empty")


def check_safety(safety: SafetySnapshot) -> None:
    if not isinstance(safety, SafetySnapshot):
        raise GatewayProtocolError("safety: gateway returned an unexpected type")
    if safety.state not in SAFETY_STATES:
        raise GatewayProtocolError(f"safety: unknown state {safety.state!r}")
    if safety.stop_latched:
        raise PreflightRefusal("safety: stop is latched; reset requires the system layer")
    if safety.state != "ARMED_IDLE":
        raise PreflightRefusal(
            f"safety: state is {safety.state}; a run requires ARMED_IDLE"
        )
    parse_timestamp(safety.last_valid_state_at, "safety.last_valid_state_at")


def check_lease(lease_id: Any) -> str:
    if not isinstance(lease_id, str) or not lease_id.strip():
        raise PreflightRefusal("gateway session has no lease_id")
    return lease_id
