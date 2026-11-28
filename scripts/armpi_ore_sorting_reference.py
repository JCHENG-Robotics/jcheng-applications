#!/usr/bin/env python3
"""Offline ArmPi Ultra x ore_sorting reference integration.

The script is a repeatable, offline consumer of two pinned artifacts:

- a device snapshot (``jcheng.device_snapshot.v1``) generated from the device
  registry shape, and
- scenario requirements (``jcheng.scenario_requirements.v1``) derived from the
  ``ore_sorting`` scenario.

It validates schema, identity, versions and the canonical ``content_hash``,
matches every required capability against the device snapshot, builds a
deterministic plan through the existing applications recipe model, and runs
that plan through the existing mock gateway, supervised lease and linear
runner. It never opens a device, never joins DDS/ROS, never touches the
network beyond a ``127.0.0.1`` loopback mock and never claims a hardware or
task result.

Outputs, all under ``--out``:

- ``reference_report.json`` -- the full, deterministic reference report;
- ``plan.json`` -- the deterministic plan with scenario/task/evaluation IDs;
- ``synthetic_samples.jsonl`` -- one explicitly synthetic sample per executed
  mock step (``data_source: synthetic``);
- ``benchmark_suite.yaml``, ``benchmark_report.json`` and
  ``benchmark_report.md`` only when ``--benchmark-root`` is given.

Exit codes: 0 for a completed reference (benchmark skipped, or executed and
inconclusive/pass with its own report), 1 for a validation, capability,
simulation, mock or benchmark failure, 2 for a usage error.

The canonical ``content_hash`` of an artifact is the SHA-256 of the canonical
JSON encoding (sorted keys, no whitespace) of the document with the
``content_hash`` field removed and a ``sha256:`` prefix. Editing any other
field without regenerating the hash is reported as a stale artifact.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for _path in (REPO_ROOT / "src", REPO_ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import yaml

from jcheng_applications.model import (
    CONTRACT_HASH,
    Authorization,
    Recipe,
    RecipeError,
    build_plan,
    canonical_json,
    recipe_from_mapping,
)
from jcheng_applications.runner import RunReport
from jcheng_applications.runstore import RunStore
from jcheng_applications.supervised import SupervisedResult, run_supervised
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway
from mock_gateway import MockGatewayServer, completed

DEVICE_SCHEMA = "jcheng.device_snapshot.v1"
SCENARIO_SCHEMA = "jcheng.scenario_requirements.v1"
REPORT_SCHEMA = "jcheng.applications.reference_report.v1"
PLAN_SCHEMA = "jcheng.applications.reference_plan.v1"
SAMPLE_DATA_SOURCE = "synthetic"
RUN_ID = "armpi-ore-sorting-reference"
FAILURE_RUN_ID = "armpi-ore-sorting-reference-failure"
MOCK_TOKEN = "armpi-ore-sorting-reference-token"
MOCK_OPERATOR = "reference-operator"
MOCK_APPROVED_AT = "2026-11-28T00:00:00+00:00"
RECIPE_VERSION = "0.1.0"
BENCHMARK_TIMEOUT_S = 60

_DEVICE_KEYS = frozenset(
    {
        "schema",
        "source_device",
        "device_version",
        "content_hash",
        "generated_by",
        "source_path",
        "capabilities",
        "joints",
        "safety_overlay",
    }
)
_DEVICE_OPTIONAL_KEYS = frozenset({"notes", "source_hash", "attributes"})
_SCENARIO_KEYS = frozenset(
    {
        "schema",
        "scenario_id",
        "scenario_version",
        "content_hash",
        "task_id",
        "evaluation_id",
        "capabilities",
        "constraints",
        "provenance",
        "simulation_assets",
        "vocabulary_status",
    }
)
_SCENARIO_REQUIRED_KEYS = frozenset(
    {
        "schema",
        "scenario_id",
        "scenario_version",
        "content_hash",
        "task_id",
        "evaluation_id",
        "capabilities",
        "constraints",
        "provenance",
    }
)
_CAPABILITY_KEYS = frozenset({"id", "parameters", "feedback"})
_REQUIREMENT_KEYS = frozenset(
    {
        "id",
        "required",
        "parameters",
        "requirement_id",
        "requirement_owner",
        "role",
        "interface",
        "threshold",
        "statement",
        "verification",
        "deferred_to",
    }
)
_ATTRIBUTE_KEYS = frozenset(
    {
        "category",
        "role",
        "dof",
        "control_modes",
        "kinematics_declared",
        "data_status",
    }
)
_ROLES = frozenset({"arm", "gripper", "camera", "base", "sensor", "teleop"})
_THRESHOLD_KEYS = frozenset({"op", "value", "unit"})
_JOINT_KEYS = frozenset({"name", "min_rad", "max_rad"})
_CONSTRAINT_KEYS = frozenset({"id", "statement"})
_ASSET_KEYS = frozenset({"id", "path"})

_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,127}$")
_SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:+-]{0,127}$")
_JOINT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_PARAMETER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_FACT_UNITS = {
    "dof": "joints",
    "reach_m": "m",
    "payload_kg": "kg",
    "aperture_m": "m",
    "resolution_px": "px",
}
_ORDERING_OPS = frozenset({">=", "<=", ">", "<"})
_COMPARISON_OPS = _ORDERING_OPS | frozenset({"==", "!="})

# Reference-only constants. They are the smallest valid commands the existing
# recipe model accepts for the two step kinds; they are not task targets, not
# success criteria and not calibrated values.
_HOME_PARAMS: dict[str, Any] = {"verify_bus": True}
_GRIPPER_PARAMS: dict[str, Any] = {
    "gripper": {
        "position_rad": 1.0,
        "max_velocity_rad_s": 0.1,
        "duration_s": 1.0,
        "verify_bus": True,
    }
}


class ReferenceCheckError(RuntimeError):
    """The reference cannot continue; the message names the failed check."""


def _fail(code: str, message: str) -> None:
    raise ReferenceCheckError(f"{code}: {message}")


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("SCHEMA", f"{where}: must be a mapping")
    for key in value:
        if not isinstance(key, str):
            _fail("SCHEMA", f"{where}: key {key!r} is not a string")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], where: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        _fail("SCHEMA", f"{where}: missing field(s): {', '.join(missing)}")
    if extra:
        _fail("SCHEMA", f"{where}: unknown field(s): {', '.join(extra)}")


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
        _fail("SCHEMA", f"{where}: missing field(s): {', '.join(missing)}")
    if extra:
        _fail("SCHEMA", f"{where}: unknown field(s): {', '.join(extra)}")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("SCHEMA", f"{where}: must be a non-empty string")
    return value


def _semver(value: Any, where: str) -> str:
    text = _text(value, where)
    if not _SEMVER_RE.fullmatch(text):
        _fail("SCHEMA", f"{where}: must be MAJOR.MINOR.PATCH")
    return text


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        _fail("SCHEMA", f"{where}: must be a boolean")
    return value


def _finite(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("SCHEMA", f"{where}: must be a number")
    number = float(value)
    if not math.isfinite(number):
        _fail("SCHEMA", f"{where}: must be finite")
    return number


def _list(value: Any, where: str, *, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        _fail("SCHEMA", f"{where}: must be a list")
    if not minimum <= len(value) <= maximum:
        _fail("SCHEMA", f"{where}: must contain {minimum} to {maximum} item(s)")
    return value


def _check_json_tree(value: Any, where: str) -> None:
    """Reject YAML-only values so the canonical hash is unambiguous."""

    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("SCHEMA", f"{where}: must be finite")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_json_tree(item, f"{where}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("SCHEMA", f"{where}: key {key!r} is not a string")
            _check_json_tree(item, f"{where}.{key}")
        return
    _fail("SCHEMA", f"{where}: unsupported value type {type(value).__name__}")


def _parameter_value(value: Any, where: str) -> Any:
    """A capability parameter is a scalar or a flat list of scalars."""

    if isinstance(value, Mapping):
        _fail("SCHEMA", f"{where}: a parameter value must not be a mapping")
    if isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, (Mapping, list)):
                _fail("SCHEMA", f"{where}[{index}]: must be a scalar")
            _check_json_tree(item, f"{where}[{index}]")
        return value
    _check_json_tree(value, where)
    return value


def _parameters(value: Any, where: str) -> dict[str, Any]:
    root = _mapping(value, where)
    result: dict[str, Any] = {}
    for key, item in root.items():
        if not _PARAMETER_NAME_RE.fullmatch(key):
            _fail(
                "SCHEMA",
                f"{where}: parameter name {key!r} must match "
                f"{_PARAMETER_NAME_RE.pattern}",
            )
        result[key] = _parameter_value(item, f"{where}.{key}")
    return result


def _expectations(value: Any, where: str) -> dict[str, Any]:
    """Validate requirement parameters, which are expectations, not values."""

    root = _mapping(value, where)
    result: dict[str, Any] = {}
    for key, item in root.items():
        if not _PARAMETER_NAME_RE.fullmatch(key):
            _fail(
                "SCHEMA",
                f"{where}: parameter name {key!r} must match "
                f"{_PARAMETER_NAME_RE.pattern}",
            )
        _expectation(f"{where}.{key}", item)
        result[key] = item
    return result


def content_hash(payload: Mapping[str, Any]) -> str:
    """Return the canonical content hash of a parsed artifact."""

    body = {key: value for key, value in payload.items() if key != "content_hash"}
    return "sha256:" + sha256(canonical_json(body).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Artifact loading and validation
# ---------------------------------------------------------------------------


def load_document(path: Path) -> Mapping[str, Any]:
    """Load a YAML or JSON mapping; JSON files are parsed strictly."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail("INPUT", f"cannot read {path}: {exc}")
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            _fail("INPUT", f"{path}: invalid JSON: {exc.msg}")
    else:
        try:
            payload = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            _fail("INPUT", f"{path}: invalid YAML: {exc}")
    if payload is None:
        _fail("INPUT", f"{path}: empty document")
    if not isinstance(payload, Mapping):
        _fail("INPUT", f"{path}: top-level document must be a mapping")
    return payload


