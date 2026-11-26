"""Recipe model, contract constants and deterministic plan construction.

This module is ROS-free and transport-free. It validates a recipe against the
v0.1.0 gateway contract and turns it into a plan whose idempotency keys are
stable for a given run, step and payload. Nothing here opens a device, joins a
DDS graph or sends a command.

The contract constants mirror ``jcheng-interfaces`` v0.1.0. A contract change
requires a new interfaces tag and a matching update here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml

CONTRACT_HASH = (
    "sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42"
)
INTERFACES_RANGE = ">=0.1,<0.2"
SYSTEM_API_RANGE = ">=0.1,<0.2"
CONTRACT_VERSION: dict[str, Any] = {
    "major": 0,
    "minor": 1,
    "patch": 0,
    "contract_hash": CONTRACT_HASH,
}

KINDS = frozenset(
    {
        "JOINT_TRAJECTORY",
        "JOINT_STEP",
        "POSE",
        "HOME",
        "BASE_PULSE",
        "GRIPPER",
    }
)
PROFILES = frozenset({"moveit", "step", "pose", "fast", "base", "gripper"})
SCOPES = frozenset({"arm", "gripper", "base", "full"})
MOTION_SCOPES = frozenset({"arm", "gripper", "full"})
SAFETY_STATES = frozenset(
    {"DISARMED", "ARMED_IDLE", "MOTION_ACTIVE", "STOP_LATCHED", "FAULT"}
)
MOTION_OUTCOMES = frozenset(
    {"COMPLETED", "REJECTED", "ABORTED", "TIMEOUT", "UNKNOWN"}
)

MAX_STEPS = 64

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")

_COMMON_PARAMS = frozenset(
    {
        "max_velocity_rad_s",
        "max_acceleration_rad_s",
        "duration_s",
        "verify_bus",
        "require_measured_state",
        "expected_profile_hash",
    }
)
_KIND_PARAMS = {
    "JOINT_TRAJECTORY": frozenset({"trajectory"}),
    "JOINT_STEP": frozenset({"joint_names", "target_positions"}),
    "POSE": frozenset({"target_pose", "tip_link", "frame_id"}),
    "HOME": frozenset(),
    "BASE_PULSE": frozenset({"base_pulse"}),
    "GRIPPER": frozenset({"gripper"}),
}
_REQUIRED_KIND_PARAMS = {
    "JOINT_TRAJECTORY": frozenset({"trajectory"}),
    "JOINT_STEP": frozenset({"joint_names", "target_positions"}),
    "POSE": frozenset({"target_pose", "tip_link", "frame_id"}),
    "HOME": frozenset(),
    "BASE_PULSE": frozenset({"base_pulse"}),
    "GRIPPER": frozenset({"gripper"}),
}

_TOP_LEVEL_KEYS = frozenset(
    {
        "name",
        "version",
        "description",
        "contract_hash",
        "scopes",
        "steps",
        "facts",
        "branches",
    }
)
_REQUIRED_TOP_LEVEL_KEYS = frozenset(
    {"name", "version", "description", "contract_hash", "scopes", "steps"}
)
_STEP_KEYS = frozenset({"name", "kind", "profile", "scopes", "params"})
_FACT_KEYS = frozenset({"from_step", "measured"})
_MEASURED_KEYS = frozenset({"index", "at_least_rad", "at_most_rad"})
_BRANCH_KEYS = frozenset({"id", "after", "required", "priority", "when", "steps"})
_CONDITION_KEYS = frozenset({"fact", "equals"})
_BRANCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")


class RecipeError(ValueError):
    """A recipe does not satisfy the documented recipe contract."""


@dataclass(frozen=True)
class Step:
    """One command-bearing step of a recipe."""

    name: str
    kind: str
    profile: str
    scopes: tuple[str, ...]
    params: Mapping[str, Any]


@dataclass(frozen=True)
class FactSpec:
    """A measured-position fact declared by a recipe.

    v0 facts use a positional index into ``MotionResult.measured_positions``;
    the gateway contract does not carry joint names or a gripper measurement.
    """

    name: str
    from_step: str
    index: int
    at_least_rad: float | None
    at_most_rad: float | None


@dataclass(frozen=True)
class BranchSpec:
    """A declared recovery branch anchored after a main step."""

    id: str
    after: str
    required: bool
    priority: int
    conditions: tuple[tuple[str, bool], ...]
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Recipe:
    """A validated, offline task description."""

    name: str
    version: str
    description: str
    contract_hash: str
    scopes: tuple[str, ...]
    steps: tuple[Step, ...]
    facts: Mapping[str, FactSpec] = field(default_factory=dict)
    branches: tuple[BranchSpec, ...] = ()


@dataclass(frozen=True)
class Authorization:
    """A per-session operator authorization record.

    The record is supplied by the caller; the core never infers, invents or
    extends it. ``approved_at`` must be an ISO 8601 timestamp with a timezone.
    """

    operator: str
    session_id: str
    scopes: frozenset[str]
    approved: bool
    approved_at: str

    def covers(self, scopes: Iterable[str]) -> bool:
        """Return whether this approved record covers ``scopes``."""

        return self.approved is True and set(scopes) <= set(self.scopes)


@dataclass(frozen=True)
class PlanStep:
    """A step bound to a run, an index and an idempotency key."""

    index: int
    name: str
    kind: str
    profile: str
    scopes: tuple[str, ...]
    params: Mapping[str, Any]
    idempotency_key: str
    branch_id: str | None = None
    branch_index: int | None = None


@dataclass(frozen=True)
class PlanFact:
    """A compiled fact projection over a main step's measured positions."""

    name: str
    from_step: str
    index: int
    at_least_rad: float | None
    at_most_rad: float | None


