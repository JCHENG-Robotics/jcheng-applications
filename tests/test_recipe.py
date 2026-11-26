"""Tests for the recipe model and the deterministic planner."""

from __future__ import annotations

from pathlib import Path

import pytest

from jcheng_applications.model import (
    CONTRACT_HASH,
    MAX_STEPS,
    Authorization,
    RecipeError,
    build_plan,
    load_recipe,
    recipe_from_text,
)

ROOT = Path(__file__).resolve().parents[1]

VALID = f"""
name: test-recipe
version: 0.1.0
description: A test recipe.
contract_hash: "{CONTRACT_HASH}"
scopes: [arm, gripper]
steps:
  - name: approach
    kind: JOINT_STEP
    profile: step
    scopes: [arm]
    params:
      joint_names: [joint1, joint2]
      target_positions: [0.0, 0.1]
      verify_bus: true
  - name: grasp
    kind: GRIPPER
    profile: gripper
    scopes: [gripper]
    params:
      gripper:
        position_rad: 0.3
        max_velocity_rad_s: 0.5
        duration_s: 1.0
        verify_bus: true
"""


def test_example_recipe_loads() -> None:
    recipe = load_recipe(ROOT / "recipes" / "example-supervised-transfer.yaml")
    assert recipe.name == "example-supervised-transfer"
    assert recipe.version == "0.1.0"
    assert recipe.contract_hash == CONTRACT_HASH
    assert recipe.scopes == ("arm", "gripper")
    assert [step.name for step in recipe.steps] == ["approach", "grasp"]


def test_valid_recipe_from_text() -> None:
    recipe = recipe_from_text(VALID)
    assert recipe.name == "test-recipe"
    assert recipe.steps[0].kind == "JOINT_STEP"
    assert recipe.steps[1].kind == "GRIPPER"


def test_contract_hash_must_match_the_pin() -> None:
    with pytest.raises(RecipeError, match="contract hash"):
        recipe_from_text(VALID.replace(CONTRACT_HASH, "sha256:" + "0" * 64))


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(RecipeError, match="unknown field"):
        recipe_from_text(VALID + "\nextra: true\n")


def test_unknown_step_field_is_rejected() -> None:
    with pytest.raises(RecipeError, match="unknown field"):
        recipe_from_text(VALID.replace("    scopes: [arm]", "    scopes: [arm]\n    note: hi"))


def test_unknown_kind_and_profile_are_rejected() -> None:
    with pytest.raises(RecipeError, match="kind"):
        recipe_from_text(VALID.replace("kind: JOINT_STEP", "kind: SPIN"))
    with pytest.raises(RecipeError, match="profile"):
        recipe_from_text(VALID.replace("profile: step", "profile: turbo"))


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(RecipeError, match="unknown scope"):
        recipe_from_text(VALID.replace("scopes: [arm, gripper]", "scopes: [arm, wheels]"))


def test_duplicate_step_names_are_rejected() -> None:
    with pytest.raises(RecipeError, match="unique"):
        recipe_from_text(VALID.replace("name: grasp", "name: approach"))


def test_recipe_scopes_must_equal_the_step_union() -> None:
    with pytest.raises(RecipeError, match="union"):
        recipe_from_text(VALID.replace("scopes: [arm, gripper]", "scopes: [arm]"))


def test_full_scope_cannot_be_combined() -> None:
    with pytest.raises(RecipeError, match="full"):
        recipe_from_text(
            VALID.replace("scopes: [arm, gripper]", "scopes: [arm, full]")
            .replace("scopes: [arm]", "scopes: [full]")
            .replace("scopes: [gripper]", "scopes: [full]")
        )


def test_missing_kind_payload_is_rejected() -> None:
    with pytest.raises(RecipeError, match="joint_names"):
        recipe_from_text(
            VALID.replace(
                "      joint_names: [joint1, joint2]\n"
                "      target_positions: [0.0, 0.1]\n",
                "      target_positions: [0.0, 0.1]\n",
            )
        )


def test_joint_name_and_position_counts_must_match() -> None:
    with pytest.raises(RecipeError, match="target_positions"):
        recipe_from_text(
            VALID.replace(
                "      target_positions: [0.0, 0.1]",
                "      target_positions: [0.0]",
            )
        )


def test_arm_step_requires_verify_bus() -> None:
    with pytest.raises(RecipeError, match="verify_bus"):
        recipe_from_text(VALID.replace("      verify_bus: true\n", "", 1))


def test_gripper_step_requires_nested_verify_bus() -> None:
    with pytest.raises(RecipeError, match="verify_bus"):
        recipe_from_text(VALID.replace("        verify_bus: true", "        verify_bus: false"))


def test_step_scope_must_match_the_command() -> None:
    with pytest.raises(RecipeError, match="JOINT_STEP requires scope arm"):
        recipe_from_text(
            VALID.replace("scopes: [arm]", "scopes: [gripper]", 1)
            .replace("scopes: [arm, gripper]", "scopes: [gripper]")
        )
    with pytest.raises(RecipeError, match="GRIPPER requires scope gripper"):
        recipe_from_text(
            VALID.replace("scopes: [gripper]", "scopes: [arm]")
            .replace("scopes: [arm, gripper]", "scopes: [arm]")
        )


