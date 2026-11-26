"""Offline CLI tests. No gateway, network or robot is used."""

from __future__ import annotations

import json
from pathlib import Path

from jcheng_applications.cli import main

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "recipes" / "example-supervised-transfer.yaml"


def test_validate_example(capsys) -> None:
    assert main(["validate", str(RECIPE)]) == 0
    output = capsys.readouterr().out
    assert "OK example-supervised-transfer 0.1.0" in output
    assert "2 step(s)" in output


def test_plan_prints_deterministic_steps(capsys) -> None:
    assert main(["plan", str(RECIPE), "--run-id", "run-001"]) == 0
    output = capsys.readouterr().out
    assert "plan example-supervised-transfer 0.1.0 run=run-001 steps=2" in output
    assert "approach: JOINT_STEP/step" in output
    assert "grasp: GRIPPER/gripper" in output


def test_plan_json_is_machine_readable(capsys) -> None:
    assert main(["plan", str(RECIPE), "--run-id", "run-001", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"] == "run-001"
    assert payload["recipe"] == {
        "name": "example-supervised-transfer",
        "version": "0.1.0",
    }
    assert len(payload["steps"]) == 2
    assert all(len(step["idempotency_key"]) == 64 for step in payload["steps"])


def test_missing_file_returns_two(tmp_path: Path, capsys) -> None:
    assert main(["validate", str(tmp_path / "absent.yaml")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_invalid_recipe_returns_two(tmp_path: Path, capsys) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("name: bad\n", encoding="utf-8")
    assert main(["validate", str(path)]) == 2
    assert "missing field" in capsys.readouterr().err


def test_invalid_run_id_returns_two(capsys) -> None:
    assert main(["plan", str(RECIPE), "--run-id", "bad id"]) == 2
    assert "run_id" in capsys.readouterr().err