@dataclass(frozen=True)
class PlanBranch:
    """A compiled declared branch with its own step keys."""

    id: str
    after: str
    required: bool
    priority: int
    conditions: tuple[tuple[str, bool], ...]
    steps: tuple[PlanStep, ...]


@dataclass(frozen=True)
class Plan:
    """The deterministic output of planning; no I/O and no robot contact."""

    run_id: str
    recipe_name: str
    recipe_version: str
    contract_hash: str
    required_scopes: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    facts: Mapping[str, PlanFact] = field(default_factory=dict)
    branches: tuple[PlanBranch, ...] = ()


def canonical_json(value: Any) -> str:
    """Return a stable JSON encoding used for hashing."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fail(where: str, message: str) -> None:
    raise RecipeError(f"{where}: {message}")


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(where, "must be a mapping")
    for key in value:
        if not isinstance(key, str):
            _fail(where, f"key {key!r} is not a string")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], where: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        _fail(where, f"missing field(s): {', '.join(missing)}")
    if extra:
        _fail(where, f"unknown field(s): {', '.join(extra)}")


def _text(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(where, "must be a string")
    if not allow_empty and not value.strip():
        _fail(where, "must not be empty")
    return value


def _name(value: Any, where: str) -> str:
    text = _text(value, where)
    if not _NAME_RE.fullmatch(text):
        _fail(where, "must match ^[a-z0-9][a-z0-9-]{0,63}$")
    return text


_AUTHORIZATION_FIELDS = frozenset(
    {"operator", "session_id", "scopes", "approved", "approved_at"}
)


def authorization_from_mapping(value: Any) -> Authorization:
    """Parse an operator-supplied ``Authorization`` record.

    The record is data, like a recipe: unknown fields are rejected and the
    scopes must be known. Whether the record is approved and covers the recipe
    is decided later by the preflight, not by the parser.
    """

    root = _mapping(value, "authorization")
    _exact_keys(root, _AUTHORIZATION_FIELDS, "authorization")
    operator = _text(root["operator"], "authorization.operator")
    session_id = _text(root["session_id"], "authorization.session_id")
    raw_scopes = root["scopes"]
    if not isinstance(raw_scopes, list) or not raw_scopes:
        _fail("authorization.scopes", "must be a non-empty list")
    scopes: list[str] = []
    for index, scope in enumerate(raw_scopes):
        if not isinstance(scope, str) or scope not in SCOPES:
            _fail(f"authorization.scopes[{index}]", f"unknown scope {scope!r}")
        if scope in scopes:
            _fail("authorization.scopes", f"duplicate scope {scope!r}")
        scopes.append(scope)
    approved = root["approved"]
    if not isinstance(approved, bool):
        _fail("authorization.approved", "must be a boolean")
    approved_at = _text(root["approved_at"], "authorization.approved_at")
    return Authorization(
        operator=operator,
        session_id=session_id,
        scopes=frozenset(scopes),
        approved=approved,
        approved_at=approved_at,
    )


def _finite_number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(where, "must be a number")
    number = float(value)
    if not math.isfinite(number):
        _fail(where, "must be finite")
    return number


def _number_list(value: Any, where: str, *, minimum_length: int = 1) -> list[float]:
    if not isinstance(value, list):
        _fail(where, "must be a list")
    if len(value) < minimum_length:
        _fail(where, f"must contain at least {minimum_length} value(s)")
    return [_finite_number(item, f"{where}[{index}]") for index, item in enumerate(value)]


def _string_list(value: Any, where: str, *, minimum_length: int = 1) -> list[str]:
    if not isinstance(value, list):
        _fail(where, "must be a list")
    if len(value) < minimum_length:
        _fail(where, f"must contain at least {minimum_length} value(s)")
    result = []
    for index, item in enumerate(value):
        text = _text(item, f"{where}[{index}]")
        result.append(text)
    return result


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        _fail(where, "must be a boolean")
    return value


def _positive(value: Any, where: str) -> float:
    number = _finite_number(value, where)
    if number <= 0:
        _fail(where, "must be greater than zero")
    return number


def _check_finite_tree(value: Any, where: str) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            _fail(where, "must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_finite_tree(item, f"{where}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _check_finite_tree(item, f"{where}.{key}")
        return
    _fail(where, f"unsupported value type {type(value).__name__}")


def _check_trajectory(value: Any, where: str) -> None:
    trajectory = _mapping(value, where)
    allowed = {"header", "joint_names", "points"}
    unknown = set(trajectory) - allowed
    if unknown:
        _fail(where, f"unknown field(s): {', '.join(sorted(unknown))}")
    if "joint_names" not in trajectory or "points" not in trajectory:
        _fail(where, "requires joint_names and points")
    names = _string_list(trajectory["joint_names"], f"{where}.joint_names")
    points = trajectory["points"]
    if not isinstance(points, list) or not points:
        _fail(f"{where}.points", "must be a non-empty list")
    for index, point in enumerate(points):
        point_where = f"{where}.points[{index}]"
        point_map = _mapping(point, point_where)
        point_unknown = set(point_map) - {
            "positions",
            "velocities",
            "accelerations",
            "time_from_start",
        }
        if point_unknown:
            _fail(point_where, f"unknown field(s): {', '.join(sorted(point_unknown))}")
        for field in ("positions", "velocities", "accelerations"):
            if field not in point_map:
                _fail(point_where, f"missing field {field}")
            values = _number_list(point_map[field], f"{point_where}.{field}", minimum_length=0)
            if len(values) != len(names):
                _fail(
                    f"{point_where}.{field}",
                    f"has {len(values)} value(s) for {len(names)} joint(s)",
                )
        if "time_from_start" not in point_map:
            _fail(point_where, "missing field time_from_start")
        time_from_start = _finite_number(
            point_map["time_from_start"], f"{point_where}.time_from_start"
        )
        if time_from_start < 0:
            _fail(f"{point_where}.time_from_start", "must not be negative")


def _check_pose(value: Any, where: str) -> None:
    pose = _mapping(value, where)
    unknown = set(pose) - {"position", "orientation"}
    if unknown:
        _fail(where, f"unknown field(s): {', '.join(sorted(unknown))}")
    for field, members in (
        ("position", {"x", "y", "z"}),
        ("orientation", {"x", "y", "z", "w"}),
    ):
        if field not in pose:
            _fail(where, f"missing field {field}")
        part = _mapping(pose[field], f"{where}.{field}")
        part_unknown = set(part) - members
        if part_unknown:
            _fail(
                f"{where}.{field}",
                f"unknown field(s): {', '.join(sorted(part_unknown))}",
            )
        for member in members:
            if member not in part:
                _fail(f"{where}.{field}", f"missing field {member}")
            _finite_number(part[member], f"{where}.{field}.{member}")


def _check_gripper(value: Any, where: str) -> None:
    gripper = _mapping(value, where)
    expected = {
        "position_rad",
        "max_velocity_rad_s",
        "duration_s",
        "verify_bus",
    }
    _exact_keys(gripper, frozenset(expected), where)
    position = _finite_number(gripper["position_rad"], f"{where}.position_rad")
    if not 0.0 <= position <= 2.22:
        _fail(f"{where}.position_rad", "must be within [0, 2.22] rad")
    _positive(gripper["max_velocity_rad_s"], f"{where}.max_velocity_rad_s")
    _positive(gripper["duration_s"], f"{where}.duration_s")
    _bool(gripper["verify_bus"], f"{where}.verify_bus")


def _check_base_pulse(value: Any, where: str) -> None:
    pulse = _mapping(value, where)
    expected = {"duty", "duration_s", "require_stopped_after"}
    _exact_keys(pulse, frozenset(expected), where)
    duties = _number_list(pulse["duty"], f"{where}.duty", minimum_length=1)
    if len(duties) > 4:
        _fail(f"{where}.duty", "must contain at most four wheel values")
    for index, duty in enumerate(duties):
        if not -1.0 <= duty <= 1.0:
            _fail(f"{where}.duty[{index}]", "must be within [-1, 1]")
    _positive(pulse["duration_s"], f"{where}.duration_s")
    _bool(pulse["require_stopped_after"], f"{where}.require_stopped_after")


def _check_scope_semantics(kind: str, scopes: tuple[str, ...], where: str) -> None:
    """Keep a step's lease scope consistent with the command it carries."""

    scope_set = set(scopes)
    if "full" in scope_set:
        return
    expected = {
        "JOINT_TRAJECTORY": {"arm"},
        "JOINT_STEP": {"arm"},
        "POSE": {"arm"},
        "HOME": {"arm"},
        "BASE_PULSE": {"base"},
        "GRIPPER": {"gripper"},
    }[kind]
    if scope_set != expected:
        _fail(
            where,
            f"{kind} requires scope {', '.join(sorted(expected))} (or full), "
            f"not {', '.join(sorted(scope_set))}",
        )