def test_full_scope_is_allowed_for_arm_and_base_steps() -> None:
    full = recipe_from_text(
        f"""
name: full-recipe
version: 0.1.0
description: Full scope.
contract_hash: "{CONTRACT_HASH}"
scopes: [full]
steps:
  - name: home
    kind: HOME
    profile: step
    scopes: [full]
    params:
      verify_bus: true
"""
    )
    assert full.scopes == ("full",)

    base = recipe_from_text(
        f"""
name: base-recipe
version: 0.1.0
description: Base scope.
contract_hash: "{CONTRACT_HASH}"
scopes: [base]
steps:
  - name: nudge
    kind: BASE_PULSE
    profile: base
    scopes: [base]
    params:
      base_pulse:
        duty: [0.1, 0.1, 0.1, 0.1]
        duration_s: 0.2
        require_stopped_after: true
"""
    )
    assert base.steps[0].kind == "BASE_PULSE"


def test_non_finite_values_are_rejected() -> None:
    with pytest.raises(RecipeError, match="finite"):
        recipe_from_text(
            VALID.replace(
                "      target_positions: [0.0, 0.1]",
                "      target_positions: [0.0, .nan]",
            )
        )


def test_unknown_parameter_is_rejected() -> None:
    with pytest.raises(RecipeError, match="unknown parameter"):
        recipe_from_text(
            VALID.replace(
                "      verify_bus: true",
                "      verify_bus: true\n      speed: 9",
                1,
            )
        )


def test_invalid_yaml_and_empty_documents_are_rejected() -> None:
    with pytest.raises(RecipeError, match="invalid YAML"):
        recipe_from_text("name: [unclosed")
    with pytest.raises(RecipeError, match="empty"):
        recipe_from_text("")


def test_path_and_version_are_validated() -> None:
    with pytest.raises(RecipeError, match="version"):
        recipe_from_text(VALID.replace("version: 0.1.0", "version: one"))
    with pytest.raises(RecipeError, match="name"):
        recipe_from_text(VALID.replace("name: test-recipe", "name: Test Recipe"))


def test_too_many_steps_are_rejected() -> None:
    step = (
        "  - name: step-{index}\n"
        "    kind: HOME\n"
        "    profile: step\n"
        "    scopes: [arm]\n"
        "    params:\n"
        "      verify_bus: true\n"
    )
    steps = "".join(step.format(index=index) for index in range(MAX_STEPS + 1))
    text = (
        f"name: too-long\nversion: 0.1.0\ndescription: too long\n"
        f'contract_hash: "{CONTRACT_HASH}"\nscopes: [arm]\nsteps:\n{steps}'
    )
    with pytest.raises(RecipeError, match="1 to"):
        recipe_from_text(text)


def test_missing_recipe_file_is_a_recipe_error(tmp_path: Path) -> None:
    with pytest.raises(RecipeError, match="cannot read"):
        load_recipe(tmp_path / "absent.yaml")


def test_plan_is_deterministic_and_run_bound() -> None:
    recipe = recipe_from_text(VALID)
    first = build_plan(recipe, "run-001")
    second = build_plan(recipe, "run-001")
    other = build_plan(recipe, "run-002")

    assert [step.idempotency_key for step in first.steps] == [
        step.idempotency_key for step in second.steps
    ]
    assert [step.idempotency_key for step in first.steps] != [
        step.idempotency_key for step in other.steps
    ]
    assert len({step.idempotency_key for step in first.steps}) == len(first.steps)
    assert all(len(step.idempotency_key) == 64 for step in first.steps)


def test_plan_key_changes_with_payload() -> None:
    recipe = recipe_from_text(VALID)
    changed = recipe_from_text(VALID.replace("[0.0, 0.1]", "[0.0, 0.2]"))
    assert (
        build_plan(recipe, "run-001").steps[0].idempotency_key
        != build_plan(changed, "run-001").steps[0].idempotency_key
    )


def test_invalid_run_id_is_rejected() -> None:
    recipe = recipe_from_text(VALID)
    for run_id in ("", " space", "bad/slash", "x" * 129):
        with pytest.raises(RecipeError, match="run_id"):
            build_plan(recipe, run_id)


def test_authorization_covers_only_approved_scopes() -> None:
    approved = Authorization(
        operator="operator",
        session_id="session-1",
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at="2026-11-26T10:00:00+08:00",
    )
    assert approved.covers({"arm"})
    assert not approved.covers({"base"})
    unapproved = Authorization(
        operator="operator",
        session_id="session-1",
        scopes=frozenset({"arm", "gripper"}),
        approved=False,
        approved_at="2026-11-26T10:00:00+08:00",
    )
    assert not unapproved.covers({"arm"})
