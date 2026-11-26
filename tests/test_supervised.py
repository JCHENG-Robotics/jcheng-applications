"""M3 supervised run: lease lifecycle, dispatch and persisted records."""

from __future__ import annotations

import json
from pathlib import Path
import stat

import pytest

from jcheng_applications.core.command import build_command
from jcheng_applications.gateway import GatewayUnavailable
from jcheng_applications.lease import LeaseSession
from jcheng_applications.model import (
    Authorization,
    PlanStep,
    RecipeError,
    authorization_from_mapping,
    load_recipe,
)
from jcheng_applications.runstore import RunStore
from jcheng_applications.supervised import run_supervised
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway, NonMockGateway

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed, wait_until

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
        session_id="session-m3",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )


def _command() -> dict:
    step = PlanStep(
        index=1,
        name="grasp",
        kind="GRIPPER",
        profile="gripper",
        scopes=("gripper",),
        params={
            "gripper": {
                "position_rad": 0.1,
                "max_velocity_rad_s": 0.5,
                "duration_s": 1.0,
                "verify_bus": True,
            }
        },
        idempotency_key="key-1",
    )
    return build_command(step, run_id="run-1", lease_id="lease-m3")


def _run(server: MockGatewayServer, store: RunStore):
    return run_supervised(
        _gateway(server.port),
        RECIPE,
        _auth(),
        run_id="run-1",
        store=store,
        recipe_hash="recipe-hash",
        evidence_class="L2",
    )


def test_authorization_parser_accepts_a_valid_record() -> None:
    parsed = authorization_from_mapping(
        {
            "operator": "operator",
            "session_id": "s1",
            "scopes": ["arm", "gripper"],
            "approved": True,
            "approved_at": NOW,
        }
    )
    assert parsed.operator == "operator"
    assert parsed.scopes == frozenset({"arm", "gripper"})


def test_authorization_parser_rejects_bad_fields() -> None:
    base = {
        "operator": "operator",
        "session_id": "s1",
        "scopes": ["arm"],
        "approved": True,
        "approved_at": NOW,
    }
    for change in (
        {"extra": 1},
        {"approved": "yes"},
        {"scopes": ["camera"]},
        {"scopes": ["arm", "arm"]},
        {"operator": ""},
    ):
        with pytest.raises(RecipeError):
            authorization_from_mapping({**base, **change})


def test_lease_session_renews_and_releases() -> None:
    with MockGatewayServer() as server:
        gateway = _gateway(server.port)
        session = LeaseSession(
            gateway, holder="operator", scope="full", ttl_ms=150
        )
        session.acquire()
        assert wait_until(lambda: session.renewals >= 1, 1.0)
        assert session.lost is False
        assert session.release() is True
        assert "/v1/leases/lease-m3/renew" in server.probe.paths()


def test_renew_failure_marks_lost_blocks_submit_and_stops() -> None:
    with MockGatewayServer() as server:
        server.probe.fail_renew = True
        gateway = _gateway(server.port)
        session = LeaseSession(
            gateway, holder="operator", scope="full", ttl_ms=150
        )
        session.acquire()
        assert wait_until(lambda: session.lost, 1.5)
        assert "503" in (session.error or "")
        with pytest.raises(GatewayUnavailable):
            gateway.submit(_command())
        assert "/v1/motion/stop" in server.probe.paths()
        assert "/v1/motion/execute" not in server.probe.paths()
        session.release()


def test_supervised_success_persists_and_releases(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.results.extend([completed(), completed()])
        store = RunStore(tmp_path / "runs")
        result = _run(server, store)
        assert result.report.state.value == "SUCCEEDED"
        assert result.lease_released is True
        assert store.verify("run-1") is True
        assert result.record_path == store.path_for("run-1")
        assert stat.S_IMODE(result.record_path.stat().st_mode) == 0o600
        assert "lease-m3" not in result.record_path.read_text(encoding="utf-8")
        paths = server.probe.paths()
        assert paths.count("/v1/motion/execute") == 2
        assert f"/v1/leases/{result.lease.lease_id}" in paths


def test_supervised_rejection_stops_and_records_failure(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.results.append(
            completed(outcome="REJECTED", code="LIMIT_VIOLATION", bus_verified=False)
        )
        store = RunStore(tmp_path / "runs")
        result = _run(server, store)
        assert result.report.state.value == "FAILED"
        assert "rejected" in (result.report.abort_reason or "")
        assert server.probe.paths().count("/v1/motion/stop") == 1
        assert store.verify("run-1") is True


def test_supervised_refuses_non_mock_gateway(tmp_path: Path) -> None:
    with MockGatewayServer(capabilities=["readonly"]) as server:
        store = RunStore(tmp_path / "runs")
        with pytest.raises(NonMockGateway):
            _run(server, store)
        assert "/v1/leases" not in server.probe.paths()
        assert "/v1/motion/execute" not in server.probe.paths()


def test_run_store_detects_tampering(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.results.extend([completed(), completed()])
        store = RunStore(tmp_path / "runs")
        result = _run(server, store)
        assert store.verify("run-1") is True
        text = result.record_path.read_text(encoding="utf-8")
        result.record_path.write_text(
            text.replace('"SUCCEEDED"', '"FAILED"'), encoding="utf-8"
        )
        assert store.verify("run-1") is False


class _FailingStore:
    def write_report(self, *args, **kwargs):
        raise OSError("disk full")


def test_record_failure_keeps_the_report_and_release() -> None:
    with MockGatewayServer() as server:
        server.probe.results.extend([completed(), completed()])
        result = run_supervised(
            _gateway(server.port),
            RECIPE,
            _auth(),
            run_id="run-1",
            store=_FailingStore(),
            recipe_hash="recipe-hash",
            evidence_class="L2",
        )
        assert result.report.state.value == "SUCCEEDED"
        assert result.record_path is None
        assert "disk full" in (result.record_error or "")
        assert result.lease_released is True


def test_run_store_rejects_unsafe_run_id(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with pytest.raises(ValueError):
        store.path_for("../escape")


def test_unauthorized_execute_is_unknown_and_not_retried(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.execute_status = 401
        store = RunStore(tmp_path / "runs")
        result = _run(server, store)
        # A 401 during submission is a transport failure after the request may
        # have been sent; the runner records UNKNOWN, never a retry.
        assert result.report.state.value == "INDETERMINATE"
        assert "GatewayUnauthorized" in (result.report.abort_reason or "")
        assert server.probe.paths().count("/v1/motion/execute") == 1
        assert store.verify("run-1") is True