@dataclass(frozen=True)
class DeviceCapability:
    id: str
    parameters: Mapping[str, Any]
    feedback: tuple[str, ...]


@dataclass(frozen=True)
class Joint:
    name: str
    min_rad: float
    max_rad: float


@dataclass(frozen=True)
class DeviceSnapshot:
    source_device: str
    device_version: str
    source_hash: str
    content_hash: str
    generated_by: str
    source_path: str
    capabilities: tuple[DeviceCapability, ...]
    joints: tuple[Joint, ...]
    attributes: Mapping[str, Any]
    safety_overlay: Mapping[str, Any]

    @property
    def role(self) -> str | None:
        role = self.attributes.get("role")
        return role if isinstance(role, str) and role else None


@dataclass(frozen=True)
class Requirement:
    id: str
    required: bool
    parameters: Mapping[str, Any]
    requirement_id: str = ""
    requirement_owner: str = "device"
    role: str | None = None
    interface: str | None = None
    threshold: Mapping[str, Any] | None = None
    statement: str = ""
    verification: str = ""
    deferred_to: str | None = None


@dataclass(frozen=True)
class SimulationAsset:
    id: str
    path: str


@dataclass(frozen=True)
class ScenarioRequirements:
    scenario_id: str
    scenario_version: str
    content_hash: str
    task_id: str
    evaluation_id: str
    capabilities: tuple[Requirement, ...]
    constraints: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any]
    simulation_assets: tuple[SimulationAsset, ...]


def _check_hash(document: Mapping[str, Any], code: str, label: str) -> str:
    declared = _text(document["content_hash"], f"{label}.content_hash")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", declared):
        _fail("SCHEMA", f"{label}.content_hash: must be sha256:<64 lowercase hex>")
    expected = content_hash(document)
    if declared != expected:
        _fail(
            code,
            f"{label} content_hash mismatch: document declares {declared}, but "
            f"the canonical content hashes to {expected}; the artifact is stale "
            "or was edited without regeneration",
        )
    return declared


def device_snapshot_from_mapping(document: Mapping[str, Any]) -> DeviceSnapshot:
    """Validate a ``jcheng.device_snapshot.v1`` document."""

    _optional_keys(
        document,
        required=_DEVICE_KEYS,
        allowed=_DEVICE_KEYS | _DEVICE_OPTIONAL_KEYS,
        where="device_snapshot",
    )
    if document["schema"] != DEVICE_SCHEMA:
        _fail(
            "SCHEMA",
            f"device_snapshot.schema: must be {DEVICE_SCHEMA!r}, "
            f"not {document['schema']!r}",
        )
    source_device = _text(document["source_device"], "device_snapshot.source_device")
    if not _DEVICE_ID_RE.fullmatch(source_device):
        _fail(
            "SCHEMA",
            "device_snapshot.source_device: must match "
            f"{_DEVICE_ID_RE.pattern}",
        )
    device_version = _semver(
        document["device_version"], "device_snapshot.device_version"
    )
    generated_by = _text(document["generated_by"], "device_snapshot.generated_by")
    source_path = _text(document["source_path"], "device_snapshot.source_path")
    source_hash = ""
    if "source_hash" in document:
        source_hash = _text(document["source_hash"], "device_snapshot.source_hash")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash):
            _fail(
                "SCHEMA",
                "device_snapshot.source_hash: must be sha256:<64 lowercase hex>",
            )
    attributes: dict[str, Any] = {}
    if "attributes" in document:
        attributes = _snapshot_attributes(
            document["attributes"], "device_snapshot.attributes"
        )

    raw_capabilities = _list(
        document["capabilities"],
        "device_snapshot.capabilities",
        minimum=1,
        maximum=128,
    )
    capabilities: list[DeviceCapability] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_capabilities):
        where = f"device_snapshot.capabilities[{index}]"
        item = _mapping(raw, where)
        _exact_keys(item, _CAPABILITY_KEYS, where)
        capability_id = _text(item["id"], f"{where}.id")
        if not _CAPABILITY_ID_RE.fullmatch(capability_id):
            _fail(
                "SCHEMA",
                f"{where}.id: must match {_CAPABILITY_ID_RE.pattern}",
            )
        if capability_id in seen:
            _fail("SCHEMA", f"{where}.id: duplicate capability {capability_id!r}")
        seen.add(capability_id)
        raw_parameters = item["parameters"]
        if isinstance(raw_parameters, list):
            # A device profile may declare parameter descriptors (name/type/
            # unit/description) rather than values. They are not matchable
            # values; keep an empty value map and record no invented values.
            for parameter_index, descriptor in enumerate(raw_parameters):
                _mapping(descriptor, f"{where}.parameters[{parameter_index}]")
            parameters: dict[str, Any] = {}
        else:
            parameters = _parameters(raw_parameters, f"{where}.parameters")
        raw_feedback = item["feedback"]
        if not isinstance(raw_feedback, list):
            _fail("SCHEMA", f"{where}.feedback: must be a list")
        feedback: list[str] = []
        for feedback_index, entry in enumerate(raw_feedback):
            if isinstance(entry, Mapping):
                text = _text(
                    entry.get("name"), f"{where}.feedback[{feedback_index}].name"
                )
            else:
                text = _text(entry, f"{where}.feedback[{feedback_index}]")
            if text in feedback:
                _fail(
                    "SCHEMA",
                    f"{where}.feedback: duplicate entry {text!r}",
                )
            feedback.append(text)
        capabilities.append(
            DeviceCapability(
                id=capability_id,
                parameters=parameters,
                feedback=tuple(feedback),
            )
        )

    raw_joints = _list(
        document["joints"], "device_snapshot.joints", minimum=0, maximum=32
    )
    joints: list[Joint] = []
    joint_names: set[str] = set()
    for index, raw in enumerate(raw_joints):
        where = f"device_snapshot.joints[{index}]"
        item = _mapping(raw, where)
        if "limits" in item:
            _optional_keys(
                item,
                required=frozenset({"name", "limits"}),
                allowed=frozenset({"name", "limits", "role"}),
                where=where,
            )
            limits = _mapping(item["limits"], f"{where}.limits")
            _exact_keys(
                limits, frozenset({"min_deg", "max_deg"}), f"{where}.limits"
            )
            minimum = math.radians(
                _finite(limits["min_deg"], f"{where}.limits.min_deg")
            )
            maximum = math.radians(
                _finite(limits["max_deg"], f"{where}.limits.max_deg")
            )
        else:
            _optional_keys(
                item,
                required=_JOINT_KEYS,
                allowed=_JOINT_KEYS | frozenset({"role"}),
                where=where,
            )
            minimum = _finite(item["min_rad"], f"{where}.min_rad")
            maximum = _finite(item["max_rad"], f"{where}.max_rad")
        name = _text(item["name"], f"{where}.name")
        if not _JOINT_NAME_RE.fullmatch(name):
            _fail("SCHEMA", f"{where}.name: must match {_JOINT_NAME_RE.pattern}")
        if name in joint_names:
            _fail("SCHEMA", f"{where}.name: duplicate joint {name!r}")
        joint_names.add(name)
        if minimum > maximum:
            _fail("SCHEMA", f"{where}: min must not exceed max")
        joints.append(Joint(name=name, min_rad=minimum, max_rad=maximum))

    if attributes.get("role") == "arm" and not joints:
        _fail(
            "SCHEMA",
            "device_snapshot.joints: an arm snapshot needs at least one joint",
        )
    safety_overlay = _mapping(
        document["safety_overlay"], "device_snapshot.safety_overlay"
    )
    _check_json_tree(dict(safety_overlay), "device_snapshot.safety_overlay")

    declared_hash = _check_hash(document, "STALE_DEVICE_HASH", "device_snapshot")
    return DeviceSnapshot(
        source_device=source_device,
        device_version=device_version,
        source_hash=source_hash,
        content_hash=declared_hash,
        generated_by=generated_by,
        source_path=source_path,
        capabilities=tuple(capabilities),
        joints=tuple(joints),
        attributes=attributes,
        safety_overlay=dict(safety_overlay),
    )


