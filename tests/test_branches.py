"""S3 declared branches: validation, plan keys, three-valued selection, runner."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jcheng_applications.gateway import (
    ContractInfo,
    GatewayProtocolError,
    MotionResult,
    SafetySnapshot,
    motion_result_from_mapping,
)
from jcheng_applications.model import (
    CONTRACT_HASH,
    Authorization,
    RecipeError,
    build_plan,
    load_recipe,
    recipe_from_mapping,
)
from jcheng_applications.runner import RunState, TaskRunner

ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATH = ROOT / "recipes" / "example-declared-branch.yaml"
RECIPE = load_recipe(RECIPE_PATH)
NOW = "2026-11-26T00:00:00+00:00"


def _raw() -> dict:
    return yaml.safe_load(RECIPE_PATH.read_text(encoding="utf-8"))


def _auth() -> Authorization:
    return Authorization(
        operator="operator",
        session_id="s3-session",
        scopes=frozenset({"arm"}),
        approved=True,
        approved_at=NOW,
    )


def _contract() -> ContractInfo:
    return ContractInfo(
        major=0,
        minor=2,
        patch=0,
        contract_hash=CONTRACT_HASH,
        profile_hash="profile-s3",
        system_version="0.1.0",
    )


def _safety() -> SafetySnapshot:
    return SafetySnapshot(
        state="ARMED_IDLE", stop_latched=False, seq=1, last_valid_state_at=NOW
    )


def _result(
    outcome: str = "COMPLETED",
    *,
    code: str = "OK",
    bus_verified: bool = True,
    measured: list[float] | tuple[float, ...] | None = None,
) -> MotionResult:
    return MotionResult(
        intent_id="intent",
        outcome=outcome,
        reason_code=code,
        message="",
        bus_verified=bus_verified,
        safety_state="ARMED_IDLE",
        stop_latched=False,
        measured_positions=(
            None if measured is None else tuple(float(item) for item in measured)
        ),
    )


class BranchGateway:
    def __init__(self, results: list[MotionResult]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []
        self.stops: list[str] = []

    def contract(self) -> ContractInfo:
        return _contract()

    def safety(self) -> SafetySnapshot:
        return _safety()

    def lease_id(self) -> str:
        return "lease-s3"

    def submit(self, command: dict) -> MotionResult:
        self.calls.append(dict(command))
        if not self.results:
            raise RuntimeError("script exhausted")
        return self.results.pop(0)

    def stop(self, reason: str) -> None:
        self.stops.append(reason)


def _run(gateway: BranchGateway, recipe=RECIPE):
    return TaskRunner(gateway).run(recipe, _auth(), run_id="run-1")


# ---- recipe validation ----


def test_recipe_loads_facts_and_branches() -> None:
    assert set(RECIPE.facts) == {"joint1_moved"}
    assert RECIPE.facts["joint1_moved"].index == 0
    assert len(RECIPE.branches) == 1
    assert RECIPE.branches[0].id == "adjust-j1.v1"
    assert RECIPE.branches[0].after == "approach-j1"


def test_v01_recipe_still_loads_without_extensions() -> None:
    recipe = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
    assert recipe.facts == {}
    assert recipe.branches == ()


def test_scope_union_includes_branch_steps() -> None:
    raw = _raw()
    raw["branches"][0]["steps"][0]["scopes"] = ["full"]
    with pytest.raises(RecipeError, match="must equal the union"):
        recipe_from_mapping(raw)


def test_fact_source_must_be_a_main_step() -> None:
    raw = _raw()
    raw["facts"]["joint1_moved"]["from_step"] = "adjust-j1"
    with pytest.raises(RecipeError, match="main step"):
        recipe_from_mapping(raw)


def test_fact_source_must_not_be_after_the_anchor() -> None:
    raw = _raw()
    raw["steps"].append(
        {
            "name": "after-anchor",
            "kind": "JOINT_STEP",
            "profile": "step",
            "scopes": ["arm"],
            "params": {
                "joint_names": ["joint1"],
                "target_positions": [0.04],
                "max_velocity_rad_s": 0.4,
                "duration_s": 1.0,
                "verify_bus": True,
            },
        }
    )
    raw["facts"]["joint1_moved"]["from_step"] = "after-anchor"
    with pytest.raises(RecipeError, match="after the anchor"):
        recipe_from_mapping(raw)


def test_branch_condition_requires_a_declared_fact() -> None:
    raw = _raw()
    raw["branches"][0]["when"][0]["fact"] = "missing"
    with pytest.raises(RecipeError, match="unknown fact"):
        recipe_from_mapping(raw)


def test_fact_requires_a_threshold() -> None:
    raw = _raw()
    del raw["facts"]["joint1_moved"]["measured"]["at_least_rad"]
    with pytest.raises(RecipeError, match="requires at_least_rad"):
        recipe_from_mapping(raw)


def test_branch_step_name_must_be_globally_unique() -> None:
    raw = _raw()
    raw["branches"][0]["steps"][0]["name"] = "approach-j1"
    with pytest.raises(RecipeError, match="unique"):
        recipe_from_mapping(raw)


def test_branch_required_and_priority_validation() -> None:
    raw = _raw()
    raw["branches"][0]["required"] = "yes"
    with pytest.raises(RecipeError):
        recipe_from_mapping(raw)
    raw = _raw()
    raw["branches"][0]["priority"] = 101
    with pytest.raises(RecipeError):
        recipe_from_mapping(raw)


# ---- plan ----


def test_branch_steps_have_their_own_deterministic_keys() -> None:
    plan = build_plan(RECIPE, "run-1")
    branch = plan.branches[0]
    assert branch.steps[0].branch_id == "adjust-j1.v1"
    assert branch.steps[0].branch_index == 0
    assert branch.steps[0].idempotency_key != plan.steps[0].idempotency_key
    assert branch.steps[0].index == 1
    assert plan.facts["joint1_moved"].index == 0

    again = build_plan(RECIPE, "run-1")
    assert again.branches[0].steps[0].idempotency_key == branch.steps[0].idempotency_key
    other = build_plan(RECIPE, "run-2")
    assert other.branches[0].steps[0].idempotency_key != branch.steps[0].idempotency_key


# ---- runner ----


def test_branch_selected_when_fact_is_true() -> None:
    gateway = BranchGateway([_result(measured=[0.03]), _result(measured=[0.05])])
    report = _run(gateway)
    assert report.state is RunState.SUCCEEDED
    assert [step.name for step in report.steps] == ["approach-j1", "adjust-j1"]
    assert len(report.selections) == 1
    assert report.selections[0].selected == "adjust-j1.v1"
    assert gateway.calls[1]["target_positions"] == [0.05]
    assert gateway.calls[1]["idempotency_key"] != gateway.calls[0]["idempotency_key"]


def test_branch_not_selected_when_fact_is_false() -> None:
    gateway = BranchGateway([_result(measured=[0.0])])
    report = _run(gateway)
    assert report.state is RunState.SUCCEEDED
    assert [step.name for step in report.steps] == ["approach-j1"]
    assert report.selections[0].selected is None
    assert report.selections[0].candidates[0].guard == "FAIL"
    assert len(gateway.calls) == 1


def test_fact_is_unknown_without_measured_positions() -> None:
    gateway = BranchGateway([_result(measured=None)])
    report = _run(gateway)
    assert report.state is RunState.SUCCEEDED
    assert report.selections[0].selected is None
    assert report.selections[0].candidates[0].guard == "UNKNOWN"


def test_execution_failure_does_not_evaluate_a_branch() -> None:
    gateway = BranchGateway([_result("REJECTED", code="X", bus_verified=False)])
    report = _run(gateway)
    assert report.state is RunState.FAILED
    assert report.selections == ()
    assert len(gateway.calls) == 1


def test_required_branch_without_candidate_aborts() -> None:
    raw = _raw()
    raw["branches"][0]["required"] = True
    recipe = recipe_from_mapping(raw)
    gateway = BranchGateway([_result(measured=[0.0])])
    report = _run(gateway, recipe)
    assert report.state is RunState.ABORTED
    assert "required branch" in (report.abort_reason or "")
    assert gateway.stops, "the run must stop once"
    assert len(gateway.calls) == 1


def test_branch_step_failure_stops_the_run() -> None:
    gateway = BranchGateway(
        [_result(measured=[0.03]), _result("REJECTED", code="X", bus_verified=False)]
    )
    report = _run(gateway)
    assert report.state is RunState.FAILED
    assert [step.outcome for step in report.steps] == ["COMPLETED", "REJECTED"]
    assert gateway.stops, "the run must stop once"


# ---- measured positions parser ----


def test_measured_positions_are_parsed_strictly() -> None:
    parsed = motion_result_from_mapping(
        {
            "intent_id": "i",
            "outcome": "COMPLETED",
            "reason_code": "OK",
            "message": "",
            "bus_verified": True,
            "measured_positions": [0.0, -0.1, 0.2],
        }
    )
    assert parsed.measured_positions == (0.0, -0.1, 0.2)
    with pytest.raises(GatewayProtocolError):
        motion_result_from_mapping(
            {
                "intent_id": "i",
                "outcome": "COMPLETED",
                "reason_code": "OK",
                "message": "",
                "bus_verified": True,
                "measured_positions": ["bad"],
            }
        )
