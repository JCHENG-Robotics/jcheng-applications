#!/usr/bin/env python3
"""Minimal offline ore-sorting task over the existing applications runtime.

This is a reference task harness, not a second executor. It:

1. loads the same pinned device snapshots and scenario requirements as
   ``armpi_ore_sorting_reference.py``;
2. synthesises an explicitly-labelled observation and a candidate grasp
   target (no perception model, no IK, no hardware);
3. builds a six-step recipe through the existing recipe model and executes it
   with the existing ``run_supervised`` / linear runner against the loopback
   mock gateway;
4. writes a deterministic task result and one synthetic benchmark sample per
   case.

Every observation, target and result is ``data_source: synthetic``. A mock
success is not a real pick, a perception result or a task-accuracy claim.

Exit codes: 0 when every requested case matched its expected classification,
1 when a case or benchmark check failed, 2 for usage/artifact errors.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for _path in (REPO_ROOT / "src", REPO_ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import armpi_ore_sorting_reference as reference
from mock_gateway import MockGatewayServer, completed

from jcheng_applications.model import (
    CONTRACT_HASH,
    Authorization,
    Recipe,
    build_plan,
    recipe_from_mapping,
)
from jcheng_applications.runstore import RunStore
from jcheng_applications.supervised import run_supervised
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway

TASK_SCHEMA = "jcheng.applications.ore_sorting_task.v1"
OBSERVATION_SCHEMA = "jcheng.synthetic_observation.v1"
TASK_ID = "ore_sorting.mock_task"
TASK_VERSION = "0.1.0"
RECIPE_VERSION = "0.1.0"
DATA_SOURCE = "synthetic"
MOCK_TOKEN = reference.MOCK_TOKEN
MOCK_OPERATOR = reference.MOCK_OPERATOR
MOCK_APPROVED_AT = reference.MOCK_APPROVED_AT

CASES = (
    "normal",
    "no-target",
    "unreachable",
    "grasp-failure",
    "timeout",
    "authorization-failure",
    "stop-request",
    "capability-unmet",
)
EXPECTED_STATUS = {
    "normal": "SUCCEEDED",
    "no-target": "NO_TARGET",
    "unreachable": "UNREACHABLE",
    "grasp-failure": "GRASP_FAILED",
    "timeout": "TIMEOUT",
    "authorization-failure": "AUTHORIZATION_FAILED",
    "stop-request": "STOP_REQUESTED",
    "capability-unmet": "CAPABILITY_UNMET",
}

# Clearly synthetic cell bounds used only to reject an obviously unreachable
# target. They are not the ArmPi workspace and declare no measured reach.
SYNTHETIC_X_M = (0.18, 0.42)
SYNTHETIC_Y_M = (-0.15, 0.15)
SYNTHETIC_Z_M = (0.0, 0.10)

PRE_GRASP_JOINTS = [0.40, 0.30, -0.80, 0.40, 0.0]
PLACE_JOINTS = [-0.60, 0.20, -0.50, 0.20, 0.0]

TASK_LIMITATIONS = [
    "Synthetic observation, target and mock execution; no camera, perception "
    "model, IK solver or hardware is involved.",
    "A SUCCEEDED task means the six mock steps completed with a verified mock "
    "bus read; it is not a real grasp or ore-sorting accuracy claim.",
    "The candidate joint positions are fixed synthetic reference values, not "
    "an IK solution and not a measured trajectory.",
    "The synthetic workspace box is not the ArmPi workspace and is not a "
    "device capability claim.",
    "Capability matching may be matched_with_unverified; unverified reach, "
    "payload, aperture and resolution remain unverified.",
]


class TaskError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise TaskError(code, message)


def synthetic_observation(case: str) -> dict[str, Any]:
    """Return an explicitly synthetic observation for one case."""

    objects: list[dict[str, Any]] = []
    if case == "no-target":
        objects = []
    elif case == "unreachable":
        objects = [
            {
                "id": "ore-001",
                "class": "ore",
                "position_m": {"x": 0.60, "y": 0.00, "z": 0.02},
                "reachable": False,
            }
        ]
    else:
        objects = [
            {
                "id": "ore-001",
                "class": "ore",
                "position_m": {"x": 0.30, "y": 0.00, "z": 0.02},
                "reachable": True,
            },
            {
                "id": "gangue-001",
                "class": "gangue",
                "position_m": {"x": 0.28, "y": 0.10, "z": 0.02},
                "reachable": True,
            },
        ]
    return {
        "schema": OBSERVATION_SCHEMA,
        "data_source": DATA_SOURCE,
        "frame": "cell",
        "objects": objects,
    }


def _inside_synthetic_box(position: Mapping[str, Any]) -> bool:
    try:
        x = float(position["x"])
        y = float(position["y"])
        z = float(position["z"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        SYNTHETIC_X_M[0] <= x <= SYNTHETIC_X_M[1]
        and SYNTHETIC_Y_M[0] <= y <= SYNTHETIC_Y_M[1]
        and SYNTHETIC_Z_M[0] <= z <= SYNTHETIC_Z_M[1]
    )


def choose_grasp_target(
    observation: Mapping[str, Any], arm_joint_names: Sequence[str]
) -> dict[str, Any] | None:
    """Pick the first reachable ore instance inside the synthetic box.

    ``None`` means no ore instance exists. A ``TaskError`` with
    ``NO_GRASP_TARGET`` means ore exists but is unreachable/unusable.
    """

    if len(arm_joint_names) != len(PRE_GRASP_JOINTS):
        _fail(
            "UNSUPPORTED_ARM",
            "the mock task expects the five-joint ArmPi reference arm, found "
            f"{len(arm_joint_names)} joints",
        )
    objects = observation.get("objects")
    if not isinstance(objects, list):
        _fail("INVALID_OBSERVATION", "objects must be a list")
    ores = [item for item in objects if item.get("class") == "ore"]
    if not ores:
        return None
    for item in ores:
        if item.get("reachable") is not True:
            continue
        if not _inside_synthetic_box(item.get("position_m", {})):
            continue
        return {
            "schema": "jcheng.synthetic_grasp_target.v1",
            "data_source": DATA_SOURCE,
            "object_id": item["id"],
            "class": item["class"],
            "position_m": dict(item["position_m"]),
            "bin": "ore_bin",
            "joint_names": list(arm_joint_names),
            "pre_grasp_joint_positions": list(PRE_GRASP_JOINTS),
            "place_joint_positions": list(PLACE_JOINTS),
            "source": "synthetic fixed reference poses; not IK",
        }
    raise TaskError("NO_GRASP_TARGET", "no ore instance is reachable and inside the synthetic box")


def _gripper(position_rad: float) -> dict[str, Any]:
    return {
        "gripper": {
            "position_rad": position_rad,
            "max_velocity_rad_s": 0.1,
            "duration_s": 0.5,
            "verify_bus": True,
        }
    }


def build_task_recipe(target: Mapping[str, Any], scenario: Any) -> Recipe:
    names = list(target["joint_names"])
    steps = [
        {
            "name": "home-initial",
            "kind": "HOME",
            "profile": "step",
            "scopes": ["arm"],
            "params": {"verify_bus": True},
        },
        {
            "name": "pre-grasp",
            "kind": "JOINT_STEP",
            "profile": "step",
            "scopes": ["arm"],
            "params": {
                "joint_names": names,
                "target_positions": list(target["pre_grasp_joint_positions"]),
                "verify_bus": True,
                "duration_s": 1.0,
            },
        },
        {
            "name": "grasp",
            "kind": "GRIPPER",
            "profile": "gripper",
            "scopes": ["gripper"],
            "params": _gripper(0.35),
        },
        {
            "name": "transfer",
            "kind": "JOINT_STEP",
            "profile": "step",
            "scopes": ["arm"],
            "params": {
                "joint_names": names,
                "target_positions": list(target["place_joint_positions"]),
                "verify_bus": True,
                "duration_s": 1.0,
            },
        },
        {
            "name": "place",
            "kind": "GRIPPER",
            "profile": "gripper",
            "scopes": ["gripper"],
            "params": _gripper(2.0),
        },
        {
            "name": "home-return",
            "kind": "HOME",
            "profile": "step",
            "scopes": ["arm"],
            "params": {"verify_bus": True},
        },
    ]
    mapping = {
        "name": "ore-sorting-mock-task",
        "version": RECIPE_VERSION,
        "description": (
            f"Synthetic ore-sorting task for {scenario.scenario_id}@"
            f"{scenario.scenario_version}. Fixed mock poses; not IK and not a "
            "hardware or task-accuracy claim."
        ),
        "contract_hash": CONTRACT_HASH,
        "scopes": ["arm", "gripper"],
        "steps": steps,
    }
    try:
        return recipe_from_mapping(mapping, source="ore_sorting_mock_task")
    except Exception as error:  # RecipeError is a ValueError subclass
        _fail("PLAN", str(error))


def _scripted_outcomes(case: str) -> list[dict[str, Any]]:
    if case == "grasp-failure":
        return [
            completed(),
            completed(),
            completed(outcome="REJECTED", code="GRASP_FAILED", bus_verified=False),
        ]
    if case == "timeout":
        return [
            completed(),
            completed(outcome="TIMEOUT", code="STEP_TIMEOUT", bus_verified=False),
        ]
    if case == "stop-request":
        return [
            completed(),
            completed(outcome="ABORTED", code="STOP_REQUESTED", bus_verified=False),
        ]
    return []


def _authorization(recipe: Recipe, run_id: str, *, approved: bool) -> Authorization:
    return Authorization(
        operator=MOCK_OPERATOR,
        session_id=f"ore-mock-{run_id}",
        scopes=frozenset(recipe.scopes),
        approved=approved,
        approved_at=MOCK_APPROVED_AT,
    )


def _run_recipe(
    recipe: Recipe,
    *,
    run_id: str,
    outcomes: Sequence[Mapping[str, Any]],
    approved: bool,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="jcheng-ore-mock-store-") as store_dir:
        with MockGatewayServer() as server:
            server.probe.results.extend(dict(item) for item in outcomes)
            endpoint = GatewayEndpoint(
                base_url=f"http://127.0.0.1:{server.port}",
                token=MOCK_TOKEN,
                timeout_s=2.0,
            )
            gateway = HttpGateway(endpoint, operator_id=MOCK_OPERATOR)
            result = run_supervised(
                gateway,
                recipe,
                _authorization(recipe, run_id, approved=approved),
                run_id=run_id,
                store=RunStore(Path(store_dir) / "store"),
                recipe_hash=reference.content_hash(
                    {
                        "name": recipe.name,
                        "version": recipe.version,
                        "contract_hash": recipe.contract_hash,
                    }
                ),
                evidence_class="L2",
            )
            request_log = server.probe.request_log
    report = result.report
    return {
        "state": report.state.value,
        "abort_reason": report.abort_reason,
        "stop_confirmation": report.stop_confirmation,
        "physical_stop": report.physical_stop,
        "steps": [
            {
                "index": step.index,
                "name": step.name,
                "outcome": step.outcome,
                "reason_code": step.reason_code,
                "bus_verified": step.bus_verified,
            }
            for step in report.steps
        ],
        "request_counts": reference._request_counts(request_log),
        "lease": {
            "acquired": bool(result.lease.lease_id),
            "released": result.lease_released,
            "lost": result.lease_error is not None,
        },
    }


def _task_status(run: Mapping[str, Any] | None) -> str:
    if run is None:
        return ""
    steps = run.get("steps", [])
    last = steps[-1] if steps else None
    if run["state"] == "SUCCEEDED":
        return "SUCCEEDED"
    if run["state"] == "ABORTED" and "authorization" in str(run.get("abort_reason") or ""):
        return "AUTHORIZATION_FAILED"
    if last is not None and last.get("outcome") == "TIMEOUT":
        return "TIMEOUT"
    if last is not None and last.get("outcome") == "ABORTED":
        return "STOP_REQUESTED"
    if last is not None and last.get("outcome") == "REJECTED":
        return "GRASP_FAILED"
    return "RUN_FAILED"


def _artifact_hashes(devices: Mapping[str, Any], scenario: Any) -> dict[str, Any]:
    return {
        "devices": {
            role: {
                "source_device": device.source_device,
                "device_version": device.device_version,
                "source_hash": device.source_hash,
                "content_hash": device.content_hash,
            }
            for role, device in sorted(devices.items())
        },
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "scenario_version": scenario.scenario_version,
            "content_hash": scenario.content_hash,
            "scenario_content_hash": scenario.provenance.get(
                "scenario_content_hash"
            ),
        },
    }


def run_case(
    case: str,
    *,
    devices: Mapping[str, Any],
    scenario: Any,
    scenario_document: Mapping[str, Any],
) -> dict[str, Any]:
    if case not in CASES:
        _fail("USAGE", f"unknown case {case!r}")
    arm = devices.get("arm")
    if arm is None:
        _fail("CAPABILITY_UNMET", "the cell has no arm role")
    observation = synthetic_observation(case)
    base = {
        "schema": TASK_SCHEMA,
        "data_source": DATA_SOURCE,
        "task_id": TASK_ID,
        "task_version": TASK_VERSION,
        "case": case,
        "expected_status": EXPECTED_STATUS[case],
        "scenario_id": scenario.scenario_id,
        "scenario_version": scenario.scenario_version,
        "evaluation_id": scenario.evaluation_id,
        "cell_roles": sorted(devices),
        "observation": observation,
        "artifacts": _artifact_hashes(devices, scenario),
        "limitations": TASK_LIMITATIONS,
    }

    if case == "capability-unmet":
        modified = copy.deepcopy(dict(scenario_document))
        for item in modified.get("capabilities", []):
            if item.get("id") == "manipulation.arm.dof":
                item["threshold"] = {"op": ">=", "value": 9, "unit": "joints"}
        try:
            altered = reference.scenario_requirements_from_mapping(modified)
            reference.match_capabilities(devices, altered)
        except reference.ReferenceCheckError as error:
            return {
                **base,
                "status": "CAPABILITY_UNMET",
                "success": False,
                "failure_reason": str(error),
                "target": None,
                "placed": None,
                "run": None,
            }
        _fail("TASK", "the altered capability requirement unexpectedly matched")

    if case == "no-target":
        target = choose_grasp_target(observation, [j.name for j in arm.joints])
        assert target is None
        return {
            **base,
            "status": "NO_TARGET",
            "success": False,
            "failure_reason": "the synthetic observation declares no ore instance",
            "target": None,
            "placed": None,
            "run": None,
        }

    try:
        target = choose_grasp_target(observation, [j.name for j in arm.joints])
    except TaskError as error:
        return {
            **base,
            "status": "UNREACHABLE" if error.code == "NO_GRASP_TARGET" else "RUN_FAILED",
            "success": False,
            "failure_reason": str(error),
            "target": None,
            "placed": None,
            "run": None,
        }

    recipe = build_task_recipe(target, scenario)
    plan = build_plan(recipe, TASK_ID)
    outcomes = _scripted_outcomes(case)
    approved = case != "authorization-failure"
    try:
        run = _run_recipe(
            recipe,
            run_id=f"ore-mock-{case}",
            outcomes=outcomes,
            approved=approved,
        )
    except Exception as error:
        return {
            **base,
            "status": "RUN_FAILED",
            "success": False,
            "failure_reason": str(error),
            "target": target,
            "placed": None,
            "run": None,
        }
    status = _task_status(run)
    expected = EXPECTED_STATUS[case]
    if status != expected:
        status = "RUN_FAILED"
    success = status == "SUCCEEDED"
    placed = (
        {
            "object_id": target["object_id"],
            "class": target["class"],
            "bin": target["bin"],
            "data_source": DATA_SOURCE,
        }
        if success
        else None
    )
    failure_reason = None
    if not success:
        failure_reason = {
            "GRASP_FAILED": "the mock gripper step was rejected",
            "TIMEOUT": "a mock step timed out",
            "STOP_REQUESTED": "the mock motion was aborted with STOP_REQUESTED",
            "AUTHORIZATION_FAILED": "the operator authorization was refused",
        }.get(status, run.get("abort_reason") or f"task ended {status}")
    return {
        **base,
        "status": status,
        "success": success,
        "failure_reason": failure_reason,
        "target": target,
        "placed": placed,
        "plan": {
            "step_count": len(plan.steps),
            "order": [step.name for step in plan.steps],
        },
        "run": run,
    }


def _sample(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": f"ore-sorting-{result['case']}",
        "case": result["case"],
        "data_source": DATA_SOURCE,
        "data_kind": "mock_task",
        "task_id": TASK_ID,
        "scenario_id": result["scenario_id"],
        "scenario_version": result["scenario_version"],
        "evaluation_id": result["evaluation_id"],
        "status": result["status"],
        "success": bool(result["success"]),
        "placed_class": (result.get("placed") or {}).get("class"),
        "failure_reason": result.get("failure_reason"),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_samples(path: Path, samples: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(dict(sample), sort_keys=True) + "\n")


def _suite(kind: str, samples: int) -> dict[str, Any]:
    thresholds: list[dict[str, Any]] = []
    if kind == "test-pass":
        thresholds = [
            {"metric": "task_success_rate", "aggregate": "rate", "op": ">=", "value": 0.10}
        ]
    elif kind == "test-fail":
        thresholds = [
            {"metric": "task_success_rate", "aggregate": "rate", "op": ">=", "value": 0.99}
        ]
    return {
        "schema_version": 1,
        "suite": f"ore-sorting-mock-{kind}",
        "title": f"Ore sorting mock task — {kind}",
        "level": "L2",
        "data_source": DATA_SOURCE,
        "description": (
            "Eight synthetic mock-task cases. The observed suite declares no "
            "business threshold; test-pass/test-fail use explicitly labelled "
            "test thresholds to prove PASS/FAIL/INCONCLUSIVE discrimination."
        ),
        "min_samples": samples,
        "metrics": [
            {
                "id": "task_success_rate",
                "kind": "success_rate",
                "source": "success",
                "unit": "ratio",
            }
        ],
        "thresholds": thresholds,
        "limitations": [
            "Synthetic mock task outcomes; not a real ore-sorting accuracy claim.",
            "test-pass/test-fail thresholds are TEST thresholds, not business "
            "acceptance criteria and not derived from real data.",
            "No hardware, Gazebo runtime, perception model or recorded data is used.",
        ],
    }


def _run_benchmarks(
    benchmark_root: Path, out_dir: Path, samples_path: Path, count: int
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for kind in ("observed", "test-pass", "test-fail"):
        suite_dir = out_dir / f"benchmark-{kind}"
        suite_dir.mkdir(parents=True, exist_ok=True)
        results[kind] = reference.run_benchmark(
            benchmark_root,
            suite_dir,
            _suite(kind, count),
            samples_path,
            count,
        )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ore-sorting-mock-task",
        description=(
            "Minimal synthetic ore-sorting task through the existing mock "
            "gateway, lease and linear runner. No hardware or perception."
        ),
    )
    parser.add_argument(
        "--device-snapshot",
        action="append",
        required=True,
        metavar="[ROLE=]PATH",
        help="arm/gripper/camera snapshots; repeat with ROLE=PATH",
    )
    parser.add_argument("--scenario-requirements", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--case",
        choices=("all", *CASES),
        default="all",
    )
    parser.add_argument("--benchmark-root")
    parser.add_argument(
        "--repository-sha",
        action="append",
        default=[],
        metavar="NAME=SHA",
        help="optional fixed repository revision recorded in the summary",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        device_or_devices, _cell = reference._load_devices(args.device_snapshot)
        if isinstance(device_or_devices, reference.DeviceSnapshot):
            _fail("USAGE", "the mock task needs ROLE=PATH cell snapshots")
        devices = dict(device_or_devices)
        scenario_document = reference.load_document(
            Path(args.scenario_requirements)
        )
        scenario = reference.scenario_requirements_from_mapping(scenario_document)
        match = reference.match_capabilities(devices, scenario)
        if match.status not in {"matched", "matched_with_unverified"}:
            _fail("CAPABILITY_UNMET", f"capability match is {match.status}")

        selected = CASES if args.case == "all" else (args.case,)
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        results = {
            case: run_case(
                case,
                devices=devices,
                scenario=scenario,
                scenario_document=scenario_document,
            )
            for case in selected
        }
        for case, result in results.items():
            _write_json(out_dir / f"case-{case}.json", result)
        samples = [_sample(result) for result in results.values()]
        samples_path = out_dir / "samples.jsonl"
        _write_samples(samples_path, samples)

        mismatches = [
            case
            for case, result in results.items()
            if result["status"] != EXPECTED_STATUS[case]
        ]
        repository_shas: dict[str, str] = {}
        for spec in args.repository_sha:
            if "=" not in spec:
                _fail("USAGE", f"--repository-sha needs NAME=SHA, got {spec!r}")
            name, sha = spec.split("=", 1)
            if not name or not sha:
                _fail("USAGE", f"--repository-sha needs NAME=SHA, got {spec!r}")
            repository_shas[name] = sha
        summary: dict[str, Any] = {
            "schema": "jcheng.applications.ore_sorting_summary.v1",
            "data_source": DATA_SOURCE,
            "task_id": TASK_ID,
            "scenario_requirements": {
                "scenario_id": scenario.scenario_id,
                "scenario_version": scenario.scenario_version,
                "evaluation_id": scenario.evaluation_id,
                "content_hash": scenario.content_hash,
                "scenario_content_hash": scenario.provenance.get(
                    "scenario_content_hash"
                ),
            },
            "repository_shas": dict(sorted(repository_shas.items())),
            "cases": {
                case: {
                    "status": result["status"],
                    "success": result["success"],
                    "expected_status": EXPECTED_STATUS[case],
                }
                for case, result in results.items()
            },
            "mismatches": mismatches,
            "artifact_valid": True,
            "capability_match_status": match.status,
            "capability_satisfied": match.status == "matched",
            "offline_execution_allowed": match.status
            in {"matched", "matched_with_unverified"},
            "deployment_ready": False,
            "motion_authorized": False,
            "limitations": TASK_LIMITATIONS,
        }
        if args.benchmark_root:
            summary["benchmark"] = _run_benchmarks(
                Path(args.benchmark_root).resolve(),
                out_dir,
                samples_path,
                len(samples),
            )
        _write_json(out_dir / "task_summary.json", summary)
    except (TaskError, reference.ReferenceCheckError) as error:
        print(f"ore-sorting-mock-task: error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"ore-sorting-mock-task: error: {error}", file=sys.stderr)
        return 2

    for case, result in results.items():
        print(f"{case}: {result['status']}")
    if mismatches:
        print(
            "ore-sorting-mock-task: unexpected case classification: "
            + ", ".join(mismatches),
            file=sys.stderr,
        )
        return 1
    if args.benchmark_root:
        statuses = {
            kind: result.get("status")
            for kind, result in summary["benchmark"].items()
        }
        if statuses != {
            "observed": "inconclusive",
            "test-pass": "pass",
            "test-fail": "fail",
        }:
            print(
                "ore-sorting-mock-task: benchmark discrimination check failed: "
                f"{statuses}",
                file=sys.stderr,
            )
            return 1
    print(f"summary: {out_dir / 'task_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