def _snapshot_attributes(value: Any, where: str) -> dict[str, Any]:
    attributes = _mapping(value, where)
    _optional_keys(
        attributes,
        required=frozenset({"category", "role", "dof", "control_modes"}),
        allowed=_ATTRIBUTE_KEYS,
        where=where,
    )
    category = _text(attributes["category"], f"{where}.category")
    role = _text(attributes["role"], f"{where}.role")
    if role not in _ROLES:
        _fail("SCHEMA", f"{where}.role: unknown device role {role!r}")
    dof = attributes["dof"]
    if dof is not None:
        if not isinstance(dof, int) or isinstance(dof, bool) or dof < 1:
            _fail("SCHEMA", f"{where}.dof: must be null or a positive integer")
    modes = attributes["control_modes"]
    if not isinstance(modes, list) or any(
        not isinstance(mode, str) for mode in modes
    ):
        _fail("SCHEMA", f"{where}.control_modes: must be a list of strings")
    _check_json_tree(dict(attributes), where)
    result: dict[str, Any] = {
        "category": category,
        "role": role,
        "dof": dof,
        "control_modes": list(modes),
    }
    if "kinematics_declared" in attributes:
        result["kinematics_declared"] = _bool(
            attributes["kinematics_declared"], f"{where}.kinematics_declared"
        )
    if "data_status" in attributes:
        result["data_status"] = _text(
            attributes["data_status"], f"{where}.data_status"
        )
    return result


def _threshold(value: Any, where: str) -> dict[str, Any]:
    item = _mapping(value, where)
    _exact_keys(item, _THRESHOLD_KEYS, where)
    op = _text(item["op"], f"{where}.op")
    if op not in _COMPARISON_OPS:
        _fail("SCHEMA", f"{where}.op: unknown comparison {op!r}")
    number = _finite(item["value"], f"{where}.value")
    unit = _text(item["unit"], f"{where}.unit")
    return {"op": op, "value": number, "unit": unit}


def _reference_id(value: Any, where: str) -> str:
    text = _text(value, where)
    if not _REFERENCE_ID_RE.fullmatch(text):
        _fail("SCHEMA", f"{where}: must match {_REFERENCE_ID_RE.pattern}")
    if "@" not in text:
        _fail("SCHEMA", f"{where}: must carry an @version suffix")
    name, version = text.rsplit("@", 1)
    if not name or not _SEMVER_RE.fullmatch(version):
        _fail("SCHEMA", f"{where}: must be <id>@MAJOR.MINOR.PATCH")
    return text


def _simulation_path(value: Any, where: str) -> str:
    text = _text(value, where).replace("\\", "/")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        _fail(
            "SCHEMA",
            f"{where}: must be a relative path inside the simulation root",
        )
    return text


def scenario_requirements_from_mapping(
    document: Mapping[str, Any],
) -> ScenarioRequirements:
    """Validate a ``jcheng.scenario_requirements.v1`` document."""

    _optional_keys(
        document,
        required=_SCENARIO_REQUIRED_KEYS,
        allowed=_SCENARIO_KEYS,
        where="scenario_requirements",
    )
    if document["schema"] != SCENARIO_SCHEMA:
        _fail(
            "SCHEMA",
            f"scenario_requirements.schema: must be {SCENARIO_SCHEMA!r}, "
            f"not {document['schema']!r}",
        )
    scenario_id = _text(document["scenario_id"], "scenario_requirements.scenario_id")
    if not _SCENARIO_ID_RE.fullmatch(scenario_id):
        _fail(
            "SCHEMA",
            "scenario_requirements.scenario_id: must match "
            f"{_SCENARIO_ID_RE.pattern}",
        )
    scenario_version = _semver(
        document["scenario_version"], "scenario_requirements.scenario_version"
    )
    task_id = _reference_id(document["task_id"], "scenario_requirements.task_id")
    evaluation_id = _reference_id(
        document["evaluation_id"], "scenario_requirements.evaluation_id"
    )
    for field, reference in (("task_id", task_id), ("evaluation_id", evaluation_id)):
        reference_version = reference.rsplit("@", 1)[1]
        if reference_version != scenario_version:
            _fail(
                "SCENARIO_VERSION_MISMATCH",
                f"scenario_requirements.{field} references {reference!r} but "
                f"scenario_version is {scenario_version}; a version change "
                "requires a matching task/evaluation reference",
            )

    raw_capabilities = _list(
        document["capabilities"],
        "scenario_requirements.capabilities",
        minimum=1,
        maximum=128,
    )
    capabilities: list[Requirement] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_capabilities):
        where = f"scenario_requirements.capabilities[{index}]"
        item = _mapping(raw, where)
        _optional_keys(
            item,
            required=frozenset({"id", "required"}),
            allowed=_REQUIREMENT_KEYS,
            where=where,
        )
        capability_id = _text(item["id"], f"{where}.id")
        if not _CAPABILITY_ID_RE.fullmatch(capability_id):
            _fail(
                "SCHEMA",
                f"{where}.id: must match {_CAPABILITY_ID_RE.pattern}",
            )
        if capability_id in seen:
            _fail("SCHEMA", f"{where}.id: duplicate requirement {capability_id!r}")
        seen.add(capability_id)
        required = _bool(item["required"], f"{where}.required")
        parameters = (
            _expectations(item["parameters"], f"{where}.parameters")
            if "parameters" in item
            else {}
        )
        requirement_owner = "device"
        if "requirement_owner" in item:
            requirement_owner = _text(
                item["requirement_owner"], f"{where}.requirement_owner"
            )
            if requirement_owner not in {"device", "algorithm"}:
                _fail(
                    "SCHEMA",
                    f"{where}.requirement_owner: must be 'device' or 'algorithm'",
                )
        role = None
        interface = None
        threshold = None
        if requirement_owner == "device":
            if "role" in item:
                role = _text(item["role"], f"{where}.role")
                if role not in _ROLES:
                    _fail("SCHEMA", f"{where}.role: unknown device role {role!r}")
            if "interface" in item:
                interface = _text(item["interface"], f"{where}.interface")
                if not _CAPABILITY_ID_RE.fullmatch(interface):
                    _fail(
                        "SCHEMA",
                        f"{where}.interface: must match {_CAPABILITY_ID_RE.pattern}",
                    )
            if "threshold" in item:
                threshold = _threshold(item["threshold"], f"{where}.threshold")
        else:
            for forbidden in ("role", "interface", "threshold"):
                if forbidden in item:
                    _fail(
                        "SCHEMA",
                        f"{where}: an algorithm requirement must not declare {forbidden}",
                    )
        deferred_to = None
        if "deferred_to" in item:
            deferred_to = _text(item["deferred_to"], f"{where}.deferred_to")
        statement = (
            _text(item["statement"], f"{where}.statement")
            if "statement" in item
            else ""
        )
        verification = (
            _text(item["verification"], f"{where}.verification")
            if "verification" in item
            else ""
        )
        capabilities.append(
            Requirement(
                id=capability_id,
                required=required,
                parameters=parameters,
                requirement_id=(
                    _text(item["requirement_id"], f"{where}.requirement_id")
                    if "requirement_id" in item
                    else ""
                ),
                requirement_owner=requirement_owner,
                role=role,
                interface=interface,
                threshold=threshold,
                statement=statement,
                verification=verification,
                deferred_to=deferred_to,
            )
        )

    raw_constraints = document["constraints"]
    if not isinstance(raw_constraints, list):
        _fail("SCHEMA", "scenario_requirements.constraints: must be a list")
    constraints: list[Mapping[str, Any]] = []
    constraint_ids: set[str] = set()
    for index, raw in enumerate(raw_constraints):
        where = f"scenario_requirements.constraints[{index}]"
        item = _mapping(raw, where)
        _exact_keys(item, _CONSTRAINT_KEYS, where)
        constraint_id = _text(item["id"], f"{where}.id")
        if constraint_id in constraint_ids:
            _fail("SCHEMA", f"{where}.id: duplicate constraint {constraint_id!r}")
        constraint_ids.add(constraint_id)
        statement = _text(item["statement"], f"{where}.statement")
        constraints.append({"id": constraint_id, "statement": statement})

    provenance = _mapping(document["provenance"], "scenario_requirements.provenance")
    if not provenance:
        _fail("SCHEMA", "scenario_requirements.provenance: must not be empty")
    _check_json_tree(dict(provenance), "scenario_requirements.provenance")

    assets: list[SimulationAsset] = []
    if "simulation_assets" in document:
        raw_assets = document["simulation_assets"]
        if not isinstance(raw_assets, list):
            _fail("SCHEMA", "scenario_requirements.simulation_assets: must be a list")
        asset_ids: set[str] = set()
        for index, raw in enumerate(raw_assets):
            where = f"scenario_requirements.simulation_assets[{index}]"
            item = _mapping(raw, where)
            _exact_keys(item, _ASSET_KEYS, where)
            asset_id = _text(item["id"], f"{where}.id")
            if asset_id in asset_ids:
                _fail("SCHEMA", f"{where}.id: duplicate asset {asset_id!r}")
            asset_ids.add(asset_id)
            assets.append(
                SimulationAsset(
                    id=asset_id,
                    path=_simulation_path(item["path"], f"{where}.path"),
                )
            )

    declared_hash = _check_hash(
        document, "STALE_SCENARIO_HASH", "scenario_requirements"
    )
    return ScenarioRequirements(
        scenario_id=scenario_id,
        scenario_version=scenario_version,
        content_hash=declared_hash,
        task_id=task_id,
        evaluation_id=evaluation_id,
        capabilities=tuple(capabilities),
        constraints=tuple(constraints),
        provenance=dict(provenance),
        simulation_assets=tuple(assets),
    )


