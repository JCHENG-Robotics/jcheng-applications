"""Eight-case synthetic ore-sorting task over the mock gateway.

No device, DDS, ROS, camera, perception model, IK solver or hardware is used.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

import armpi_ore_sorting_reference as reference
import ore_sorting_mock_task as task


def _write(path: Path, payload: dict) -> Path:
    document = copy.deepcopy(payload)
    document["content_hash"] = reference.content_hash(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _device(role: str, device_id: str, capabilities: list[dict]) -> dict:
    return {
        "schema": "jcheng.device_snapshot.v1",
        "source_device": device_id,
        "device_version": "0.1.0",
        "source_hash": "sha256:" + "a" * 64,
        "content_hash": "",
        "generated_by": "test",
        "source_path": f"registry/devices/{device_id}.yaml",
        "capabilities": capabilities,
        "joints": (
            [
                {
                    "name": f"joint{index}",
                    "role": f"joint{index}",
                    "limits": {"min_deg": -90.0, "max_deg": 90.0},
                }
                for index in range(1, 6)
            ]
            if role == "arm"
            else []
        ),
        "attributes": {
            "category": {
                "arm": "arm",
                "gripper": "end_effector",
                "camera": "sensor",
            }[role],
            "role": role,
            "dof": 5 if role == "arm" else None,
            "control_modes": ["position"] if role == "arm" else [],
            "kinematics_declared": role == "arm",
        },
        "safety_overlay": {
            "applied": False,
            "source": None,
            "content_hash": None,
            "stricter_wins": True,
        },
        "notes": "test snapshot",
    }


def _requirements() -> dict:
    return {
        "schema": "jcheng.scenario_requirements.v1",
        "scenario_id": "ore_sorting",
        "scenario_version": "0.1.0",
        "vocabulary_status": "local_draft",
        "content_hash": "",
        "task_id": "ore_sorting.task@0.1.0",
        "evaluation_id": "ore_sorting.evaluation@0.1.0",
        "capabilities": [
            {
                "id": "manipulation.arm.dof",
                "requirement_id": "arm_dof",
                "requirement_owner": "device",
                "required": True,
                "role": "arm",
                "interface": "arm.position_control",
                "threshold": {"op": ">=", "value": 4, "unit": "joints"},
                "statement": "at least four joints",
                "verification": "Unverified",
            },
            {
                "id": "manipulation.gripper.aperture_m",
                "requirement_id": "gripper_aperture",
                "requirement_owner": "device",
                "required": True,
                "role": "gripper",
                "interface": "end_effector.position_control",
                "threshold": {"op": ">=", "value": 0.04, "unit": "m"},
                "statement": "gripper aperture",
                "verification": "Unverified",
            },
            {
                "id": "sensing.camera.resolution_px",
                "requirement_id": "camera_resolution",
                "requirement_owner": "device",
                "required": True,
                "role": "camera",
                "interface": "sensor.rgb_stream",
                "statement": "640 x 480",
                "verification": "Unverified",
            },
            {
                "id": "perception.scene_state",
                "requirement_id": "scene_state",
                "requirement_owner": "algorithm",
                "required": True,
                "deferred_to": "jcheng-perception",
                "statement": "advisory scene state",
                "verification": "Unverified",
            },
        ],
        "constraints": [{"id": "mock_only", "statement": "offline only"}],
        "provenance": {"generated_by": "test"},
    }


def _cell(tmp_path: Path) -> list[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    arm = _write(
        tmp_path / "arm.yaml",
        _device(
            "arm",
            "hiwonder.armpi-ultra",
            [
                {
                    "id": "arm.position_control",
                    "parameters": {},
                    "feedback": [],
                }
            ],
        ),
    )
    gripper = _write(
        tmp_path / "gripper.yaml",
        _device(
            "gripper",
            "hiwonder.armpi-ultra-gripper",
            [
                {
                    "id": "end_effector.position_control",
                    "parameters": {},
                    "feedback": [],
                }
            ],
        ),
    )
    camera = _write(
        tmp_path / "camera.yaml",
        _device(
            "camera",
            "intel.realsense-d435i",
            [{"id": "sensor.rgb_stream", "parameters": {}, "feedback": []}],
        ),
    )
    scenario = _write(tmp_path / "requirements.yaml", _requirements())
    return [
        "--device-snapshot",
        f"arm={arm}",
        "--device-snapshot",
        f"gripper={gripper}",
        "--device-snapshot",
        f"camera={camera}",
        "--scenario-requirements",
        str(scenario),
    ]


def _run(tmp_path: Path, *extra: str) -> tuple[int, Path]:
    out = tmp_path / "out"
    code = task.main([*_cell(tmp_path), "--out", str(out), *extra])
    return code, out


def _summary(out: Path) -> dict:
    return json.loads((out / "task_summary.json").read_text(encoding="utf-8"))


def test_all_eight_cases_have_the_expected_classification(tmp_path: Path) -> None:
    code, out = _run(tmp_path, "--case", "all")
    assert code == 0
    summary = _summary(out)
    assert summary["mismatches"] == []
    assert summary["capability_match_status"] == "matched_with_unverified"
    assert summary["artifact_valid"] is True
    assert summary["capability_satisfied"] is False
    assert summary["offline_execution_allowed"] is True
    assert summary["deployment_ready"] is False
    assert summary["motion_authorized"] is False
    assert summary["scenario_requirements"]["evaluation_id"] == (
        "ore_sorting.evaluation@0.1.0"
    )
    sample = json.loads(
        (out / "samples.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert sample["evaluation_id"] == "ore_sorting.evaluation@0.1.0"
    assert {case: item["status"] for case, item in summary["cases"].items()} == {
        case: task.EXPECTED_STATUS[case] for case in task.CASES
    }


def test_normal_case_completes_six_mock_steps_and_places_one_ore(
    tmp_path: Path,
) -> None:
    code, out = _run(tmp_path, "--case", "normal")
    assert code == 0
    result = json.loads((out / "case-normal.json").read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert result["success"] is True
    assert result["placed"] == {
        "object_id": "ore-001",
        "class": "ore",
        "bin": "ore_bin",
        "data_source": "synthetic",
    }
    assert result["plan"]["step_count"] == 6
    assert result["run"]["state"] == "SUCCEEDED"
    assert len(result["run"]["steps"]) == 6
    assert result["run"]["request_counts"]["POST /v1/motion/execute"] == 6
    assert "POST /v1/motion/stop" not in result["run"]["request_counts"]


def test_no_target_unreachable_and_capability_unmet_submit_no_motion(
    tmp_path: Path,
) -> None:
    for case, status in (
        ("no-target", "NO_TARGET"),
        ("unreachable", "UNREACHABLE"),
        ("capability-unmet", "CAPABILITY_UNMET"),
    ):
        code, out = _run(tmp_path / case, "--case", case)
        assert code == 0
        result = json.loads(
            (out / f"case-{case}.json").read_text(encoding="utf-8")
        )
        assert result["status"] == status
        assert result["success"] is False
        assert result["run"] is None


def test_failure_cases_stop_once_without_claiming_success(tmp_path: Path) -> None:
    for case, status in (
        ("grasp-failure", "GRASP_FAILED"),
        ("timeout", "TIMEOUT"),
        ("stop-request", "STOP_REQUESTED"),
    ):
        code, out = _run(tmp_path / case, "--case", case)
        assert code == 0
        result = json.loads(
            (out / f"case-{case}.json").read_text(encoding="utf-8")
        )
        assert result["status"] == status
        assert result["success"] is False
        assert result["placed"] is None
        assert result["run"]["request_counts"]["POST /v1/motion/stop"] == 1


def test_authorization_failure_submits_no_execute(tmp_path: Path) -> None:
    code, out = _run(tmp_path, "--case", "authorization-failure")
    assert code == 0
    result = json.loads(
        (out / "case-authorization-failure.json").read_text(encoding="utf-8")
    )
    assert result["status"] == "AUTHORIZATION_FAILED"
    assert result["run"]["state"] == "ABORTED"
    assert "POST /v1/motion/execute" not in result["run"]["request_counts"]
    assert result["run"]["steps"] == []


def test_benchmark_suites_declare_the_intended_discrimination() -> None:
    observed = task._suite("observed", 8)
    test_pass = task._suite("test-pass", 8)
    test_fail = task._suite("test-fail", 8)
    assert observed["thresholds"] == []
    assert test_pass["thresholds"][0]["value"] == 0.10
    assert test_fail["thresholds"][0]["value"] == 0.99
    for kind, suite in (
        ("observed", observed),
        ("test-pass", test_pass),
        ("test-fail", test_fail),
    ):
        assert suite["data_source"] == "synthetic"
        assert suite["level"] == "L2"
        assert suite["limitations"]
