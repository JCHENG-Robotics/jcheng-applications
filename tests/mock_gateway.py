"""A loopback mock gateway for the M3 tests.

It implements the subset of ``jcheng-gateway.v1`` the application uses. It is
stateful only in the request log; motion results are scripted. No robot, DDS
or device is involved.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
import time
from typing import Any

from jcheng_applications.model import CONTRACT_HASH

NOW = "2026-11-26T00:00:00+00:00"
TOKEN = "m3-test-token"


def contract_payload() -> dict[str, Any]:
    return {
        "contract": {
            "major": 0,
            "minor": 1,
            "patch": 0,
            "contract_hash": CONTRACT_HASH,
        },
        "profile_hash": "profile-m3",
        "system_version": "0.1.0",
    }


def safety_payload() -> dict[str, Any]:
    return {
        "state": "ARMED_IDLE",
        "stop_latched": False,
        "seq": 1,
        "last_valid_state_at": NOW,
    }


def state_payload() -> dict[str, Any]:
    return {
        "header": {"stamp": NOW, "frame_id": "base_link"},
        "truth": "measured",
        "verification": "bus",
        "fresh": True,
        "profile_hash": "profile-m3",
    }


def completed(
    *,
    outcome: str = "COMPLETED",
    code: str = "OK",
    bus_verified: bool = True,
    safety_state: str = "ARMED_IDLE",
    stop_latched: bool = False,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "reason_code": code,
        "message": "",
        "bus_verified": bus_verified,
        "safety": {
            "state": safety_state,
            "stop_latched": stop_latched,
            "seq": 1,
            "last_valid_state_at": NOW,
        },
        "measured_positions": [],
    }


class _Probe:
    def __init__(self, capabilities: list[str]) -> None:
        self.capabilities = capabilities
        self.requests: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.fail_renew = False
        self.execute_status: int | None = None
        self.execute_delay_s = 0.0
        self.lock = Lock()

    def record(self, method: str, path: str, headers: dict[str, str], body: Any) -> None:
        with self.lock:
            self.requests.append(
                {"method": method, "path": path, "headers": headers, "body": body}
            )

    def paths(self, method: str | None = None) -> list[str]:
        with self.lock:
            return [
                item["path"]
                for item in self.requests
                if method is None or item["method"] == method
            ]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self, method: str) -> None:
        probe: _Probe = self.server.probe  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}
        headers = {key.lower(): value for key, value in self.headers.items()}
        probe.record(method, self.path, headers, body)
        path = self.path.split("?", 1)[0]

        if method == "GET" and path == "/v1/health":
            self._send(200, {"status": "ok", "capabilities": probe.capabilities})
            return
        if method == "GET" and path == "/v1/contract":
            self._send(200, contract_payload())
            return
        if method == "GET" and path == "/v1/safety":
            self._send(200, safety_payload())
            return
        if method == "GET" and path == "/v1/state":
            self._send(200, state_payload())
            return
        if method == "POST" and path == "/v1/leases":
            self._send(
                200,
                {
                    "lease_id": "lease-m3",
                    "holder": body.get("holder", ""),
                    "scope": body.get("scope", "full"),
                    "expires_at": NOW,
                },
            )
            return
        if path.startswith("/v1/leases/") and path.endswith("/renew"):
            if probe.fail_renew:
                self._send(503, {"error": {"code": "LEASE_EXPIRED"}})
                return
            self._send(
                200,
                {
                    "lease_id": path.split("/")[3],
                    "holder": "operator",
                    "scope": "full",
                    "expires_at": NOW,
                },
            )
            return
        if path.startswith("/v1/leases/") and method == "DELETE":
            self._send(200, {"released": True})
            return
        if method == "POST" and path == "/v1/motion/execute":
            if probe.execute_status is not None:
                self._send(probe.execute_status, {"error": {"code": "TEST"}})
                return
            if probe.execute_delay_s:
                time.sleep(probe.execute_delay_s)
            with probe.lock:
                result = (
                    probe.results.pop(0)
                    if probe.results
                    else completed()
                )
            result = dict(result)
            result.setdefault("intent_id", body.get("intent_id", "intent"))
            self._send(200, result)
            return
        if method == "POST" and path == "/v1/motion/stop":
            self._send(
                200,
                {
                    "accepted": True,
                    "safety": safety_payload(),
                    "message": "software stop requested",
                    "requested_at": NOW,
                },
            )
            return
        self._send(404, {"error": {"code": "NOT_FOUND"}})

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


class MockGatewayServer:
    def __init__(
        self, *, capabilities: list[str] | None = None
    ) -> None:
        self.probe = _Probe(capabilities or ["mock"])
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


def wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