# ---------------------------------------------------------------------------
# Capability matching
# ---------------------------------------------------------------------------


def _expectation(where: str, value: Any) -> tuple[str, Any]:
    """Normalise a requirement parameter to ``(op, value)``.

    A bare scalar means equality. A mapping must be exactly ``{op, value}``
    with an explicit comparison, so no direction is assumed silently.
    """

    if not isinstance(value, Mapping):
        _parameter_value(value, where)
        return "==", value
    _exact_keys(value, frozenset({"op", "value"}), where)
    op = _text(value["op"], f"{where}.op")
    if op not in _COMPARISON_OPS:
        _fail(
            "INCOMPATIBLE_PARAMETER",
            f"{where}.op: unsupported comparison {op!r}; use one of "
            f"{', '.join(sorted(_COMPARISON_OPS))}",
        )
    expected = _parameter_value(value["value"], f"{where}.value")
    return op, expected


def _equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual is expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return float(actual) == float(expected)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual == expected
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _equal(left, right) for left, right in zip(actual, expected)
        )
    return False


def _compare(actual: Any, op: str, expected: Any, where: str) -> bool:
    if op in _ORDERING_OPS:
        if isinstance(actual, bool) or isinstance(expected, bool):
            _fail(
                "INCOMPATIBLE_PARAMETER",
                f"{where}: an ordering comparison requires two numbers",
            )
        if not isinstance(actual, (int, float)) or not isinstance(
            expected, (int, float)
        ):
            _fail(
                "INCOMPATIBLE_PARAMETER",
                f"{where}: an ordering comparison requires two numbers, got "
                f"{type(actual).__name__} and {type(expected).__name__}",
            )
        left, right = float(actual), float(expected)
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        return left < right
    if op == "==":
        return _equal(actual, expected)
    return not _equal(actual, expected)


@dataclass(frozen=True)
class CapabilityMatch:
    matched: tuple[Mapping[str, Any], ...]
    optional_absent: tuple[str, ...]
    unused_device_capabilities: tuple[str, ...]
    unverified: tuple[Mapping[str, Any], ...] = ()
    deferred: tuple[Mapping[str, Any], ...] = ()
    status: str = "matched"

    def report(
        self,
        device_or_devices: DeviceSnapshot | Mapping[str, DeviceSnapshot],
        scenario: ScenarioRequirements,
    ) -> dict[str, Any]:
        if isinstance(device_or_devices, DeviceSnapshot):
            devices = {device_or_devices.role or "device": device_or_devices}
        else:
            devices = dict(device_or_devices)
        primary = next(iter(devices.values()))
        return {
            "status": self.status,
            "capability_satisfied": self.status == "matched",
            "deployable": self.status == "matched",  # deprecated alias
            "deployment_ready": False,
            "motion_authorized": False,
            "device": primary.source_device,
            "devices": {
                role: {
                    "source_device": device.source_device,
                    "device_version": device.device_version,
                    "source_hash": device.source_hash,
                    "content_hash": device.content_hash,
                    "role": device.role,
                }
                for role, device in sorted(devices.items())
            },
            "scenario": scenario.scenario_id,
            "required_count": sum(
                1
                for item in (*self.matched, *self.unverified, *self.deferred)
                if item["required"] is True
            ),
            "matched": [dict(item) for item in self.matched],
            "unverified": [dict(item) for item in self.unverified],
            "deferred": [dict(item) for item in self.deferred],
            "optional_absent": list(self.optional_absent),
            "unused_device_capabilities": list(self.unused_device_capabilities),
        }


def match_capabilities(
    device_or_devices: DeviceSnapshot | Mapping[str, DeviceSnapshot],
    scenario: ScenarioRequirements,
) -> CapabilityMatch:
    """Match scenario requirements against one device or a role-composed cell.

    A single snapshot keeps the exact-capability-id contract used by the pinned
    fixture. A role mapping enables cell composition: each device requirement
    names a role and a concrete interface, the snapshot filling that role must
    declare the interface, and a declared threshold is checked only against a
    device fact. A threshold with no declared fact is ``unverified``; it is
    never promoted to ``matched``. Algorithm requirements are deferred to their
    owner and are never matched against a device.
    """

    if isinstance(device_or_devices, DeviceSnapshot):
        return _match_single_device(device_or_devices, scenario)
    return _match_cell(device_or_devices, scenario)