def _check_params(kind: str, params: Mapping[str, Any], scopes: tuple[str, ...], where: str) -> None:
    allowed = _COMMON_PARAMS | _KIND_PARAMS[kind]
    unknown = set(params) - allowed
    if unknown:
        _fail(where, f"unknown parameter(s): {', '.join(sorted(unknown))}")
    missing = _REQUIRED_KIND_PARAMS[kind] - set(params)
    if missing:
        _fail(where, f"missing parameter(s): {', '.join(sorted(missing))}")
    _check_finite_tree(dict(params), where)

    if "verify_bus" in params:
        _bool(params["verify_bus"], f"{where}.verify_bus")
    if "require_measured_state" in params:
        _bool(params["require_measured_state"], f"{where}.require_measured_state")
    if "expected_profile_hash" in params:
        _text(params["expected_profile_hash"], f"{where}.expected_profile_hash")
    if "max_velocity_rad_s" in params:
        _positive(params["max_velocity_rad_s"], f"{where}.max_velocity_rad_s")
    if "max_acceleration_rad_s2" in params:
        _positive(params["max_acceleration_rad_s2"], f"{where}.max_acceleration_rad_s2")
    if "duration_s" in params:
        _positive(params["duration_s"], f"{where}.duration_s")

    if kind == "JOINT_TRAJECTORY":
        _check_trajectory(params["trajectory"], f"{where}.trajectory")
    elif kind == "JOINT_STEP":
        names = _string_list(params["joint_names"], f"{where}.joint_names")
        positions = _number_list(
            params["target_positions"], f"{where}.target_positions"
        )
        if len(names) != len(positions):
            _fail(
                where,
                f"joint_names has {len(names)} value(s) but target_positions has "
                f"{len(positions)}",
            )
    elif kind == "POSE":
        _check_pose(params["target_pose"], f"{where}.target_pose")
        _text(params["tip_link"], f"{where}.tip_link")
        _text(params["frame_id"], f"{where}.frame_id")
    elif kind == "BASE_PULSE":
        _check_base_pulse(params["base_pulse"], f"{where}.base_pulse")
    elif kind == "GRIPPER":
        _check_gripper(params["gripper"], f"{where}.gripper")

    touches_motion = bool(set(scopes) & MOTION_SCOPES)
    if kind == "GRIPPER":
        nested = params["gripper"]
        if touches_motion and not nested["verify_bus"]:
            _fail(f"{where}.gripper.verify_bus", "must be true for a motion scope")
    elif touches_motion and params.get("verify_bus") is not True:
        _fail(
            f"{where}.verify_bus",
            "must be true for an arm, gripper or full scope; completion needs a bus read",
        )


