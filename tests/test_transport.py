"""Read-only transport tests against a loopback mock gateway.

The mock server is local, in-process and temporary. No robot, no DDS and no
external network are contacted.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Thread
import time
from typing import Any

import pytest

from jcheng_applications.gateway import (
    GatewayContractMismatch,
    GatewayProtocolError,
    GatewayUnauthorized,
    GatewayUnavailable,
)
from jcheng_applications.model import CONTRACT_HASH
from jcheng_applications.transport import (
    GatewayEndpoint,
    ReadOnlyGateway,
    StateSnapshot,
)

TOKEN = "transport-test-token"
NOW = "2026-11-26T00:00:00+00:00"


def _contract_payload(*, contract_hash: str = CONTRACT_HASH) -> dict[str, Any]:
    return {
        "contract": {
            "major": 0,
            "minor": 1,
            "patch": 0,
            "contract_hash": contract_hash,
        },
        "profile_hash": "profile-abc",
        "system_version": "0.1.0",
    }


SAFETY = {
    "state": "ARMED_IDLE",
    "stop_latched": False,
    "seq": 3,
    "last_valid_state_at": NOW,
}

STATE = {
    "header": {"stamp": NOW, "frame_id": "base_link"},
    "truth": "measured",
    "verification": "bus",
    "fresh": True,
    "profile_hash": "profile-abc",
}


class _Probe:
    def __init__(self) -> None:
        self.routes: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def set(
        self,
        path: str,
        payload: Any,
        *,
        status: int = 200,
        delay_s: float = 0.0,
    ) -> None:
        self.routes[path] = {
            "payload": payload,
            "status": status,
            "delay_s": delay_s,
        }

    def reset(self) -> None:
        self.requests.clear()

    def default(self) -> None:
        self.set("/v1/health", {"status": "ok", "capabilities": ["mock"]})
        self.set("/v1/contract", _contract_payload())
        self.set("/v1/state", STATE)
        self.set("/v1/safety", SAFETY)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - HTTP verb
        probe = self.server.probe  # type: ignore[attr-defined]
        probe.requests.append(
            (self.command, self.path, {key.lower(): value for key, value in self.headers.items()})
        )
        route = probe.routes.get(self.path)
        if route is None:
            self._send(404, {"error": {"code": "NOT_FOUND"}})
            return
        if route["delay_s"]:
            time.sleep(route["delay_s"])
        payload = route["payload"]
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self._send(route["status"], body)

    def _send(self, status: int, body: bytes | dict[str, Any]) -> None:
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


class MockGatewayServer:
    def __init__(self) -> None:
        self.probe = _Probe()
        self.probe.default()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.probe = self.probe  # type: ignore[attr-defined]
        self.httpd.handle_error = lambda *args, **kwargs: None  # type: ignore[assignment]
        self._thread = Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def __enter__(self) -> "MockGatewayServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=2.0)


def _endpoint(port: int, timeout_s: float = 1.0) -> GatewayEndpoint:
    return GatewayEndpoint(
        base_url=f"http://127.0.0.1:{port}", token=TOKEN, timeout_s=timeout_s
    )


def test_admission_is_get_only_and_reports_ready() -> None:
    with MockGatewayServer() as server:
        report = ReadOnlyGateway(_endpoint(server.port)).check_admission()
        methods = {method for method, _, _ in server.probe.requests}
        paths = {path for _, path, _ in server.probe.requests}
        assert methods == {"GET"}
        assert paths == {"/v1/health", "/v1/contract", "/v1/safety"}
        assert report.ready is True
        assert report.contract.contract_hash == CONTRACT_HASH


def test_health_is_unauthenticated_and_safety_is_authenticated() -> None:
    with MockGatewayServer() as server:
        gateway = ReadOnlyGateway(_endpoint(server.port))
        gateway.health()
        gateway.safety()
        health_headers = server.probe.requests[0][2]
        safety_headers = server.probe.requests[1][2]
        assert "authorization" not in health_headers
        assert safety_headers["authorization"] == f"Bearer {TOKEN}"


def test_contract_mismatch_refuses() -> None:
    with MockGatewayServer() as server:
        server.probe.set(
            "/v1/contract",
            _contract_payload(contract_hash="sha256:" + "0" * 64),
        )
        with pytest.raises(GatewayContractMismatch):
            ReadOnlyGateway(_endpoint(server.port)).check_admission()


def test_unauthorized_is_not_retried() -> None:
    with MockGatewayServer() as server:
        server.probe.set("/v1/safety", {"error": "no"}, status=401)
        with pytest.raises(GatewayUnauthorized):
            ReadOnlyGateway(_endpoint(server.port)).safety()
        assert len(server.probe.requests) == 1


def test_read_timeout_is_bounded() -> None:
    with MockGatewayServer() as server:
        server.probe.set("/v1/safety", SAFETY, delay_s=0.5)
        started = time.monotonic()
        with pytest.raises(GatewayUnavailable):
            ReadOnlyGateway(_endpoint(server.port, timeout_s=0.1)).safety()
        assert time.monotonic() - started < 1.0


def test_unreachable_is_bounded_and_never_retried() -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    started = time.monotonic()
    with pytest.raises(GatewayUnavailable):
        ReadOnlyGateway(_endpoint(port, timeout_s=0.2)).contract()
    assert time.monotonic() - started < 1.0


def test_malformed_json_refuses() -> None:
    with MockGatewayServer() as server:
        server.probe.set("/v1/safety", b"{not-json")
        with pytest.raises(GatewayProtocolError):
            ReadOnlyGateway(_endpoint(server.port)).safety()


def test_oversized_response_refuses() -> None:
    with MockGatewayServer() as server:
        server.probe.set("/v1/safety", b"x" * 2048)
        with pytest.raises(GatewayProtocolError):
            ReadOnlyGateway(
                GatewayEndpoint(
                    base_url=f"http://127.0.0.1:{server.port}",
                    token=TOKEN,
                    timeout_s=1.0,
                    max_response_bytes=1024,
                )
            ).safety()


def test_commanded_state_is_not_measured() -> None:
    with MockGatewayServer() as server:
        commanded = dict(STATE, truth="commanded", verification="command_cache")
        server.probe.set("/v1/state", commanded)
        state = ReadOnlyGateway(_endpoint(server.port)).state()
        assert isinstance(state, StateSnapshot)
        assert state.truth == "commanded"
        assert state.is_measured is False


def test_state_requires_header_stamp() -> None:
    with MockGatewayServer() as server:
        # The contract carries the stamp in header.stamp, not at top level.
        server.probe.set(
            "/v1/state",
            {
                "truth": "measured",
                "verification": "bus",
                "fresh": True,
                "profile_hash": "profile-abc",
                "stamp": NOW,
            },
        )
        with pytest.raises(GatewayProtocolError):
            ReadOnlyGateway(_endpoint(server.port)).state()


def test_endpoint_rejects_userinfo_path_query_and_bad_port() -> None:
    for url in (
        "http://user:pass@127.0.0.1:8766",
        "http://127.0.0.1:8766/api",
        "http://127.0.0.1:8766?x=1",
        "http://127.0.0.1:0",
    ):
        with pytest.raises(ValueError):
            GatewayEndpoint(base_url=url, token=TOKEN)


def test_endpoint_from_env() -> None:
    endpoint = GatewayEndpoint.from_env(
        {
            "JCHENG_GATEWAY_URL": "http://127.0.0.1:8766",
            "JCHENG_GATEWAY_TOKEN": "env-token",
            "JCHENG_GATEWAY_TIMEOUT_S": "0.5",
        }
    )
    assert endpoint.host == "127.0.0.1"
    assert endpoint.port == 8766
    assert endpoint.timeout_s == 0.5


def test_server_error_is_unavailable_and_not_retried() -> None:
    with MockGatewayServer() as server:
        server.probe.set("/v1/contract", {"error": "down"}, status=503)
        with pytest.raises(GatewayUnavailable):
            ReadOnlyGateway(_endpoint(server.port)).contract()
        assert len(server.probe.requests) == 1
