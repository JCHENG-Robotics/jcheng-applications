"""Read-only audit of a persisted run record."""

from __future__ import annotations

import json
from pathlib import Path

from jcheng_applications.cli import main
from jcheng_applications.model import Authorization, load_recipe
from jcheng_applications.runstore import RunStore
from jcheng_applications.supervised import run_supervised
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed

ROOT = Path(__file__).resolve().parents[1]
RECIPE = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")


def _auth() -> Authorization:
    return Authorization(
        operator="operator",
        session_id="session-audit",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )


def _complete_run(store: RunStore) -> None:
    with MockGatewayServer() as server:
        server.probe.results.extend([completed(), completed()])
        endpoint = GatewayEndpoint(
            base_url=f"http://127.0.0.1:{server.port}", token=TOKEN, timeout_s=1.0
        )
        run_supervised(
            HttpGateway(endpoint, operator_id="operator"),
            RECIPE,
            _auth(),
            run_id="run-audit",
            store=store,
            recipe_hash="recipe-hash",
            evidence_class="L2",
        )


def test_verify_accepts_a_complete_record(tmp_path: Path, capsys) -> None:
    store = RunStore(tmp_path / "runs")
    _complete_run(store)
    code = main(
        ["verify", "run-audit", "--store-dir", str(tmp_path / "runs")]
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["verified"] is True
    assert output["complete"] is True
    assert output["state"] == "SUCCEEDED"
    assert output["recipe_name"] == RECIPE.name
    assert output["evidence_class"] == "L2"
    assert [step["name"] for step in output["steps"]] == ["approach", "grasp"]


def test_verify_reports_stop_levels_from_the_record(tmp_path: Path, capsys) -> None:
    store = RunStore(tmp_path / "runs")
    store.append(
        "run-stop",
        "run_started",
        {
            "run_id": "run-stop",
            "recipe_name": "stop-example",
            "recipe_version": "0.1.0",
            "evidence_class": "L2",
        },
    )
    store.append(
        "run-stop",
        "run_finished",
        {
            "run_id": "run-stop",
            "state": "FAILED",
            "steps": [],
            "stop_confirmation": "request_accepted",
            "physical_stop": "unverified",
            "stop_error": None,
            "operator_required": False,
            "report_hash": "digest",
        },
    )
    code = main(["verify", "run-stop", "--store-dir", str(tmp_path / "runs")])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["stop_confirmation"] == "request_accepted"
    assert output["physical_stop"] == "unverified"


def test_verify_rejects_a_tampered_chain(tmp_path: Path, capsys) -> None:
    store = RunStore(tmp_path / "runs")
    _complete_run(store)
    path = store.path_for("run-audit")
    path.write_text(
        path.read_text(encoding="utf-8").replace('"SUCCEEDED"', '"FAILED"'),
        encoding="utf-8",
    )
    code = main(["verify", "run-audit", "--store-dir", str(tmp_path / "runs")])
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert code == 1
    assert output["verified"] is False
    assert output["complete"] is False
    assert "hash chain" in captured.err


def test_verify_treats_unparseable_json_as_a_chain_failure(
    tmp_path: Path, capsys
) -> None:
    store = RunStore(tmp_path / "runs")
    _complete_run(store)
    store.path_for("run-audit").write_text("{not-json\n", encoding="utf-8")
    code = main(["verify", "run-audit", "--store-dir", str(tmp_path / "runs")])
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert code == 1
    assert output["verified"] is False
    assert output["complete"] is False
    assert "hash chain" in captured.err


def test_verify_reports_an_incomplete_record(tmp_path: Path, capsys) -> None:
    store = RunStore(tmp_path / "runs")
    store.append("run-open", "run_started", {"run_id": "run-open"})
    code = main(["verify", "run-open", "--store-dir", str(tmp_path / "runs")])
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert code == 1
    assert output["verified"] is True
    assert output["complete"] is False
    assert "run_finished" in captured.err


def test_verify_missing_record_does_not_create_the_store(
    tmp_path: Path, capsys
) -> None:
    store_dir = tmp_path / "absent"
    code = main(["verify", "run-missing", "--store-dir", str(store_dir)])
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert code == 1
    assert output["verified"] is False
    assert output["complete"] is False
    assert store_dir.exists() is False


def test_verify_rejects_an_unsafe_run_id(tmp_path: Path, capsys) -> None:
    code = main(
        ["verify", "../escape", "--store-dir", str(tmp_path / "runs")]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "unsafe run_id" in captured.err