def _parse_step(value: Any, index: int, *, where_prefix: str = "steps") -> Step:
    where = f"{where_prefix}[{index}]"
    step = _mapping(value, where)
    _exact_keys(step, _STEP_KEYS, where)
    name = _name(step["name"], f"{where}.name")
    kind = _text(step["kind"], f"{where}.kind")
    if kind not in KINDS:
        _fail(f"{where}.kind", f"must be one of {', '.join(sorted(KINDS))}")
    profile = _text(step["profile"], f"{where}.profile")
    if profile not in PROFILES:
        _fail(f"{where}.profile", f"must be one of {', '.join(sorted(PROFILES))}")
    scopes = _string_list(step["scopes"], f"{where}.scopes")
    if len(set(scopes)) != len(scopes):
        _fail(f"{where}.scopes", "must not contain duplicates")
    for scope in scopes:
        if scope not in SCOPES:
            _fail(f"{where}.scopes", f"unknown scope {scope!r}")
    if "full" in scopes and len(scopes) != 1:
        _fail(f"{where}.scopes", "'full' cannot be combined with other scopes")
    params = _mapping(step["params"], f"{where}.params")
    _check_scope_semantics(kind, tuple(scopes), f"{where}.scopes")
    _check_params(kind, params, tuple(scopes), f"{where}.params")
    return Step(
        name=name,
        kind=kind,
        profile=profile,
        scopes=tuple(scopes),
        params=dict(params),
    )


