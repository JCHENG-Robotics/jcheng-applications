"""T-C06: full RunReport parity between the linear and tree engines.

The two engines run the same scripted cases against the same loopback mock
gateway with a fixed clock, so the complete ``RunReport`` objects must be
equal. Skipped unless the optional py_trees extra is installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("py_trees")

from jcheng_applications.model import Authorization, load_recipe  # noqa: E402
from jcheng_applications.runstore import RunStore  # noqa: E402
from jcheng_applications.supervised import run_supervised  # noqa: E402
from jcheng_applications.transport import GatewayEndpoint  # noqa: E402
from jcheng_applications.write_transport import HttpGateway  # noqa: E402

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RECIPE = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
FIXED = datetime(2026, 11, 26, 0, 0, 0, tzinfo=timezone.utc)


def _gateway(port: int) -> HttpGateway:
    endpoint = GatewayEndpoint(
        base_url=f"http://127.0.0.1:{port}", token=TOKEN, timeout_s=1.0
    )
    return HttpGateway(endpoint, operator_id="operator")


def _auth() -> Authorization:
    return Authorization(
        operator="operator",
        session_id="session-t06",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )


def _run(server: MockGatewayServer, store: RunStore, engine: str):
    return run_supervised(
        _gateway(server.port),
        RECIPE,
        _auth(),
        run_id="run-1",
        store=store,
        recipe_hash="recipe-hash",
        evidence_class="L2",
        engine=engine,
        clock=lambda: FIXED,
    )


CASES = {
    "success": ([completed(), completed()], {}),
    "unverified": ([completed(), completed(bus_verified=False)], {}),
    "rejected-first": (
        [completed(outcome="REJECTED", code="X", bus_verified=False)],
        {},
    ),
    "rejected-second": (
        [completed(), completed(outcome="REJECTED", code="X", bus_verified=False)],
        {},
    ),
    "aborted": ([completed(outcome="ABORTED", code="X", bus_verified=False)], {}),
    "timeout": ([completed(outcome="TIMEOUT", code="X", bus_verified=False)], {}),
    "unknown": ([completed(outcome="UNKNOWN", code="X", bus_verified=False)], {}),
    "safety-latch": (
        [completed(), completed(safety_state="STOP_LATCHED", stop_latched=True)],
        {},
    ),
    "execute-unavailable": ([], {"execute_status": 503}),
    "safety-disarmed": ([], {"safety_state": "DISARMED"}),
    "stop-fails": (
        [completed(outcome="REJECTED", code="X", bus_verified=False)],
        {"stop_status": 503},
    ),
    "malformed": ([completed(), {"outcome": "COMPLETED"}], {}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_full_report_parity(case: str, tmp_path: Path) -> None:
    script, config = CASES[case]
    with MockGatewayServer() as server:
        linear_script = [dict(item) if isinstance(item, dict) else item for item in script]
        server.probe.results = list(linear_script)
        server.probe.execute_status = config.get("execute_status")
        server.probe.safety_state = config.get("safety_state", "ARMED_IDLE")
        server.probe.stop_status = config.get("stop_status")
        linear = _run(server, RunStore(tmp_path / "linear"), "linear")

        tree_script = [dict(item) if isinstance(item, dict) else item for item in script]
        server.probe.results = list(tree_script)
        server.probe.execute_status = config.get("execute_status")
        server.probe.safety_state = config.get("safety_state", "ARMED_IDLE")
        server.probe.stop_status = config.get("stop_status")
        tree = _run(server, RunStore(tmp_path / "tree"), "tree")

        assert tree.report == linear.report, case
        assert tree.lease_released is True
        assert linear.lease_released is True
