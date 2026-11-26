"""M3 write transport against a loopback mock gateway."""

from __future__ import annotations

import pytest

from jcheng_applications.core.command import build_command
from jcheng_applications.gateway import GatewayUnauthorized, GatewayUnavailable
from jcheng_applications.model import PlanStep
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway, NonMockGateway

from mock_gateway import TOKEN, MockGatewayServer, completed


def _endpoint(port: int) -> GatewayEndpoint:
    return GatewayEndpoint(
        base_url=f"http://127.0.0.1:{port}", token=TOKEN, timeout_s=1.0
    )


def _gateway(port: int) -> HttpGateway:
    return HttpGateway(_endpoint(port), operator_id="operator")


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


def test_check_mock_accepts_mock_and_refuses_other() -> None:
    with MockGatewayServer() as server:
        _gateway(server.port).check_mock()
    with MockGatewayServer(capabilities=["readonly"]) as server:
        with pytest.raises(NonMockGateway):
            _gateway(server.port).check_mock()


def test_lease_acquire_renew_release() -> None:
    with MockGatewayServer() as server:
        gateway = _gateway(server.port)
        info = gateway.acquire_lease(holder="operator", scope="full", ttl_ms=30_000)
        assert info.lease_id == "lease-m3"
        assert info.scope == "full"
        assert gateway.lease_id() == "lease-m3"
        gateway.renew_lease(info.lease_id, 30_000)
        assert gateway.release_lease(info.lease_id) is True
        paths = server.probe.paths()
        assert "/v1/leases" in paths
        assert "/v1/leases/lease-m3/renew" in paths
        assert server.probe.requests[0]["headers"]["authorization"] == f"Bearer {TOKEN}"
        assert server.probe.requests[0]["body"]["contract"]["contract_hash"]


def test_submit_uses_idempotency_header_and_parses_result() -> None:
    with MockGatewayServer() as server:
        server.probe.results.append(completed())
        result = _gateway(server.port).submit(_command())
        assert result.outcome == "COMPLETED"
        assert result.bus_verified is True
        request = server.probe.requests[-1]
        assert request["path"] == "/v1/motion/execute"
        assert request["headers"]["idempotency-key"] == "key-1"
        assert request["body"]["idempotency_key"] == "key-1"


def test_stop_posts_operator_and_reason() -> None:
    with MockGatewayServer() as server:
        _gateway(server.port).stop("test stop")
        body = server.probe.requests[-1]["body"]
        assert body == {"operator_id": "operator", "reason": "test stop"}


def test_unauthorized_is_not_retried() -> None:
    with MockGatewayServer() as server:
        server.probe.execute_status = 401
        with pytest.raises(GatewayUnauthorized):
            _gateway(server.port).submit(_command())
        assert server.probe.paths("POST") == ["/v1/motion/execute"]


def test_lost_lease_blocks_submit_without_a_request() -> None:
    with MockGatewayServer() as server:
        gateway = _gateway(server.port)
        gateway.mark_lease_lost("renew failed")
        with pytest.raises(GatewayUnavailable):
            gateway.submit(_command())
        assert server.probe.paths() == []
