# Recipes and runs — jcheng-applications

> Status: **v0.1.0** as of 2026-11-26. The recipe format below is implemented
> and enforced by `src/jcheng_applications/model.py`. Execution is not: there
> is no gateway transport until M2/M3.

## 1. What a recipe is

A recipe is an operator-authored, versioned sequence of intent steps. It is
data, not code: it cannot open a device, join a DDS graph, call a ROS service or
execute motion. The application validates it, plans it and — from M3 — asks the
gateway to evaluate each step.

A recipe is also not a hardware program. It becomes one only when it is written
against a robot profile and calibration record, and executed in a separately
authorized session under the system layer's lease, limits and stop latch.

## 2. Top-level fields

A recipe is a YAML mapping with these fields; `facts` and `branches` are
optional (format 0.2):

| Field | Type | Rule |
|---|---|---|
| `name` | string | `^[a-z0-9][a-z0-9-]{0,63}$` |
| `version` | string | `MAJOR.MINOR.PATCH`; recipe-local, independent of the repository version |
| `description` | string | non-empty; state the intent and any calibration dependency |
| `contract_hash` | string | must equal the pinned v0.1.0 contract hash |
| `scopes` | list | non-empty subset of `arm`, `gripper`, `base`, `full`; must equal the union of **main and branch** step scopes; `full` cannot be combined |
| `steps` | list | 1 to 64 main steps; names unique across main and branches |
| `facts` | mapping | optional; named measured-position facts (see §4.1) |
| `branches` | list | optional; declared branches (see §4.1) |

Unknown fields are rejected. The loader does not ignore a typo.

## 3. Step fields

Each step has exactly:

| Field | Type | Rule |
|---|---|---|
| `name` | string | unique within the recipe |
| `kind` | enum | `JOINT_TRAJECTORY`, `JOINT_STEP`, `POSE`, `HOME`, `BASE_PULSE` or `GRIPPER` |
| `profile` | enum | `moveit`, `step`, `pose`, `fast`, `base` or `gripper` |
| `scopes` | list | non-empty subset of the recipe scopes; `full` cannot be combined |
| `params` | mapping | the kind payload plus optional common parameters |

The recipe does not choose a robot, a gateway URL, a token or a lease. Those
are session and deployment inputs.

## 4. Kinds and payloads

| Kind | Required payload | Notes |
|---|---|---|
| `JOINT_TRAJECTORY` | `trajectory` | `joint_names` plus non-empty `points`; each point has `positions`, `velocities`, `accelerations` and `time_from_start`, with one value per joint |
| `JOINT_STEP` | `joint_names`, `target_positions` | equal lengths; optional `max_velocity_rad_s`, `duration_s`. The deployed system profile accepts **one joint per step** (see the note below) |
| `POSE` | `target_pose`, `tip_link`, `frame_id` | `target_pose` has `position {x,y,z}` and `orientation {x,y,z,w}` |
| `HOME` | none | optional common parameters only |
| `BASE_PULSE` | `base_pulse` | `{duty, duration_s, require_stopped_after}`; 1 to 4 duty values in `[-1, 1]` |
| `GRIPPER` | `gripper` | `{position_rad, max_velocity_rad_s, duration_s, verify_bus}`; `position_rad` in `[0, 2.22]` |

Common optional parameters: `max_velocity_rad_s`, `max_acceleration_rad_s`,
`duration_s`, `verify_bus`, `require_measured_state`, `expected_profile_hash`.
All numbers must be finite; `NaN` and infinity are rejected.

**Deployment note (M3).** The deployed `jcheng-system` profile accepts exactly
one joint per `JOINT_STEP`; a multi-joint command is rejected at the gateway
with `INVALID_COMMAND`. The validator permits a list for offline planning, so
a multi-joint step is syntactically valid but not executable against the
current profile. Split multi-joint moves into one step per joint. The shipped
offline example is multi-joint and is not a hardware program.

A step's scope must match the command it carries: `JOINT_TRAJECTORY`,
`JOINT_STEP`, `POSE` and `HOME` use `arm`; `BASE_PULSE` uses `base`; `GRIPPER`
uses `gripper`. Any of them may use `full` instead, but `full` cannot be
combined with another scope.

### 4.1 Facts and declared branches (format 0.2)

A fact is a threshold on one measured position of a completed **main** step:

```yaml
facts:
  joint1_moved:
    from_step: approach-j1
    measured:
      index: 0
      at_least_rad: 0.02
```

- `from_step` must name a main step, not a branch step.
- `measured.index` is a zero-based index into `MotionResult.measured_positions`.
  The v0.1 gateway contract is positional: it carries no joint names and no
  gripper measurement. A fact over a named joint or the gripper needs a
  contract change; until then, recipe authors must use the profile order.
- At least one of `at_least_rad` / `at_most_rad` is required; both may be set.
- A fact is `TRUE`/`FALSE` only when the source step completed with a verified
  bus read and the index exists. Missing, unverified or out-of-range input is
  `UNKNOWN`, which never selects a branch.

A branch is a declared alternative anchored after a main step:

```yaml
branches:
  - id: adjust-j1.v1
    after: approach-j1
    required: false
    priority: 10
    when:
      - fact: joint1_moved
        equals: true
    steps:
      - name: adjust-j1
        kind: JOINT_STEP
        profile: step
        scopes: [arm]
        params: { ... }
```

