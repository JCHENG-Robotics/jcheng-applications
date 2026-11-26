"""WAITING_FOR_OPERATOR: terminal operator-required semantics and CLI exit 3."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jcheng_applications.cli import main
from jcheng_applications.gateway import ContractInfo, MotionResult, SafetySnapshot
from jcheng_applications.model import (
    CONTRACT_HASH,
    Authorization,
    load_recipe,
    recipe_from_mapping,
)
from jcheng_applications.runner import RunState, TaskRunner
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed

ROOT = Path(__file__).resolve().parents[1]
RECIPE = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
BRANCH_RECIPE_PATH = ROOT / "recipes" / "example-declared-branch.yaml"


def _auth(scopes=("arm", "gripper")) -> Authorization:
    return Authorization(
        operator="operator",
        session_id="waiting-session",
        scopes=frozenset(scopes),
        approved=True,
        approved_at=NOW,
    )


def _contract() -> ContractInfo:
    return ContractInfo(
        major=0,
        minor=1,
        patch=0,
        contract_hash=CONTRACT_HASH,
        profile_hash="profile-waiting",
        system_version="0.1.0",
    )


def _safety(state: str = "ARMED_IDLE") -> SafetySnapshot:
    return SafetySnapshot(
        state=state, stop_latched=False, seq=1, last_valid_state_at=NOW
    )


def _result(
    outcome: str = "COMPLETED",
    *,
    code: str = "OK",
    bus_verified: bool = True,
) -> MotionResult:
    return MotionResult(
        intent_id="intent",
        outcome=outcome,
        reason_code=code,
        message="",
        bus_verified=bus_verified,
        safety_state="ARMED_IDLE",
        stop_latched=False,
        measured_positions=(0.03, 0.0, 0.0, 0.0, 0.0),
    )


class WaitingGateway:
    def __init__(
        self,
        results: list[MotionResult],
        *,
        safety_state: str = "ARMED_IDLE",
        stop_error: Exception | None = None,
    ) -> None:
        self.results = list(results)
        self._safety_state = safety_state
        self.stop_error = stop_error
        self.stops: list[str] = []

    def contract(self) -> ContractInfo:
        return _contract()

    def safety(self) -> SafetySnapshot:
        return _safety(self._safety_state)

    def lease_id(self) -> str:
        return "lease-waiting"

    def submit(self, command: dict) -> MotionResult:
        return self.results.pop(0)

    def stop(self, reason: str) -> None:
        self.stops.append(reason)
        if self.stop_error is not None:
            raise self.stop_error


def _run(gateway: WaitingGateway, recipe=RECIPE):
    return TaskRunner(gateway).run(recipe, _auth(), run_id="run-1")


def test_success_does_not_require_operator() -> None:
    report = _run(WaitingGateway([_result(), _result()]))
    assert report.state is RunState.SUCCEEDED
    assert report.operator_required is False
    assert report.operator_reason is None


def test_unverified_completion_requires_operator() -> None:
    report = _run(WaitingGateway([_result(), _result(bus_verified=False)]))
    assert report.state is RunState.INDETERMINATE
    assert report.operator_required is True
    assert "verified bus read" in (report.operator_reason or "")


def test_rejection_does_not_require_operator() -> None:
    report = _run(
        WaitingGateway([_result("REJECTED", code="X", bus_verified=False)])
    )
    assert report.state is RunState.FAILED
    assert report.operator_required is False


def test_aborted_result_requires_operator() -> None:
    report = _run(
        WaitingGateway([_result("ABORTED", code="X", bus_verified=False)])
    )
    assert report.state is RunState.ABORTED
    assert report.operator_required is True


def test_stop_failure_requires_operator() -> None:
    report = _run(
        WaitingGateway(
            [_result("REJECTED", code="X", bus_verified=False)],
            stop_error=RuntimeError("stop failed"),
        )
    )
    assert report.state is RunState.FAILED
    assert report.operator_required is True
    assert "stop request failed" in (report.operator_reason or "")


def test_preflight_refusal_does_not_require_operator() -> None:
    report = _run(
        WaitingGateway([], safety_state="DISARMED")
    )
    assert report.state is RunState.ABORTED
    assert report.operator_required is False


def test_required_branch_without_candidate_requires_operator() -> None:
    raw = yaml.safe_load(BRANCH_RECIPE_PATH.read_text(encoding="utf-8"))
    raw["branches"][0]["required"] = True
    recipe = recipe_from_mapping(raw)
    gateway = WaitingGateway([_result()])
    gateway.results[0] = MotionResult(
        intent_id="intent",
        outcome="COMPLETED",
        reason_code="OK",
        message="",
        bus_verified=True,
        safety_state="ARMED_IDLE",
        stop_latched=False,
        measured_positions=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    report = TaskRunner(gateway).run(
        recipe, _auth(("arm",)), run_id="run-1"
    )
    assert report.state is RunState.ABORTED
    assert report.operator_required is True
    assert "required branch" in (report.operator_reason or "")


def test_cli_exits_3_on_operator_required(tmp_path: Path, monkeypatch) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "operator": "operator",
                "session_id": "waiting-cli",
                "scopes": ["arm", "gripper"],
                "approved": True,
                "approved_at": NOW,
            }
        ),
        encoding="utf-8",
    )
    with MockGatewayServer() as server:
        server.probe.results.append(completed(bus_verified=False))
        monkeypatch.setenv("JCHENG_GATEWAY_TOKEN", TOKEN)
        code = main(
            [
                "run",
                str(ROOT / "recipes" / "example-supervised-transfer.yaml"),
                "--authorization",
                str(auth_path),
                "--run-id",
                "run-1",
                "--url",
                f"http://127.0.0.1:{server.port}",
                "--store-dir",
                str(tmp_path / "runs"),
            ]
        )
    assert code == 3