def _nonnegative_int(value: Any, where: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(where, "must be an integer")
    if value < 0:
        _fail(where, "must not be negative")
    if maximum is not None and value > maximum:
        _fail(where, f"must not exceed {maximum}")
    return value


def _optional_keys(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    where: str,
) -> None:
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    if missing:
        _fail(where, f"missing field(s): {', '.join(missing)}")
    if extra:
        _fail(where, f"unknown field(s): {', '.join(extra)}")


def _parse_facts(value: Any, source: str) -> dict[str, FactSpec]:
    root = _mapping(value, f"{source}.facts")
    if not root:
        _fail(f"{source}.facts", "must not be empty when present")
    facts: dict[str, FactSpec] = {}
    for name, item in root.items():
        if not isinstance(name, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,63}", name
        ):
            _fail(f"{source}.facts", f"invalid fact name {name!r}")
        where = f"{source}.facts[{name}]"
        fact = _mapping(item, where)
        _exact_keys(fact, _FACT_KEYS, where)
        from_step = _name(fact["from_step"], f"{where}.from_step")
        measured = _mapping(fact["measured"], f"{where}.measured")
        _optional_keys(
            measured,
            required=frozenset({"index"}),
            allowed=_MEASURED_KEYS,
            where=f"{where}.measured",
        )
        index = _nonnegative_int(measured["index"], f"{where}.measured.index")
        at_least = (
            _finite_number(measured["at_least_rad"], f"{where}.measured.at_least_rad")
            if "at_least_rad" in measured
            else None
        )
        at_most = (
            _finite_number(measured["at_most_rad"], f"{where}.measured.at_most_rad")
            if "at_most_rad" in measured
            else None
        )
        if at_least is None and at_most is None:
            _fail(
                f"{where}.measured",
                "requires at_least_rad and/or at_most_rad",
            )
        if at_least is not None and at_most is not None and at_least > at_most:
            _fail(f"{where}.measured", "at_least_rad must not exceed at_most_rad")
        facts[name] = FactSpec(
            name=name,
            from_step=from_step,
            index=index,
            at_least_rad=at_least,
            at_most_rad=at_most,
        )
    return facts