- `id` matches `^[a-z0-9][a-z0-9.-]{0,63}$` and is unique.
- `after` names a main step; a branch cannot anchor on another branch.
- `when` is a non-empty list of `{fact, equals}`; every named fact must be
  declared and measured at or before the anchor.
- Branch step names are unique across the main plan and every branch. Branch
  steps get their own idempotency keys (`branch_id`, `branch_index`) and their
  scopes join the recipe scope union, so the operator authorization must cover
  them.
- Selection happens only after the anchor step returns `CONTINUE`; an execution
  failure stops the run before any branch is considered. All conditions must be
  `PASS`; the selector orders eligible branches by `(-priority, id)` and records
  every candidate and guard.
- `required: true` with no eligible candidate aborts the run with one stop (the
  linear-engine approximation of `WAITING_FOR_OPERATOR`). `required: false`
  continues the main plan.
- Both engines execute declared branches. The tree engine evaluates the
  selector once per anchor and ticks only the selected branch; a selected
  branch failure is not swallowed by the fallback.
- A Derived Plan is a new recipe revision, run id and authorization; it is never
  a mutation of a live plan.

See [`recipes/example-declared-branch.yaml`](../recipes/example-declared-branch.yaml).

## 5. Bus verification

Completion truth for the arm and gripper comes from a bus read, not from a
command cache. Therefore:

- A step with `arm`, `gripper` or `full` scope must require a verified bus read.
  For `JOINT_TRAJECTORY`, `JOINT_STEP`, `POSE` and `HOME`, set top-level
  `verify_bus: true`.
- For `GRIPPER`, set `gripper.verify_bus: true`.
- A `COMPLETED` result without `bus_verified: true` makes the run
  `INDETERMINATE` for those scopes.
- A `BASE_PULSE` does not require bus verification. The platform has no wheel
  encoders, so the base result cannot be bus-verified in the same sense; the
  pulse contract uses `require_stopped_after` instead.

## 6. Idempotency

For each step the planner computes:

```text
sha256(canonical_json({
  run_id, index, name, kind, profile, params
}))
```

The key is 64 lowercase hex characters. It is stable for a given run and
payload and changes when any of those fields changes. The command carries this
key in `idempotency_key` and as the gateway's `Idempotency-Key` header (M3).
`intent_id` is derived from the first 24 characters of the key, so a retried
submission is recognisable as the same intent.

`run_id` must start with an alphanumeric character and contain only
`A-Z a-z 0-9 . _ : + -` up to 128 characters. The caller supplies it; the core
never generates a random or time-based id.

## 7. Authorization

A run requires an `Authorization` record with:

| Field | Rule |
|---|---|
| `operator` | non-empty; the named human who approved this session |
| `session_id` | non-empty; unique per authorization |
| `scopes` | non-empty subset of `arm`, `gripper`, `base`, `full`; must cover every recipe scope |
| `approved` | exactly `true`; a truthy string is not approval |
| `approved_at` | ISO 8601 with an explicit timezone |

The record is supplied by the caller. In the ecosystem, `jcheng-studio` owns the
human-facing authorization interaction; this repository neither authenticates
the operator nor stores a token. A record from a previous session is not reused.

## 8. Run states

| Gateway result | Run state | Behavior |
|---|---|---|
| `COMPLETED` and bus verified where required | continue; `SUCCEEDED` after the last step | next step |
| `COMPLETED` without required bus verification | `INDETERMINATE` | one stop request; no retry |
| `REJECTED` | `FAILED` | one stop request |
| `ABORTED` | `ABORTED` | one stop request |
| `TIMEOUT` / `UNKNOWN` | `INDETERMINATE` | one stop request; operator inspection |
| Safety state `STOP_LATCHED`, `FAULT` or `DISARMED`, or `stop_latched` | `ABORTED` | one stop request |
| Submission exception | `INDETERMINATE` | one stop request; the outcome is unknown |
| Malformed result | `INDETERMINATE` | one stop request |
| Preflight refusal | `ABORTED` | nothing submitted, no stop request |

A run report preserves the gateway's `outcome`, `reason_code`, `message` and
`intent_id` for every step it reached. It never invents a missing value.

## 9. Example

[`recipes/example-supervised-transfer.yaml`](../recipes/example-supervised-transfer.yaml)
is an uncalibrated two-step example: a joint step and a gripper close. It exists
to exercise validation, planning and tests. It is not authorized for hardware
and its joint names and positions are placeholders.

```bash
jcheng-applications validate recipes/example-supervised-transfer.yaml
jcheng-applications plan recipes/example-supervised-transfer.yaml \
  --run-id demo-001 --json
```

## 10. Versioning and change rules

- A recipe's `version` is independent of `VERSION` and of `jcheng-interfaces`.
- Every recipe pins `contract_hash`. A contract change requires a new recipe
  version and a matching update in `jcheng-interfaces`.
- Adding a kind, a profile, a common parameter or a payload rule is a recipe
  contract change: update this document, the model, the tests and the
  `CHANGELOG.md` together.
- A recipe that was never executed on hardware must not be described as
  validated. Mock and replay results stay labelled.

## 11. Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — components, state machine and invariants.
- [`INTEGRATION.md`](INTEGRATION.md) — gateway contract, admission and failure semantics.
- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — `MotionCommand`, `MotionResult` and the contract hash.
- [README](../README.md) / [ROADMAP](../ROADMAP.md) / [ACCEPTANCE](../ACCEPTANCE.md).
