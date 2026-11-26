"""Tests for the gateway payload parsers and contract matching."""

from __future__ import annotations

import pytest

from jcheng_applications.gateway import (
    ContractInfo,
    GatewayProtocolError,
    contract_from_mapping,
    contract_in_range,
    motion_result_from_mapping,
    safety_from_mapping,
)
from jcheng_applications.model import CONTRACT_HASH

CONTRACT = {
    "contract": {
        "major": 0,
        "minor": 1,
        "patch": 0,
        "contract_hash": CONTRACT_HASH,
    },
    "profile_hash": "sha256:" + "a" * 64,
    "system_version": "0.1.0",
}

SAFETY = {
    "state": "ARMED_IDLE",
    "stop_latched": False,
    "seq": 7,
    "last_valid_state_at": "2026-11-26T10:00:00+08:00",
}

RESULT = {
    "intent_id": "app-1",
    "outcome": "COMPLETED",
    "reason_code": "OK",
    "message": "",
    "bus_verified": True,
    "safety": SAFETY,
}


def test_contract_parses() -> None:
    info = contract_from_mapping(CONTRACT)
    assert info == ContractInfo(
        major=0,
        minor=1,
        patch=0,
        contract_hash=CONTRACT_HASH,
        profile_hash=CONTRACT["profile_hash"],
        system_version="0.1.0",
    )
    assert contract_in_range(info)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda payload: payload.pop("profile_hash"), "profile_hash"),
        (lambda payload: payload["contract"].update(contract_hash="deadbeef"), "contract_hash"),
        (lambda payload: payload["contract"].update(major=-1), "major"),
        (lambda payload: payload["contract"].update(minor=70000), "minor"),
    ],
)
def test_contract_rejects_invalid_payloads(mutate, message) -> None:
    payload = {
        "contract": dict(CONTRACT["contract"]),
        "profile_hash": CONTRACT["profile_hash"],
        "system_version": CONTRACT["system_version"],
    }
    mutate(payload)
    with pytest.raises(GatewayProtocolError, match=message):
        contract_from_mapping(payload)


def test_contract_range_requires_the_pinned_identity() -> None:
    base = dict(major=0, minor=1, patch=0, contract_hash=CONTRACT_HASH,
                profile_hash="p", system_version="s")
    assert contract_in_range(ContractInfo(**base))
    assert not contract_in_range(ContractInfo(**{**base, "minor": 2}))
    assert not contract_in_range(
        ContractInfo(**{**base, "contract_hash": "sha256:" + "0" * 64})
    )


def test_safety_parses() -> None:
    snapshot = safety_from_mapping(SAFETY)
    assert snapshot.state == "ARMED_IDLE"
    assert snapshot.stop_latched is False
    assert snapshot.seq == 7


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda payload: payload.update(state="RUNNING"), "unknown state"),
        (lambda payload: payload.update(stop_latched="no"), "boolean"),
        (lambda payload: payload.update(seq=-1), "negative"),
        (lambda payload: payload.update(last_valid_state_at="2026-11-26T10:00:00"), "timezone"),
        (lambda payload: payload.update(last_valid_state_at="yesterday"), "ISO 8601"),
    ],
)
def test_safety_rejects_invalid_payloads(mutate, message) -> None:
    payload = dict(SAFETY)
    mutate(payload)
    with pytest.raises(GatewayProtocolError, match=message):
        safety_from_mapping(payload)


def test_motion_result_parses_with_and_without_safety() -> None:
    with_safety = motion_result_from_mapping(RESULT)
    assert with_safety.outcome == "COMPLETED"
    assert with_safety.safety_state == "ARMED_IDLE"
    assert with_safety.stop_latched is False

    without_safety = motion_result_from_mapping(
        {key: value for key, value in RESULT.items() if key != "safety"}
    )
    assert without_safety.safety_state is None
    assert without_safety.stop_latched is None


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda payload: payload.update(outcome="DONE"), "unknown outcome"),
        (lambda payload: payload.update(bus_verified="yes"), "boolean"),
        (lambda payload: payload.update(intent_id=""), "non-empty"),
        (lambda payload: payload.update(message=3), "string"),
    ],
)
def test_motion_result_rejects_invalid_payloads(mutate, message) -> None:
    payload = dict(RESULT)
    mutate(payload)
    with pytest.raises(GatewayProtocolError, match=message):
        motion_result_from_mapping(payload)


def test_motion_result_rejects_malformed_safety() -> None:
    payload = dict(RESULT)
    payload["safety"] = {"state": "ARMED_IDLE"}
    with pytest.raises(GatewayProtocolError, match="stop_latched"):
        motion_result_from_mapping(payload)