def _parse_branches(value: Any, source: str) -> tuple[BranchSpec, ...]:
    raw = value
    if not isinstance(raw, list):
        _fail(f"{source}.branches", "must be a list")
    if not raw:
        _fail(f"{source}.branches", "must not be empty when present")
    branches: list[BranchSpec] = []
    ids: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{source}.branches[{index}]"
        branch = _mapping(item, where)
        _optional_keys(
            branch,
            required=frozenset({"id", "after", "when", "steps"}),
            allowed=_BRANCH_KEYS,
            where=where,
        )
        branch_id = _text(branch["id"], f"{where}.id")
        if not _BRANCH_ID_RE.fullmatch(branch_id):
            _fail(f"{where}.id", "must match ^[a-z0-9][a-z0-9.-]{0,63}$")
        if branch_id in ids:
            _fail(f"{where}.id", f"duplicate branch id {branch_id!r}")
        ids.add(branch_id)
        after = _name(branch["after"], f"{where}.after")
        required = (
            _bool(branch["required"], f"{where}.required")
            if "required" in branch
            else False
        )
        priority = (
            _nonnegative_int(branch["priority"], f"{where}.priority", maximum=100)
            if "priority" in branch
            else 0
        )
        raw_when = branch["when"]
        if not isinstance(raw_when, list) or not raw_when:
            _fail(f"{where}.when", "must be a non-empty list")
        conditions: list[tuple[str, bool]] = []
        seen_facts: set[str] = set()
        for condition_index, condition_value in enumerate(raw_when):
            condition_where = f"{where}.when[{condition_index}]"
            condition = _mapping(condition_value, condition_where)
            _exact_keys(condition, _CONDITION_KEYS, condition_where)
            fact = _text(condition["fact"], f"{condition_where}.fact")
            if fact in seen_facts:
                _fail(f"{condition_where}.fact", f"duplicate condition {fact!r}")
            seen_facts.add(fact)
            equals = _bool(condition["equals"], f"{condition_where}.equals")
            conditions.append((fact, equals))
        raw_steps = branch["steps"]
        if not isinstance(raw_steps, list):
            _fail(f"{where}.steps", "must be a list")
        if not 1 <= len(raw_steps) <= MAX_STEPS:
            _fail(f"{where}.steps", f"must contain 1 to {MAX_STEPS} steps")
        steps = tuple(
            _parse_step(step, step_index, where_prefix=f"{where}.steps")
            for step_index, step in enumerate(raw_steps)
        )
        branches.append(
            BranchSpec(
                id=branch_id,
                after=after,
                required=required,
                priority=priority,
                conditions=tuple(conditions),
                steps=steps,
            )
        )
    return tuple(branches)