def _match_single_device(
    device: DeviceSnapshot, scenario: ScenarioRequirements
) -> CapabilityMatch:
    device_by_id = {capability.id: capability for capability in device.capabilities}
    matched: list[Mapping[str, Any]] = []
    optional_absent: list[str] = []
    for requirement in scenario.capabilities:
        if requirement.requirement_owner == "algorithm":
            continue
        capability = device_by_id.get(requirement.id)
        if capability is None:
            if requirement.required:
                _fail(
                    "INCOMPATIBLE_CAPABILITY",
                    f"required capability {requirement.id!r} is not declared by "
                    f"device {device.source_device!r}",
                )
            optional_absent.append(requirement.id)
            continue
        checks: list[dict[str, Any]] = []
        for parameter, raw_expectation in requirement.parameters.items():
            where = f"capability {requirement.id!r} parameter {parameter!r}"
            op, expected = _expectation(where, raw_expectation)
            if parameter not in capability.parameters:
                _fail(
                    "INCOMPATIBLE_PARAMETER",
                    f"{where}: the device does not declare this parameter",
                )
            actual = capability.parameters[parameter]
            if not _compare(actual, op, expected, where):
                _fail(
                    "INCOMPATIBLE_PARAMETER",
                    f"{where}: device declares {actual!r}, requirement is "
                    f"{op} {expected!r}",
                )
            checks.append(
                {
                    "parameter": parameter,
                    "op": op,
                    "required": expected,
                    "device": actual,
                    "result": "ok",
                }
            )
        matched.append(
            {
                "id": requirement.id,
                "required": requirement.required,
                "device_parameters": dict(capability.parameters),
                "device_feedback": list(capability.feedback),
                "checks": checks,
            }
        )

    required_ids = {item.id for item in scenario.capabilities}
    unused = tuple(
        sorted(
            capability.id
            for capability in device.capabilities
            if capability.id not in required_ids
        )
    )
    return CapabilityMatch(
        matched=tuple(matched),
        optional_absent=tuple(optional_absent),
        unused_device_capabilities=unused,
    )


def _match_cell(
    devices: Mapping[str, DeviceSnapshot], scenario: ScenarioRequirements
) -> CapabilityMatch:
    if not devices:
        _fail("CAPABILITY_CONTRACT", "a cell match needs at least one device")
    for role, device in devices.items():
        if role not in _ROLES:
            _fail("CAPABILITY_CONTRACT", f"unknown composition role {role!r}")
        declared = device.role
        if declared is not None and declared != role:
            _fail(
                "CAPABILITY_CONTRACT",
                f"device {device.source_device!r} declares role {declared!r}, "
                f"but was composed as {role!r}",
            )

    matched: list[Mapping[str, Any]] = []
    unverified: list[Mapping[str, Any]] = []
    deferred: list[Mapping[str, Any]] = []
    optional_absent: list[str] = []
    used_capabilities: set[tuple[str, str]] = set()

    for requirement in scenario.capabilities:
        if requirement.requirement_owner == "algorithm":
            deferred.append(
                {
                    "id": requirement.id,
                    "requirement_id": requirement.requirement_id,
                    "required": requirement.required,
                    "deferred_to": requirement.deferred_to,
                    "statement": requirement.statement,
                }
            )
            continue
        if requirement.role is None or requirement.interface is None:
            _fail(
                "CAPABILITY_CONTRACT",
                f"device requirement {requirement.id!r} must declare role and interface",
            )
        device = devices.get(requirement.role)
        if device is None:
            if requirement.required:
                _fail(
                    "NO_DEVICE_FOR_ROLE",
                    f"device requirement {requirement.id!r} needs role "
                    f"{requirement.role!r}, but the cell declares no such device",
                )
            optional_absent.append(requirement.id)
            continue
        capabilities = {capability.id: capability for capability in device.capabilities}
        capability = capabilities.get(requirement.interface)
        if capability is None:
            _fail(
                "INCOMPATIBLE_CAPABILITY",
                f"device {device.source_device!r} filling role "
                f"{requirement.role!r} does not declare interface "
                f"{requirement.interface!r}",
            )
        used_capabilities.add((requirement.role, requirement.interface))
        base = {
            "id": requirement.id,
            "requirement_id": requirement.requirement_id,
            "required": requirement.required,
            "role": requirement.role,
            "interface": requirement.interface,
            "device": device.source_device,
            "interface_ok": True,
            "verification": requirement.verification,
        }
        if requirement.threshold is None:
            unverified.append(
                {
                    **base,
                    "reason": "NO_MACHINE_READABLE_THRESHOLD",
                    "statement": requirement.statement,
                }
            )
            continue

        property_name = requirement.id.rsplit(".", 1)[-1]
        expected_unit = _FACT_UNITS.get(property_name)
        if expected_unit is not None and requirement.threshold["unit"] != expected_unit:
            _fail(
                "INCOMPATIBLE_UNIT",
                f"device requirement {requirement.id!r} uses unit "
                f"{requirement.threshold['unit']!r}, expected {expected_unit!r}",
            )
        actual = _declared_fact(device, capability, requirement)
        if actual is None:
            unverified.append(
                {
                    **base,
                    "reason": "DEVICE_FACT_NOT_DECLARED",
                    "statement": requirement.statement,
                    "threshold": dict(requirement.threshold),
                }
            )
            continue
        op = str(requirement.threshold["op"])
        expected = requirement.threshold["value"]
        where = (
            f"device requirement {requirement.id!r} on "
            f"{device.source_device!r}"
        )
        if not _compare(actual, op, expected, where):
            _fail(
                "INCOMPATIBLE_PARAMETER",
                f"{where}: device declares {actual!r}, requirement is "
                f"{op} {expected!r} {requirement.threshold['unit']}",
            )
        matched.append(
            {
                **base,
                "statement": requirement.statement,
                "threshold": dict(requirement.threshold),
                "device_value": actual,
                "evidence": (
                    "unverified"
                    if requirement.verification == "Unverified"
                    else requirement.verification.lower()
                ),
            }
        )

    unused = tuple(
        sorted(
            f"{role}:{capability.id}"
            for role, device in devices.items()
            for capability in device.capabilities
            if (role, capability.id) not in used_capabilities
        )
    )
    evidence_unverified = any(
        item.get("verification") == "Unverified" for item in matched
    )
    if not matched and unverified:
        status = "unverified"
    elif unverified or evidence_unverified:
        status = "matched_with_unverified"
    else:
        status = "matched"
    return CapabilityMatch(
        matched=tuple(matched),
        optional_absent=tuple(optional_absent),
        unused_device_capabilities=unused,
        unverified=tuple(unverified),
        deferred=tuple(deferred),
        status=status,
    )


def _declared_fact(
    device: DeviceSnapshot,
    capability: DeviceCapability,
    requirement: Requirement,
) -> Any:
    """Return a device fact for a threshold, or ``None`` when none is declared."""

    property_name = requirement.id.rsplit(".", 1)[-1]
    if property_name in device.attributes:
        value = device.attributes[property_name]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        return None
    if property_name in capability.parameters:
        value = capability.parameters[property_name]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


# ---------------------------------------------------------------------------
# Deterministic plan
# ---------------------------------------------------------------------------


