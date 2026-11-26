"""T-C06 for declared branches: tree and linear full-report parity.

Skipped unless the optional py_trees extra is installed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

pytest.importorskip("py_trees")

from jcheng_applications.model import (  # noqa: E402
    Authorization,
    load_recipe,
    recipe_from_mapping,
)
from jcheng_applications.runstore import RunStore  # noqa: E402
from jcheng_applications.supervised import run_supervised  # noqa: E402
from jcheng_applications.transport import GatewayEndpoint  # noqa: E402
from jcheng_applications.write_transport import HttpGateway  # noqa: E402

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATH = ROOT / "recipes" / "example-declared-branch.yaml"
RECIPE = load_recipe(RECIPE_PATH)
FIXED = datetime(2026, 11, 26, 0, 0, 0, tzinfo=timezone.utc)


def _gateway(port: int) -> HttpGateway:
    endpoint = GatewayEndpoint(
        base_url=f"http://127.0.0.1:{port}", token=TOKEN, timeout_s=1.0
    )
    return HttpGateway(endpoint, operator_id="operator")


def _auth() -> Authorization:
    return Authorization(
        operator="operator",
        session_id="session-s3-tree",
        scopes=frozenset({"arm"}),
        approved=True,
        approved_at=NOW,
    )


def _required_recipe():
    raw = yaml.safe_load(RECIPE_PATH.read_text(encoding="utf-8"))
    raw["branches"][0]["required"] = True
    return recipe_from_mapping(raw)


def _run(server: MockGatewayServer, store: RunStore, engine: str, recipe=RECIPE):
    return run_supervised(
        _gateway(server.port),
        recipe,
        _auth(),
        run_id="run-1",
        store=store,
        recipe_hash="recipe-hash",
        evidence_class="L2",
        engine=engine,
        clock=lambda: FIXED,
    )


def _parity(tmp_path: Path, script: list[dict], *, recipe=RECIPE):
    with MockGatewayServer() as server:
        server.probe.results = [dict(item) for item in script]
        linear = _run(server, RunStore(tmp_path / "linear"), "linear", recipe)

        server.probe.results = [dict(item) for item in script]
        tree = _run(server, RunStore(tmp_path / "tree"), "tree", recipe)

    assert tree.report == linear.report
    assert tree.lease_released is True
    assert linear.lease_released is True
    return linear.report


def test_selected_branch_has_full_report_parity(tmp_path: Path) -> None:
    report = _parity(
        tmp_path,
        [
            completed(measured_positions=[0.03]),
            completed(measured_positions=[0.05]),
        ],
    )
    assert report.state.value == "SUCCEEDED"
    assert report.selections[0].selected == "adjust-j1.v1"
    assert [step.name for step in report.steps] == ["approach-j1", "adjust-j1"]


def test_optional_branch_not_selected_has_full_report_parity(tmp_path: Path) -> None:
    report = _parity(
        tmp_path, [completed(measured_positions=[0.0])]
    )
    assert report.state.value == "SUCCEEDED"
    assert report.selections[0].selected is None
    assert report.selections[0].candidates[0].guard == "FAIL"


def test_required_branch_without_candidate_has_full_report_parity(
    tmp_path: Path,
) -> None:
    report = _parity(
        tmp_path,
        [completed(measured_positions=[0.0])],
        recipe=_required_recipe(),
    )
    assert report.state.value == "ABORTED"
    assert "required branch" in (report.abort_reason or "")


def test_branch_step_failure_has_full_report_parity(tmp_path: Path) -> None:
    report = _parity(
        tmp_path,
        [
            completed(measured_positions=[0.03]),
            completed(
                outcome="REJECTED", code="X", bus_verified=False
            ),
        ],
    )
    assert report.state.value == "FAILED"
    assert [step.outcome for step in report.steps] == ["COMPLETED", "REJECTED"]