def recipe_from_mapping(value: Any, *, source: str = "<mapping>") -> Recipe:
    """Validate a mapping and return a :class:`Recipe`."""

    recipe = _mapping(value, source)
    _optional_keys(
        recipe,
        required=_REQUIRED_TOP_LEVEL_KEYS,
        allowed=_TOP_LEVEL_KEYS,
        where=source,
    )
    name = _name(recipe["name"], f"{source}.name")
    version = _text(recipe["version"], f"{source}.version")
    if not _SEMVER_RE.fullmatch(version):
        _fail(f"{source}.version", "must be MAJOR.MINOR.PATCH")
    description = _text(recipe["description"], f"{source}.description")
    contract_hash = _text(recipe["contract_hash"], f"{source}.contract_hash")
    if contract_hash != CONTRACT_HASH:
        _fail(
            f"{source}.contract_hash",
            f"must equal the pinned v0.1.0 contract hash {CONTRACT_HASH}",
        )

    raw_scopes = _string_list(recipe["scopes"], f"{source}.scopes")
    if len(set(raw_scopes)) != len(raw_scopes):
        _fail(f"{source}.scopes", "must not contain duplicates")
    for scope in raw_scopes:
        if scope not in SCOPES:
            _fail(f"{source}.scopes", f"unknown scope {scope!r}")
    if "full" in raw_scopes and len(raw_scopes) != 1:
        _fail(f"{source}.scopes", "'full' cannot be combined with other scopes")
    scopes = tuple(raw_scopes)

    raw_steps = recipe["steps"]
    if not isinstance(raw_steps, list):
        _fail(f"{source}.steps", "must be a list")
    if not 1 <= len(raw_steps) <= MAX_STEPS:
        _fail(f"{source}.steps", f"must contain 1 to {MAX_STEPS} steps")
    steps = tuple(_parse_step(item, index) for index, item in enumerate(raw_steps))
    facts = _parse_facts(recipe["facts"], source) if "facts" in recipe else {}
    branches = (
        _parse_branches(recipe["branches"], source) if "branches" in recipe else ()
    )

    main_names = [step.name for step in steps]
    if len(set(main_names)) != len(main_names):
        _fail(f"{source}.steps", "step names must be unique")
    all_names = main_names + [
        step.name for branch in branches for step in branch.steps
    ]
    if len(set(all_names)) != len(all_names):
        _fail(
            f"{source}.branches",
            "step names must be unique across the main plan and every branch",
        )
    main_index = {step.name: index for index, step in enumerate(steps)}

    for fact_name, fact in facts.items():
        if fact.from_step not in main_index:
            _fail(
                f"{source}.facts[{fact_name}].from_step",
                "must name a main step",
            )
    for branch in branches:
        if branch.after not in main_index:
            _fail(
                f"{source}.branches[{branch.id}].after",
                "must name a main step",
            )
        anchor_index = main_index[branch.after]
        for fact_name, _equals in branch.conditions:
            if fact_name not in facts:
                _fail(
                    f"{source}.branches[{branch.id}].when",
                    f"unknown fact {fact_name!r}",
                )
            source_index = main_index[facts[fact_name].from_step]
            if source_index > anchor_index:
                _fail(
                    f"{source}.branches[{branch.id}].when",
                    f"fact {fact_name!r} is measured after the anchor step",
                )

    step_scopes: set[str] = set()
    for step in steps:
        step_scopes.update(step.scopes)
    for branch in branches:
        for step in branch.steps:
            step_scopes.update(step.scopes)
    if step_scopes != set(scopes):
        _fail(
            f"{source}.scopes",
            f"must equal the union of step scopes; recipe declares "
            f"{sorted(scopes)}, steps use {sorted(step_scopes)}",
        )

    return Recipe(
        name=name,
        version=version,
        description=description,
        contract_hash=contract_hash,
        scopes=scopes,
        steps=steps,
        facts=facts,
        branches=branches,
    )


