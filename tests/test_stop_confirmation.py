"""Stop confirmation levels recorded from the gateway receipt.

The R5 fields are optional until the contract review closes. These tests pin
the client rule: HTTP 200 proves S1 only; S2 requires an explicit
``backend_confirmed``; a physical stop is never claimed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcheng_applications.gateway import (
    GatewayProtocolError,
    PHYSICAL_STOP_UNVERIFIED,
    STOP_CONFIRMATION_BACKEND_CONFIRMED,
    STOP_CONFIRMATION_REQUEST_ACCEPTED,
    stop_receipt_from_mapping,
)
from jcheng_applications.model import Authorization, load_recipe
from jcheng_applications.runstore import RunStore
from jcheng_applications.supervised import run_supervised
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed

ROOT = Path(__file__).resolve().parents[1]
RECIPE = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")


def _gateway(port: int) -> HttpGateway:
    endpoint = GatewayEndpoint(
        base_url=f"http://127.0.0.1:{port}", token=TOKEN, timeout_s=1.0
    )
    return HttpGateway(endpoint, operator_id="operator")


def _auth() -> Authorization:
    return Authorization(
        operator="operator",
        session_id="session-stop",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )


def _run(server: MockGatewayServer, store: RunStore, engine: str = "linear"):
    return run_supervised(
        _gateway(server.port),
        RECIPE,
        _auth(),
        run_id=f"run-stop-{engine}",
        store=store,
        recipe_hash="recipe-hash",
        evidence_class="L2",
        engine=engine,
    )


def test_receipt_fields_do_not_partially_trust_a_malformed_receipt() -> None:
    from jcheng_applications.core.stop import receipt_fields

    class Bad:
        confirmation = STOP_CONFIRMATION_BACKEND_CONFIRMED
        physical_stop = "stopped"

    assert receipt_fields(Bad()) == (None, None)
    assert receipt_fields(None) == (None, None)


def test_missing_optional_fields_are_s1_only() -> None:
    receipt = stop_receipt_from_mapping({"accepted": True})
    assert receipt.accepted is True
    assert receipt.confirmation == STOP_CONFIRMATION_REQUEST_ACCEPTED
    assert receipt.physical_stop == PHYSICAL_STOP_UNVERIFIED


def test_backend_confirmed_is_s2_and_physical_stays_unverified() -> None:
    receipt = stop_receipt_from_mapping(
        {
            "accepted": True,
            "confirmation": STOP_CONFIRMATION_BACKEND_CONFIRMED,
            "physical_stop": PHYSICAL_STOP_UNVERIFIED,
        }
    )
    assert receipt.confirmation == STOP_CONFIRMATION_BACKEND_CONFIRMED
    assert receipt.physical_stop == PHYSICAL_STOP_UNVERIFIED


@pytest.mark.parametrize(
    "payload",
    [
        {"accepted": True, "confirmation": "confirmed"},
        {"accepted": True, "confirmation": None},
        {"accepted": True, "physical_stop": "stopped"},
        {"accepted": True, "physical_stop": True},
        {"accepted": False},
        {"accepted": "yes"},
    ],
)
def test_invalid_receipts_are_rejected(payload: dict) -> None:
    with pytest.raises(GatewayProtocolError):
        stop_receipt_from_mapping(payload)


def test_http_stop_returns_the_receipt() -> None:
    with MockGatewayServer() as server:
        server.probe.stop_confirmation = STOP_CONFIRMATION_BACKEND_CONFIRMED
        server.probe.stop_physical_stop = PHYSICAL_STOP_UNVERIFIED
        receipt = _gateway(server.port).stop("test stop")
        assert receipt.confirmation == STOP_CONFIRMATION_BACKEND_CONFIRMED
        assert receipt.physical_stop == PHYSICAL_STOP_UNVERIFIED


@pytest.mark.parametrize("engine", ["linear", "tree"])
def test_run_records_s1_when_the_gateway_omits_the_fields(
    engine: str, tmp_path: Path
) -> None:
    if engine == "tree":
        pytest.importorskip("py_trees")
    with MockGatewayServer() as server:
        server.probe.results.append(
            completed(outcome="REJECTED", code="LIMIT_VIOLATION", bus_verified=False)
        )
        store = RunStore(tmp_path / engine)
        result = _run(server, store, engine)
        assert result.report.stop_confirmation == STOP_CONFIRMATION_REQUEST_ACCEPTED
        assert result.report.physical_stop == PHYSICAL_STOP_UNVERIFIED
        assert result.report.stop_error is None
        assert store.verify(result.report.run_id) is True
        last = json.loads(
            store.path_for(result.report.run_id)
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )
        assert last["payload"]["stop_confirmation"] == (
            STOP_CONFIRMATION_REQUEST_ACCEPTED
        )
        assert last["payload"]["physical_stop"] == PHYSICAL_STOP_UNVERIFIED


@pytest.mark.parametrize("engine", ["linear", "tree"])
def test_run_records_s2_only_when_the_gateway_confirms(
    engine: str, tmp_path: Path
) -> None:
    if engine == "tree":
        pytest.importorskip("py_trees")
    with MockGatewayServer() as server:
        server.probe.stop_confirmation = STOP_CONFIRMATION_BACKEND_CONFIRMED
        server.probe.stop_physical_stop = PHYSICAL_STOP_UNVERIFIED
        server.probe.results.append(
            completed(outcome="REJECTED", code="LIMIT_VIOLATION", bus_verified=False)
        )
        result = _run(server, RunStore(tmp_path / engine), engine)
        assert result.report.stop_confirmation == STOP_CONFIRMATION_BACKEND_CONFIRMED
        assert result.report.physical_stop == PHYSICAL_STOP_UNVERIFIED


def test_stop_failure_records_no_level_and_requires_the_operator(
    tmp_path: Path,
) -> None:
    with MockGatewayServer() as server:
        server.probe.stop_status = 503
        server.probe.results.append(
            completed(outcome="REJECTED", code="LIMIT_VIOLATION", bus_verified=False)
        )
        result = _run(server, RunStore(tmp_path / "runs"))
        assert result.report.stop_error is not None
        assert result.report.stop_confirmation is None
        assert result.report.physical_stop is None
        assert result.report.operator_required is True


def test_successful_run_has_no_stop_level(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.results.extend([completed(), completed()])
        result = _run(server, RunStore(tmp_path / "runs"))
        assert result.report.state.value == "SUCCEEDED"
        assert result.report.stop_confirmation is None
        assert result.report.physical_stop is None
        assert server.probe.paths().count("/v1/motion/stop") == 0