def _recipe_slug(scenario_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", scenario_id.lower()).strip("-")
    return f"{slug[:48]}-reference"


def build_reference_recipe(
    device: DeviceSnapshot,
    scenario: ScenarioRequirements,
    match: CapabilityMatch,
) -> Recipe:
    """Build the reference recipe through the existing recipe model.

    The plan is a two-action reference skeleton, not a task solution: a ``HOME``
    step when the scenario requires an arm capability and a ``GRIPPER`` step
    when it requires a gripper capability, in that fixed order. No success
    threshold is encoded, and the constants are deliberately conservative.
    """

    matched_ids = {item["id"] for item in match.matched}
    interfaces = {
        item["interface"]
        for item in (*match.matched, *match.unverified)
        if item.get("interface_ok") is True and "interface" in item
    }
    arm_ready = "arm.position_control" in interfaces or any(
        capability_id.startswith("manipulation.arm.")
        for capability_id in matched_ids
    )
    gripper_ready = "end_effector.position_control" in interfaces or (
        "manipulation.gripper.aperture_m" in matched_ids
    )
    steps: list[dict[str, Any]] = []
    if arm_ready:
        steps.append(
            {
                "name": "home",
                "kind": "HOME",
                "profile": "step",
                "scopes": ["arm"],
                "params": dict(_HOME_PARAMS),
            }
        )
    if gripper_ready:
        steps.append(
            {
                "name": "prepare-gripper",
                "kind": "GRIPPER",
                "profile": "gripper",
                "scopes": ["gripper"],
                "params": dict(_GRIPPER_PARAMS),
            }
        )
    if not steps:
        _fail(
            "PLAN",
            "no executable capability matched; the reference can only plan "
            "arm and gripper capabilities",
        )
    scopes: list[str] = []
    for step in steps:
        for scope in step["scopes"]:
            if scope not in scopes:
                scopes.append(scope)
    recipe_mapping = {
        "name": _recipe_slug(scenario.scenario_id),
        "version": RECIPE_VERSION,
        "description": (
            f"Offline reference plan for scenario {scenario.scenario_id}@"
            f"{scenario.scenario_version}, task {scenario.task_id}, evaluation "
            f"{scenario.evaluation_id}, on {device.source_device}@"
            f"{device.device_version}. Mock-only reference actions; this is "
            "not a task solution and declares no success criterion."
        ),
        "contract_hash": CONTRACT_HASH,
        "scopes": scopes,
        "steps": steps,
    }
    try:
        return recipe_from_mapping(recipe_mapping, source="reference_recipe")
    except RecipeError as exc:
        _fail("PLAN", str(exc))


def plan_document(plan: Any, scenario: ScenarioRequirements) -> dict[str, Any]:
    """Serialize a plan with the scenario, task and evaluation identity."""

    return {
        "schema": PLAN_SCHEMA,
        "data_source": SAMPLE_DATA_SOURCE,
        "scenario_id": scenario.scenario_id,
        "scenario_version": scenario.scenario_version,
        "task_id": scenario.task_id,
        "evaluation_id": scenario.evaluation_id,
        "run_id": plan.run_id,
        "recipe": {
            "name": plan.recipe_name,
            "version": plan.recipe_version,
            "contract_hash": plan.contract_hash,
            "scopes": list(plan.required_scopes),
        },
        "step_count": len(plan.steps),
        "order": [step.name for step in plan.steps],
        "steps": [
            {
                "index": step.index,
                "name": step.name,
                "kind": step.kind,
                "profile": step.profile,
                "scopes": list(step.scopes),
                "params": dict(step.params),
                "idempotency_key": step.idempotency_key,
            }
            for step in plan.steps
        ],
        "facts": {},
        "branches": [],
    }


# ---------------------------------------------------------------------------
# Mock execution: existing loopback gateway, lease and linear runner
# ---------------------------------------------------------------------------


def _normalise_path(path: str) -> str:
    return re.sub(r"/v1/leases/[^/]+", "/v1/leases/{lease}", path)


def _request_counts(entries: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in entries:
        key = f"{item['method']} {_normalise_path(str(item['path']))}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _report_steps(report: RunReport) -> list[dict[str, Any]]:
    return [
        {
            "index": step.index,
            "name": step.name,
            "outcome": step.outcome,
            "reason_code": step.reason_code,
            "bus_verified": step.bus_verified,
            "intent_id": step.intent_id,
        }
        for step in report.steps
    ]


@dataclass(frozen=True)
class MockRun:
    run_label: str
    result: SupervisedResult
    request_counts: Mapping[str, int]

    def summary(self) -> dict[str, Any]:
        report = self.result.report
        return {
            "run": self.run_label,
            "state": report.state.value,
            "abort_reason": report.abort_reason,
            "stop_error": report.stop_error,
            "stop_confirmation": report.stop_confirmation,
            "physical_stop": report.physical_stop,
            "operator_required": report.operator_required,
            "steps": _report_steps(report),
            "request_counts": dict(self.request_counts),
        }

    def lease_check(self) -> dict[str, Any]:
        return {
            "run": self.run_label,
            "acquired": bool(self.result.lease.lease_id),
            "released": self.result.lease_released,
            "lost": self.result.lease_error is not None,
            "lease_error": self.result.lease_error,
            "release_error": self.result.release_error,
            "record_written": self.result.record_path is not None
            and self.result.record_error is None,
        }


def _authorization(recipe: Recipe, session: str) -> Authorization:
    return Authorization(
        operator=MOCK_OPERATOR,
        session_id=session,
        scopes=frozenset(recipe.scopes),
        approved=True,
        approved_at=MOCK_APPROVED_AT,
    )


def run_mock_once(
    recipe: Recipe,
    *,
    run_id: str,
    label: str,
    fail_first_step: bool,
) -> MockRun:
    """Run one recipe through the mock gateway, lease and linear runner."""

    with tempfile.TemporaryDirectory(prefix="jcheng-reference-store-") as store_dir:
        with MockGatewayServer() as server:
            if fail_first_step:
                server.probe.results.append(
                    completed(
                        outcome="REJECTED",
                        code="REFERENCE_INJECTED_REJECTION",
                        bus_verified=False,
                    )
                )
            endpoint = GatewayEndpoint(
                base_url=f"http://127.0.0.1:{server.port}",
                token=MOCK_TOKEN,
                timeout_s=2.0,
            )
            gateway = HttpGateway(endpoint, operator_id=MOCK_OPERATOR)
            store = RunStore(Path(store_dir) / "store")
            result = run_supervised(
                gateway,
                recipe,
                _authorization(recipe, f"reference-session-{run_id}"),
                run_id=run_id,
                store=store,
                recipe_hash=content_hash(
                    {
                        "name": recipe.name,
                        "version": recipe.version,
                        "contract_hash": recipe.contract_hash,
                    }
                ),
                evidence_class="L2",
            )
            request_counts = _request_counts(server.probe.request_log)
    return MockRun(run_label=label, result=result, request_counts=request_counts)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail("MOCK_EXECUTION", message)


def run_mock_reference(recipe: Recipe, plan: Any) -> dict[str, Any]:
    """Execute the plan and one injected failure, then validate fail-closed."""

    happy = run_mock_once(
        recipe,
        run_id=RUN_ID,
        label="happy-path",
        fail_first_step=False,
    )
    failure = run_mock_once(
        recipe,
        run_id=FAILURE_RUN_ID,
        label="failure-path",
        fail_first_step=True,
    )

    _require(
        happy.result.report.state.value == "SUCCEEDED",
        f"the reference plan did not succeed on the mock: "
        f"{happy.result.report.state.value}",
    )
    _require(
        len(happy.result.report.steps) == len(plan.steps),
        "the mock executed a different number of steps than the plan",
    )
    _require(
        all(
            step.outcome == "COMPLETED" and step.bus_verified
            for step in happy.result.report.steps
        ),
        "a mock step did not report COMPLETED with a verified bus read",
    )
    _require(
        failure.result.report.state.value == "FAILED",
        f"the injected rejection produced {failure.result.report.state.value}, "
        "not FAILED",
    )
    _require(
        len(failure.result.report.steps) == 1,
        "the runner submitted a step after the rejected step",
    )
    _require(
        failure.request_counts.get("POST /v1/motion/execute") == 1,
        "the failure path submitted more than one intent",
    )
    _require(
        failure.request_counts.get("POST /v1/motion/stop") == 1,
        "the failure path did not request exactly one stop",
    )
    _require(
        happy.request_counts.get("POST /v1/motion/stop", 0) == 0,
        "the happy path requested a stop",
    )
    for run in (happy, failure):
        check = run.lease_check()
        _require(check["acquired"], f"{run.run_label}: no lease was acquired")
        _require(check["released"], f"{run.run_label}: the lease was not released")
        _require(not check["lost"], f"{run.run_label}: the lease was reported lost")
        _require(
            check["record_written"],
            f"{run.run_label}: the supervised record was not written",
        )
    _require(
        failure.result.report.stop_confirmation == "request_accepted",
        "the stop confirmation was not the expected S1 request_accepted",
    )
    _require(
        failure.result.report.physical_stop == "unverified",
        "the reference must not claim a physical stop",
    )

    return {
        "happy_path": happy.summary(),
        "failure_path": failure.summary(),
        "lease_checks": [happy.lease_check(), failure.lease_check()],
        "stop_checks": {
            "failure_path_stops": failure.request_counts.get(
                "POST /v1/motion/stop", 0
            ),
            "happy_path_stops": happy.request_counts.get(
                "POST /v1/motion/stop", 0
            ),
            "confirmation": failure.result.report.stop_confirmation,
            "physical_stop": failure.result.report.physical_stop,
            "stop_error": failure.result.report.stop_error,
        },
    }

# ---------------------------------------------------------------------------
# Synthetic samples and benchmark
# ---------------------------------------------------------------------------


def synthetic_samples(
    device: DeviceSnapshot,
    scenario: ScenarioRequirements,
    mock_results: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """One explicitly synthetic sample per executed mock step."""

    samples: list[dict[str, Any]] = []
    for run_label, result_key in (
        ("happy-path", "happy_path"),
        ("failure-path", "failure_path"),
    ):
        run = mock_results[result_key]
        for step in run["steps"]:
            success = step["outcome"] == "COMPLETED" and step["bus_verified"] is True
            samples.append(
                {
                    "sample_id": f"{run_label}-{step['index']:02d}-{step['name']}",
                    "trial": run_label,
                    "data_source": SAMPLE_DATA_SOURCE,
                    "data_kind": "mock_step",
                    "device": device.source_device,
                    "device_version": device.device_version,
                    "scenario_id": scenario.scenario_id,
                    "scenario_version": scenario.scenario_version,
                    "task_id": scenario.task_id,
                    "evaluation_id": scenario.evaluation_id,
                    "step": step["name"],
                    "outcome": step["outcome"],
                    "bus_verified": step["bus_verified"],
                    "success": success,
                }
            )
    return samples


def _benchmark_suite(samples: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "suite": "armpi-ore-sorting-reference-mock-steps",
        "title": "ArmPi Ultra ore sorting reference mock step completion",
        "level": "L2",
        "data_source": SAMPLE_DATA_SOURCE,
        "description": (
            "Offline reference only: mock step completion from the "
            "ArmPi Ultra x ore_sorting reference. It is not ore-sorting task "
            "success and it declares no threshold."
        ),
        "min_samples": 1,
        "metrics": [
            {
                "id": "mock_step_completion",
                "kind": "success_rate",
                "source": "success",
                "unit": "ratio",
            }
        ],
        "thresholds": [],
        "limitations": [
            "Synthetic mock step outcomes, not a task success rate.",
            "No threshold is declared; the report is informational.",
            "No hardware, simulation or recorded data is involved.",
        ],
    }


def _sha256_file(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def run_benchmark(
    benchmark_root: Path | None,
    out_dir: Path,
    suite: Mapping[str, Any],
    samples_path: Path,
    sample_count: int,
) -> dict[str, Any]:
    """Run the benchmark CLI in a subprocess, or report it as skipped."""

    if benchmark_root is None:
        return {
            "benchmark_executed": False,
            "status": "skipped",
            "reason": "--benchmark-root was not provided; no benchmark was run",
        }
    root = benchmark_root.resolve()
    if not (root / "src" / "jcheng_benchmark").is_dir():
        _fail(
            "BENCHMARK",
            f"{root}: src/jcheng_benchmark is missing; point --benchmark-root at "
            "the jcheng-benchmark repository",
        )

    suite_path = out_dir / "benchmark_suite.yaml"
    suite_path.write_text(
        yaml.safe_dump(dict(suite), sort_keys=False), encoding="utf-8"
    )
    json_report = out_dir / "benchmark_report.json"
    markdown_report = out_dir / "benchmark_report.md"
    command = [
        sys.executable,
        "-m",
        "jcheng_benchmark",
        "run",
        "--spec",
        str(suite_path),
        "--data",
        str(samples_path),
        "--json",
        str(json_report),
        "--markdown",
        str(markdown_report),
        "--operator",
        "reference-offline",
        "--host",
        "offline-reference",
        "--note",
        "Synthetic reference data; not hardware, not ore-sorting task success.",
    ]
    # Record a root-relative command so the reference report stays reproducible;
    # the subprocess itself receives the absolute artifact paths.
    recorded_command = [
        "-m",
        "jcheng_benchmark",
        "run",
        "--spec",
        suite_path.name,
        "--data",
        samples_path.name,
        "--json",
        json_report.name,
        "--markdown",
        markdown_report.name,
        "--operator",
        "reference-offline",
        "--host",
        "offline-reference",
        "--note",
        "Synthetic reference data; not hardware, not ore-sorting task success.",
    ]
    env = dict(os.environ)
    python_path = str(root / "src")
    if env.get("PYTHONPATH"):
        python_path = python_path + os.pathsep + env["PYTHONPATH"]
    env["PYTHONPATH"] = python_path

    result: dict[str, Any] = {
        "benchmark_executed": True,
        "benchmark_root": str(root),
        "status": "failed",
        "suite": {
            "id": suite["suite"],
            "path": suite_path.name,
            "sha256": _sha256_file(suite_path),
        },
        "dataset": {
            "path": samples_path.name,
            "sha256": _sha256_file(samples_path),
            "samples": sample_count,
        },
        "report_path": json_report.name,
        "command": recorded_command,
    }
    try:
        completed_process = subprocess.run(
            command,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=BENCHMARK_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result["exit_code"] = None
        result["reason"] = (
            f"the benchmark CLI did not finish within {BENCHMARK_TIMEOUT_S}s"
        )
        return result
    except OSError as exc:
        result["exit_code"] = None
        result["reason"] = f"the benchmark CLI could not start: {exc}"
        return result

    result["exit_code"] = completed_process.returncode
    if completed_process.returncode not in (0, 1, 2) or not json_report.is_file():
        result["stdout_tail"] = completed_process.stdout[-4000:]
        result["stderr_tail"] = completed_process.stderr[-4000:]
        result["reason"] = (
            "the benchmark CLI did not produce a report"
            if not json_report.is_file()
            else f"the benchmark CLI exited {completed_process.returncode}"
        )
        return result

    try:
        benchmark_report = json.loads(json_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result["reason"] = f"the benchmark report is not readable JSON: {exc}"
        return result
    if not isinstance(benchmark_report, Mapping):
        result["reason"] = "the benchmark report is not a JSON object"
        return result

    reported_result = benchmark_report.get("result")
    if reported_result not in {"pass", "fail", "inconclusive"}:
        result["reason"] = (
            f"the benchmark report has an unexpected result {reported_result!r}"
        )
        return result
    result["status"] = reported_result
    result["result"] = reported_result
    result["result_reason"] = benchmark_report.get("result_reason")
    result["metrics"] = benchmark_report.get("metrics")
    dataset = benchmark_report.get("dataset")
    suite_section = benchmark_report.get("suite")
    if isinstance(dataset, Mapping):
        result["verified_dataset_sha256"] = dataset.get("sha256")
        if dataset.get("sha256") != result["dataset"]["sha256"].removeprefix(
            "sha256:"
        ):
            result["status"] = "failed"
            result["reason"] = (
                "the benchmark report hashed a different dataset than the "
                "reference wrote; the result is not traceable"
            )
            return result
    if isinstance(suite_section, Mapping):
        result["verified_suite_sha256"] = suite_section.get("sha256")
        if suite_section.get("sha256") != result["suite"]["sha256"].removeprefix(
            "sha256:"
        ):
            result["status"] = "failed"
            result["reason"] = (
                "the benchmark report hashed a different suite than the "
                "reference wrote; the result is not traceable"
            )
            return result
    if reported_result == "fail":
        result["reason"] = "the benchmark report failed its own thresholds"
    else:
        result["reason"] = (
            "the benchmark executed and its report is recorded as "
            f"{reported_result}"
        )
    return result


# ---------------------------------------------------------------------------
# Simulation check
# ---------------------------------------------------------------------------


def check_simulation_assets(
    scenario: ScenarioRequirements, simulation_root: Path | None
) -> dict[str, Any]:
    """Check declared simulation asset references; never run the plant."""

    if not scenario.simulation_assets:
        return {
            "status": "not_checked",
            "reason": "scenario requirements declare no simulation assets",
            "assets": [],
        }
    if simulation_root is None:
        return {
            "status": "not_checked",
            "reason": "--simulation-root was not provided",
            "assets": [
                {"id": asset.id, "path": asset.path, "exists": None}
                for asset in scenario.simulation_assets
            ],
        }
    root = simulation_root.resolve()
    if not root.is_dir():
        _fail("SIMULATION", f"{root}: --simulation-root is not a directory")
    assets: list[dict[str, Any]] = []
    missing: list[str] = []
    for asset in scenario.simulation_assets:
        target = (root / asset.path).resolve()
        if target != root and root not in target.parents:
            _fail(
                "SIMULATION",
                f"asset {asset.id!r} path {asset.path!r} escapes the simulation root",
            )
        exists = target.is_file()
        assets.append({"id": asset.id, "path": asset.path, "exists": exists})
        if not exists:
            missing.append(asset.path)
    if missing:
        return {
            "status": "missing",
            "reason": "declared simulation asset(s) are missing: "
            + ", ".join(missing),
            "root": str(root),
            "assets": assets,
        }
    return {
        "status": "ok",
        "reason": "every declared simulation asset exists under the root",
        "root": str(root),
        "assets": assets,
    }


# ---------------------------------------------------------------------------
# Report assembly and CLI
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_samples(path: Path, samples: Sequence[Mapping[str, Any]]) -> None:
    lines = [
        json.dumps(sample, sort_keys=True, separators=(",", ":"))
        for sample in samples
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_devices(
    specs: Sequence[str],
) -> tuple[DeviceSnapshot | Mapping[str, DeviceSnapshot], bool]:
    """Load one legacy snapshot or a role-keyed cell composition."""

    if not specs:
        _fail("INPUT", "at least one --device-snapshot is required")
    parsed: list[tuple[str | None, Path]] = []
    for spec in specs:
        if "=" in spec:
            role, raw_path = spec.split("=", 1)
            if role in _ROLES:
                parsed.append((role, Path(raw_path)))
                continue
        parsed.append((None, Path(spec)))
    if len(parsed) > 1 and any(role is None for role, _ in parsed):
        _fail(
            "INPUT",
            "multiple snapshots need an explicit role: --device-snapshot ROLE=PATH",
        )
    if len(parsed) == 1 and parsed[0][0] is None:
        return device_snapshot_from_mapping(load_document(parsed[0][1])), False

    devices: dict[str, DeviceSnapshot] = {}
    for role, path in parsed:
        assert role is not None
        if role in devices:
            _fail("INPUT", f"duplicate composition role {role!r}")
        device = device_snapshot_from_mapping(load_document(path))
        if device.role is not None and device.role != role:
            _fail(
                "INPUT",
                f"snapshot {device.source_device!r} declares role "
                f"{device.role!r}, not {role!r}",
            )
        devices[role] = device
    return devices, True


def run_reference(args: argparse.Namespace) -> int:
    device_or_devices, cell = _load_devices(args.device_snapshot)
    if isinstance(device_or_devices, DeviceSnapshot):
        device = device_or_devices
        devices: dict[str, DeviceSnapshot] = {}
    else:
        devices = dict(device_or_devices)
        device = devices.get("arm") or next(iter(devices.values()))
    scenario_document = load_document(Path(args.scenario_requirements))
    scenario = scenario_requirements_from_mapping(scenario_document)

    match = match_capabilities(device_or_devices, scenario)
    recipe = build_reference_recipe(device, scenario, match)
    plan = build_plan(recipe, RUN_ID)
    plan_payload = plan_document(plan, scenario)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    simulation_check = check_simulation_assets(
        scenario,
        Path(args.simulation_root) if args.simulation_root else None,
    )
    mock_results = run_mock_reference(recipe, plan)
    samples = synthetic_samples(device, scenario, mock_results)
    _write_samples(out_dir / "synthetic_samples.jsonl", samples)
    _write_json(out_dir / "plan.json", plan_payload)

    benchmark = run_benchmark(
        Path(args.benchmark_root) if args.benchmark_root else None,
        out_dir,
        _benchmark_suite(len(samples)),
        out_dir / "synthetic_samples.jsonl",
        len(samples),
    )

    report = {
        "schema": REPORT_SCHEMA,
        "data_source": SAMPLE_DATA_SOURCE,
        "reference": {
            "name": "armpi-ultra-ore-sorting",
            "kind": "offline-mock",
            "run_id": RUN_ID,
        },
        "device": {
            "source_device": device.source_device,
            "device_version": device.device_version,
            "source_hash": device.source_hash,
            "content_hash": device.content_hash,
            "generated_by": device.generated_by,
            "source_path": device.source_path,
            "joint_count": len(device.joints),
            "joint_names": [joint.name for joint in device.joints],
            "attributes": dict(device.attributes),
            "safety_overlay": dict(device.safety_overlay),
        },
        "scenario_requirements": {
            "scenario_id": scenario.scenario_id,
            "scenario_version": scenario.scenario_version,
            "content_hash": scenario.content_hash,
            "task_id": scenario.task_id,
            "evaluation_id": scenario.evaluation_id,
            "constraint_count": len(scenario.constraints),
            "provenance": dict(scenario.provenance),
        },
        "cell": {
            "composed": cell,
            "roles": sorted(devices),
        },
        "acceptance_gates": {
            "artifact_valid": True,
            "capability_match_status": match.status,
            "offline_execution_allowed": match.status
            in {"matched", "matched_with_unverified"},
            "deployment_ready": False,
            "motion_authorized": False,
        },
        "capability_match": match.report(device_or_devices, scenario),
        "plan": plan_payload,
        "task_evaluation": {
            "evaluated": False,
            "reason": (
                "the reference executes a mock step plan; it does not evaluate "
                "ore-sorting task success and declares no success criterion"
            ),
        },
        "mock_results": {
            "happy_path": mock_results["happy_path"],
            "failure_path": mock_results["failure_path"],
        },
        "lease_checks": mock_results["lease_checks"],
        "stop_checks": mock_results["stop_checks"],
        "simulation_check": simulation_check,
        "benchmark": benchmark,
        "authorization": {
            "kind": "synthetic-reference",
            "operator": MOCK_OPERATOR,
            "scopes": list(recipe.scopes),
            "approved_at": MOCK_APPROVED_AT,
        },
        "limitations": [
            "Synthetic offline reference: no hardware, DDS, ROS or device access.",
            "Mock step completion is not ore-sorting task success; no success "
            "threshold is evaluated.",
            "Device capability values come from generated declarative snapshots, "
            "not measurements.",
            "A requirement with no declared device fact is reported as "
            "unverified and is never promoted to matched.",
            "Algorithm requirements are deferred to their owner; no perception "
            "code runs.",
            "The simulation check only verifies declared asset paths; no plant "
            "is launched and no domain is joined.",
            "A skipped benchmark is recorded as skipped; no benchmark result is "
            "invented.",
        ],
    }
    _write_json(out_dir / "reference_report.json", report)

    if simulation_check["status"] == "missing":
        print(
            "armpi-ore-sorting-reference: SIMULATION: "
            + str(simulation_check["reason"]),
            file=sys.stderr,
        )
        return 1
    if benchmark["status"] in {"failed", "fail"}:
        print(
            "armpi-ore-sorting-reference: BENCHMARK: "
            + str(benchmark.get("reason", "benchmark failed")),
            file=sys.stderr,
        )
        return 1
    print(
        "armpi-ore-sorting-reference: "
        f"{'+'.join(sorted(devices)) or device.source_device} x "
        f"{scenario.scenario_id}@{scenario.scenario_version}: "
        f"{len(plan.steps)} plan step(s), mock {mock_results['happy_path']['state']}, "
        f"simulation {simulation_check['status']}, "
        f"benchmark {benchmark['status']}"
    )
    print(f"report: {out_dir / 'reference_report.json'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="armpi-ore-sorting-reference",
        description=(
            "Offline, repeatable ArmPi Ultra x ore_sorting reference: validate "
            "pinned artifacts, match capabilities, plan and execute against the "
            "loopback mock gateway. No device, DDS or ROS access."
        ),
    )
    parser.add_argument(
        "--device-snapshot",
        action="append",
        required=True,
        metavar="[ROLE=]PATH",
        help=(
            "device snapshot YAML/JSON; repeat with ROLE=PATH to compose a "
            "cell (arm, gripper, camera, base, sensor, teleop)"
        ),
    )
    parser.add_argument(
        "--scenario-requirements",
        required=True,
        help="scenario requirements YAML/JSON",
    )
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument(
        "--benchmark-root",
        help="jcheng-benchmark repository root; its CLI is invoked offline",
    )
    parser.add_argument(
        "--simulation-root",
        help="jcheng-simulation repository root for declared asset checks",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run_reference(args)
    except ReferenceCheckError as exc:
        print(f"armpi-ore-sorting-reference: error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"armpi-ore-sorting-reference: error: OUTPUT: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