def recipe_from_text(text: str, *, source: str = "<text>") -> Recipe:
    """Parse YAML text and validate it as a recipe."""

    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RecipeError(f"{source}: invalid YAML: {exc}") from exc
    if payload is None:
        raise RecipeError(f"{source}: empty document")
    return recipe_from_mapping(payload, source=source)


def load_recipe(path: Path | str) -> Recipe:
    """Load a recipe from a YAML file."""

    recipe_path = Path(path)
    try:
        text = recipe_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeError(f"{recipe_path}: cannot read recipe: {exc}") from exc
    return recipe_from_text(text, source=str(recipe_path))


def build_plan(recipe: Recipe, run_id: str) -> Plan:
    """Build the deterministic plan for ``recipe`` under ``run_id``.

    An idempotency key is a SHA-256 over the run, step index, name, kind,
    profile and canonical parameters. Re-planning the same run and payload
    yields the same key; changing any of them yields a different key.
    """

    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise RecipeError(
            "run_id must start with an alphanumeric character and contain only "
            "A-Z a-z 0-9 . _ : + - (maximum 128 characters)"
        )

    plan_steps = []
    for index, step in enumerate(recipe.steps):
        payload = {
            "run_id": run_id,
            "index": index,
            "name": step.name,
            "kind": step.kind,
            "profile": step.profile,
            "params": step.params,
        }
        key = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        plan_steps.append(
            PlanStep(
                index=index,
                name=step.name,
                kind=step.kind,
                profile=step.profile,
                scopes=step.scopes,
                params=dict(step.params),
                idempotency_key=key,
            )
        )

    facts = {
        name: PlanFact(
            name=name,
            from_step=fact.from_step,
            index=fact.index,
            at_least_rad=fact.at_least_rad,
            at_most_rad=fact.at_most_rad,
        )
        for name, fact in recipe.facts.items()
    }
    plan_branches = []
    next_index = len(plan_steps)
    for branch in recipe.branches:
        branch_steps = []
        for branch_index, step in enumerate(branch.steps):
            payload = {
                "run_id": run_id,
                "branch_id": branch.id,
                "branch_index": branch_index,
                "name": step.name,
                "kind": step.kind,
                "profile": step.profile,
                "params": step.params,
            }
            key = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
            branch_steps.append(
                PlanStep(
                    index=next_index,
                    name=step.name,
                    kind=step.kind,
                    profile=step.profile,
                    scopes=step.scopes,
                    params=dict(step.params),
                    idempotency_key=key,
                    branch_id=branch.id,
                    branch_index=branch_index,
                )
            )
            next_index += 1
        plan_branches.append(
            PlanBranch(
                id=branch.id,
                after=branch.after,
                required=branch.required,
                priority=branch.priority,
                conditions=branch.conditions,
                steps=tuple(branch_steps),
            )
        )

    return Plan(
        run_id=run_id,
        recipe_name=recipe.name,
        recipe_version=recipe.version,
        contract_hash=recipe.contract_hash,
        required_scopes=recipe.scopes,
        steps=tuple(plan_steps),
        facts=facts,
        branches=tuple(plan_branches),
    )


def requires_bus_verification(scopes: Iterable[str]) -> bool:
    """Return whether a completed step must carry a verified bus read.

    Arm and gripper completion is only trustworthy with a bus read. The base
    has no encoders and its pulse result cannot be bus-verified in the same
    sense, so it is excluded.
    """

    return bool(set(scopes) & MOTION_SCOPES)
