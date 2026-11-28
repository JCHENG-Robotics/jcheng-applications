"""Offline ArmPi Ultra x ore_sorting reference integration.

Every test runs against the loopback mock gateway only. No device, DDS, ROS or
real system gateway is contacted, and no test calls the sibling repositories
except through the benchmark CLI contract with a local stub.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import textwrap

import yaml

import armpi_ore_sorting_reference as reference

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEVICE_FIXTURE = FIXTURES / "armpi-ultra.device-snapshot.yaml"
SCENARIO_FIXTURE = FIXTURES / "ore-sorting.scenario-requirements.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict, *, recompute: bool = True) -> Path:
    document = copy.deepcopy(payload)
    if recompute:
        document["content_hash"] = reference.content_hash(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _run(tmp_path: Path, *extra: str, device: Path | None = None,
         scenario: Path | None = None) -> tuple[int, Path]:
    out = tmp_path / "out"
    argv = [
        "--device-snapshot",
        str(device or DEVICE_FIXTURE),
        "--scenario-requirements",
        str(scenario or SCENARIO_FIXTURE),
        "--out",
        str(out),
        *extra,
    ]
    return reference.main(argv), out


def _report(out: Path) -> dict:
    return json.loads((out / "reference_report.json").read_text(encoding="utf-8"))


def _fake_benchmark_root(tmp_path: Path) -> Path:
    root = tmp_path / "fake-benchmark"
    package = root / "src" / "jcheng_benchmark"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        textwrap.dedent(
            """
            import hashlib
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]

            def flag(name):
                return args[args.index(name) + 1]

            spec = Path(flag("--spec"))
            data = Path(flag("--data"))
            report_path = Path(flag("--json"))
            exit_code = int(os.environ.get("STUB_BENCHMARK_EXIT", "2"))
            if os.environ.get("STUB_BENCHMARK_WRITE", "1") == "1":
                samples = [
                    line
                    for line in data.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                report_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "report_kind": "benchmark-report",
                            "suite": {
                                "id": "stub",
                                "sha256": hashlib.sha256(
                                    spec.read_bytes()
                                ).hexdigest(),
                            },
                            "dataset": {
                                "sha256": hashlib.sha256(
                                    data.read_bytes()
                                ).hexdigest(),
                            },
                            "metrics": {
                                "mock_step_completion": {
                                    "kind": "success_rate",
                                    "unit": "ratio",
                                    "rate": 0.5,
                                    "count": len(samples),
                                }
                            },
                            "result": "inconclusive",
                            "result_reason": "stub benchmark",
                        }
                    ),
                    encoding="utf-8",
                )
            print("stub benchmark ran")
            raise SystemExit(exit_code)
            """
        ),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Valid path and reproducibility
# ---------------------------------------------------------------------------


def test_valid_reference_runs_offline_and_records_identity(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    report = _report(out)
    assert report["schema"] == reference.REPORT_SCHEMA
    assert report["data_source"] == "synthetic"
    assert report["device"]["source_device"] == "hiwonder.armpi-ultra"
    assert report["device"]["device_version"] == "0.1.0"
    assert report["scenario_requirements"]["scenario_id"] == "ore_sorting"
    assert report["scenario_requirements"]["scenario_version"] == "0.1.0"
    assert report["scenario_requirements"]["task_id"] == "ore_sorting.task@0.1.0"
    assert (
        report["scenario_requirements"]["evaluation_id"]
        == "ore_sorting.evaluation@0.1.0"
    )
    assert report["capability_match"]["status"] == "matched"
    assert report["capability_match"]["optional_absent"] == [
        "manipulation.base.velocity_intent"
    ]
    assert report["task_evaluation"]["evaluated"] is False


def test_valid_reference_is_byte_identical_across_runs(tmp_path):
    first, first_out = _run(tmp_path / "first")
    second, second_out = _run(tmp_path / "second")
    assert first == 0 and second == 0
    for name in ("reference_report.json", "plan.json", "synthetic_samples.jsonl"):
        assert (first_out / name).read_bytes() == (second_out / name).read_bytes()


def test_plan_has_fixed_order_and_ids(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    plan = _report(out)["plan"]
    assert plan["schema"] == reference.PLAN_SCHEMA
    assert plan["step_count"] == 2
    assert plan["order"] == ["home", "prepare-gripper"]
    assert [step["index"] for step in plan["steps"]] == [0, 1]
    assert plan["scenario_id"] == "ore_sorting"
    assert plan["task_id"] == "ore_sorting.task@0.1.0"
    assert plan["evaluation_id"] == "ore_sorting.evaluation@0.1.0"
    assert (out / "plan.json").read_text(encoding="utf-8") == json.dumps(
        plan, indent=2, sort_keys=True
    ) + "\n"


def test_json_artifacts_are_accepted(tmp_path):
    device_json = tmp_path / "device.json"
    device_json.write_text(
        json.dumps(_load(DEVICE_FIXTURE)), encoding="utf-8"
    )
    scenario_json = tmp_path / "scenario.json"
    scenario_json.write_text(
        json.dumps(_load(SCENARIO_FIXTURE)), encoding="utf-8"
    )
    code, out = _run(tmp_path, device=device_json, scenario=scenario_json)
    assert code == 0
    assert _report(out)["capability_match"]["status"] == "matched"


# ---------------------------------------------------------------------------
# Artifact and capability rejection
# ---------------------------------------------------------------------------


def test_unknown_capability_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["capabilities"][0]["id"] = "manipulation.arm.teleport"
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    err = capsys.readouterr().err
    assert "INCOMPATIBLE_CAPABILITY" in err
    assert "manipulation.arm.teleport" in err


def test_missing_required_capability_is_rejected(tmp_path, capsys):
    device = _load(DEVICE_FIXTURE)
    device["capabilities"] = [
        capability
        for capability in device["capabilities"]
        if capability["id"] != "manipulation.arm.dof"
    ]
    path = _write(tmp_path / "device.yaml", device)
    code, _ = _run(tmp_path, device=path)
    assert code == 1
    err = capsys.readouterr().err
    assert "INCOMPATIBLE_CAPABILITY" in err
    assert "manipulation.arm.dof" in err


def test_missing_required_field_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    del scenario["capabilities"][0]["required"]
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    assert "missing field(s): required" in capsys.readouterr().err


def test_stale_device_hash_is_rejected(tmp_path, capsys):
    device = _load(DEVICE_FIXTURE)
    device["capabilities"][0]["parameters"]["reach_m"] = 0.90
    path = _write(tmp_path / "device.yaml", device, recompute=False)
    code, _ = _run(tmp_path, device=path)
    assert code == 1
    err = capsys.readouterr().err
    assert "STALE_DEVICE_HASH" in err
    assert "content_hash mismatch" in err


def test_stale_scenario_hash_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["constraints"][0]["statement"] = "edited without regeneration"
    path = _write(tmp_path / "scenario.yaml", scenario, recompute=False)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    assert "STALE_SCENARIO_HASH" in capsys.readouterr().err


def test_scenario_version_mismatch_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["scenario_version"] = "0.2.0"
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    err = capsys.readouterr().err
    assert "SCENARIO_VERSION_MISMATCH" in err
    assert "ore_sorting.task@0.1.0" in err


def test_incompatible_parameter_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["capabilities"][0]["parameters"]["reach_m"]["value"] = 0.75
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    err = capsys.readouterr().err
    assert "INCOMPATIBLE_PARAMETER" in err
    assert "reach_m" in err


def test_unknown_schema_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["schema"] = "jcheng.scenario_requirements.v2"
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    assert "SCHEMA" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Mock execution, lease and stop
# ---------------------------------------------------------------------------


def test_mock_execution_verifies_lease_stop_and_fail_closed(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    report = _report(out)
    mock = report["mock_results"]
    assert mock["happy_path"]["state"] == "SUCCEEDED"
    assert len(mock["happy_path"]["steps"]) == 2
    assert mock["happy_path"]["request_counts"]["POST /v1/motion/execute"] == 2
    assert "POST /v1/motion/stop" not in mock["happy_path"]["request_counts"]

    failure = mock["failure_path"]
    assert failure["state"] == "FAILED"
    assert len(failure["steps"]) == 1
    assert failure["steps"][0]["outcome"] == "REJECTED"
    assert failure["request_counts"]["POST /v1/motion/execute"] == 1
    assert failure["request_counts"]["POST /v1/motion/stop"] == 1
    assert failure["stop_confirmation"] == "request_accepted"
    assert failure["physical_stop"] == "unverified"
    assert failure["operator_required"] is False
    assert report["stop_checks"]["failure_path_stops"] == 1
    assert report["stop_checks"]["happy_path_stops"] == 0


def test_lease_is_acquired_and_released_on_both_paths(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    checks = _report(out)["lease_checks"]
    assert [check["run"] for check in checks] == ["happy-path", "failure-path"]
    for check in checks:
        assert check["acquired"] is True
        assert check["released"] is True
        assert check["lost"] is False
        assert check["lease_error"] is None
        assert check["release_error"] is None
        assert check["record_written"] is True


def test_synthetic_samples_are_labelled_and_traceable(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    lines = (out / "synthetic_samples.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    samples = [json.loads(line) for line in lines]
    assert all(sample["data_source"] == "synthetic" for sample in samples)
    assert all(sample["data_kind"] == "mock_step" for sample in samples)
    assert [sample["sample_id"] for sample in samples] == [
        "happy-path-00-home",
        "happy-path-01-prepare-gripper",
        "failure-path-00-home",
    ]
    assert [sample["success"] for sample in samples] == [True, True, False]
    report = _report(out)
    assert report["data_source"] == "synthetic"
    assert report["mock_results"]["happy_path"]["steps"][0]["outcome"] == "COMPLETED"


# ---------------------------------------------------------------------------
# Benchmark: skipped, available and failing
# ---------------------------------------------------------------------------


def test_benchmark_is_skipped_without_a_root(tmp_path):
    code, out = _run(tmp_path)
    assert code == 0
    benchmark = _report(out)["benchmark"]
    assert benchmark["benchmark_executed"] is False
    assert benchmark["status"] == "skipped"
    assert "not provided" in benchmark["reason"]
    assert not (out / "benchmark_report.json").exists()


def test_benchmark_cli_is_invoked_when_available(tmp_path):
    root = _fake_benchmark_root(tmp_path)
    code, out = _run(tmp_path, "--benchmark-root", str(root))
    assert code == 0
    benchmark = _report(out)["benchmark"]
    assert benchmark["benchmark_executed"] is True
    assert benchmark["status"] == "inconclusive"
    assert benchmark["exit_code"] == 2
    assert benchmark["result"] == "inconclusive"
    assert benchmark["metrics"]["mock_step_completion"]["count"] == 3
    assert benchmark["command"][:2] == ["-m", "jcheng_benchmark"]
    assert (out / "benchmark_report.json").is_file()
    assert (out / "benchmark_suite.yaml").is_file()
    suite = yaml.safe_load((out / "benchmark_suite.yaml").read_text(encoding="utf-8"))
    assert suite["data_source"] == "synthetic"
    assert suite["thresholds"] == []
    verified = benchmark["verified_dataset_sha256"]
    assert "sha256:" + verified == benchmark["dataset"]["sha256"]


def test_benchmark_failure_is_recorded_not_faked(tmp_path, monkeypatch):
    root = _fake_benchmark_root(tmp_path)
    monkeypatch.setenv("STUB_BENCHMARK_EXIT", "1")
    monkeypatch.setenv("STUB_BENCHMARK_WRITE", "0")
    code, out = _run(tmp_path, "--benchmark-root", str(root))
    assert code == 1
    benchmark = _report(out)["benchmark"]
    assert benchmark["benchmark_executed"] is True
    assert benchmark["status"] == "failed"
    assert "metrics" not in benchmark
    assert benchmark["reason"]


def test_benchmark_root_without_the_package_is_rejected(tmp_path, capsys):
    code, _ = _run(tmp_path, "--benchmark-root", str(tmp_path / "missing"))
    assert code == 1
    assert "BENCHMARK" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Simulation asset references
# ---------------------------------------------------------------------------


def test_simulation_assets_are_not_checked_without_a_root(tmp_path):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["simulation_assets"] = [
        {"id": "ore_cell", "path": "worlds/ore_cell.sdf"}
    ]
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, out = _run(tmp_path, scenario=path)
    assert code == 0
    simulation = _report(out)["simulation_check"]
    assert simulation["status"] == "not_checked"
    assert simulation["assets"][0]["exists"] is None


def test_missing_simulation_asset_fails(tmp_path):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["simulation_assets"] = [
        {"id": "ore_cell", "path": "worlds/ore_cell.sdf"}
    ]
    path = _write(tmp_path / "scenario.yaml", scenario)
    root = tmp_path / "simulation"
    root.mkdir()
    code, out = _run(tmp_path, "--simulation-root", str(root), scenario=path)
    assert code == 1
    simulation = _report(out)["simulation_check"]
    assert simulation["status"] == "missing"
    assert "worlds/ore_cell.sdf" in simulation["reason"]


def test_present_simulation_asset_is_checked(tmp_path):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["simulation_assets"] = [
        {"id": "ore_cell", "path": "worlds/ore_cell.sdf"}
    ]
    path = _write(tmp_path / "scenario.yaml", scenario)
    root = tmp_path / "simulation"
    (root / "worlds").mkdir(parents=True)
    (root / "worlds" / "ore_cell.sdf").write_text("<sdf/>", encoding="utf-8")
    code, out = _run(tmp_path, "--simulation-root", str(root), scenario=path)
    assert code == 0
    simulation = _report(out)["simulation_check"]
    assert simulation["status"] == "ok"
    assert simulation["assets"] == [
        {"id": "ore_cell", "path": "worlds/ore_cell.sdf", "exists": True}
    ]


def test_simulation_asset_path_escape_is_rejected(tmp_path, capsys):
    scenario = _load(SCENARIO_FIXTURE)
    scenario["simulation_assets"] = [
        {"id": "escape", "path": "../outside.sdf"}
    ]
    path = _write(tmp_path / "scenario.yaml", scenario)
    code, _ = _run(tmp_path, scenario=path)
    assert code == 1
    assert "SCHEMA" in capsys.readouterr().err
