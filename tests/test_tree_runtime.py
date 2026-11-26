"""M1.5 production tree runtime and supervisor.

Skipped unless the optional ``tree`` extra is installed. CI installs
py_trees; the default gate on a bare interpreter skips this module.
"""

from __future__ import annotations

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


def _gateway(port: int) -> HttpGateway:
    endpoint = GatewayEndpoint(
        base_url=f"http://127.0.0.1:{port}", token=TOKEN, timeout_s=1.0
    )
    return HttpGateway(endpoint, operator_id="operator")


def _auth() -> Authorization:
    return Authorization(
        operator="operator",
        session_id="session-m15",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )


def _run(server: MockGatewayServer, store: RunStore, *, engine: str, deadline_s=None):
    return run_supervised(
        _gateway(server.port),
        RECIPE,
        _auth(),
        run_id="run-1",
        store=store,
        recipe_hash="recipe-hash",
        evidence_class="L2",
        engine=engine,
        deadline_s=deadline_s,
    )


def test_tree_success_matches_linear(tmp_path: Path) -> None:
    with MockGatewayServer() as linear_server:
        linear_server.probe.results.extend([completed(), completed()])
        linear = _run(linear_server, RunStore(tmp_path / "linear"), engine="linear")
    with MockGatewayServer() as tree_server:
        tree_server.probe.results.extend([completed(), completed()])
        tree = _run(tree_server, RunStore(tmp_path / "tree"), engine="tree")

    assert tree.report.state.value == "SUCCEEDED"
    assert tree.lease_released is True
    assert tree.report.state == linear.report.state
    assert [step.outcome for step in tree.report.steps] == [
        step.outcome for step in linear.report.steps
    ]
    assert [step.reason_code for step in tree.report.steps] == [
        step.reason_code for step in linear.report.steps
    ]
    assert linear_server.probe.paths().count("/v1/motion/stop") == 0
    assert tree_server.probe.paths().count("/v1/motion/stop") == 0
    assert RunStore(tmp_path / "tree").verify("run-1") is True


def test_tree_rejection_stops_once_and_records_failure(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.results.append(
            completed(outcome="REJECTED", code="LIMIT_VIOLATION", bus_verified=False)
        )
        store = RunStore(tmp_path / "runs")
        result = _run(server, store, engine="tree")
        assert result.report.state.value == "FAILED"
        assert "rejected" in (result.report.abort_reason or "")
        assert server.probe.paths().count("/v1/motion/stop") == 1
        assert store.verify("run-1") is True


def test_tree_deadline_stops_and_is_indeterminate(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        server.probe.execute_delay_s = 0.3
        store = RunStore(tmp_path / "runs")
        result = _run(server, store, engine="tree", deadline_s=0.1)
        assert result.report.state.value == "INDETERMINATE"
        assert result.report.steps[0].outcome == "UNKNOWN"
        assert result.report.steps[0].reason_code == "DEADLINE"
        assert server.probe.paths().count("/v1/motion/stop") == 1
        assert result.lease_released is True
        assert store.verify("run-1") is True


def test_unknown_engine_and_linear_deadline_are_refused(tmp_path: Path) -> None:
    with MockGatewayServer() as server:
        store = RunStore(tmp_path / "runs")
        with pytest.raises(ValueError):
            _run(server, store, engine="magic")
        with pytest.raises(ValueError):
            _run(server, store, engine="linear", deadline_s=1.0)
