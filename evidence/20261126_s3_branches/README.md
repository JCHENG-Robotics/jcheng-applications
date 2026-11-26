# S3 declared branches — evidence

> Date: 2026-11-26. Level: **L3 for the end-to-end branch run** (real
> `jcheng-system` gateway process + `MockStack`), L2 for validation/selection
> tests. No robot, no physical motion claim.
> Scope: **S3 v0 on the linear engine, positional arm facts**. The tree engine
> refuses a recipe with facts/branches until branch support lands.

## 1. What was built

- Recipe format 0.2 (additive): optional `facts` and `branches`. A fact is a
  threshold on one positional `measured_positions[index]` from a **main** step.
  A branch is anchored after a main step and has a non-empty `when` list of
  `{fact, equals}` conditions.
- Plan compilation: branch steps get their own `branch_id`/`branch_index`
  idempotency keys and a global report index; branch scopes join the recipe's
  scope union and therefore the authorization.
- Three-valued facts: `TRUE`/`FALSE`/`UNKNOWN`. A fact is `UNKNOWN` unless its
  source step completed with a verified bus read and the measured index exists.
- Selector: only branches anchored after the current main step; all conditions
  must be `PASS`; order by `(-priority, id)`; a selection record is always
  emitted with every candidate and guard.
- Runner: branches are evaluated only after a `CONTINUE`; execution failure
  never selects a branch. A `required` branch with no eligible candidate is
  `ABORTED` with one stop (v0 linear approximation of `WAITING_FOR_OPERATOR`).
- The tree engine refuses a facts/branches recipe; declared branches are
  linear-engine only in this stage.

## 2. L3 run

Recipe: [example-declared-branch.yaml](../../recipes/example-declared-branch.yaml).
The main step moves joint1 to 0.03 rad; the fact is `index 0 >= 0.02`; the
branch moves it to 0.05 rad.

```text
approach-j1  COMPLETED  measured_positions [0.03, 0.0, 0.0, 0.0, 0.0]
adjust-j1    COMPLETED  measured_positions [0.05, 0.0, 0.0, 0.0, 0.0]

selection after approach-j1:
  candidates: [{branch_id: adjust-j1.v1, guard: PASS, selected: true}]
  selected: adjust-j1.v1
```

State `SUCCEEDED`, lease released, `record_error: null`.

- [`cli-success.json`](cli-success.json)
- [`report-summary.json`](report-summary.json) — sanitized `run_started` and
  `run_finished`
- [`verify.txt`](verify.txt) — external chain verified, file 0600

The raw JSONL stays outside the repository (`/tmp/s3-runs`) because the gate
rejects `*.jsonl`.

## 3. Tests

| Run | Result |
|---|---|
| With the tree extra | `160 passed` — [`pytest-tree.txt`](pytest-tree.txt) |
| Bare interpreter | `144 passed, 2 skipped` — [`pytest-bare.txt`](pytest-bare.txt) |
| Gate with the tree extra | `160 passed`, gate OK — [`gate.txt`](gate.txt) |

`tests/test_branches.py` covers recipe validation, plan keys, three-valued
guards, no branch on execution failure, required-branch abort, branch step
failure, and strict `measured_positions` parsing.

## 4. Limitations and contract gap

- Facts use a **positional index** into `MotionResult.measured_positions`. The
  v0.1 gateway contract does not carry joint names or a gripper measurement, so
  a `gripper_closed` fact is not implementable without a contract change.
  `docs/RECIPES.md` records the constraint; the proposal is
  `jcheng-interfaces/docs/proposals/measured-facts-named.md` (branch
  `docs/measured-facts-proposal-20261126`).
- The tree engine does not execute declared branches yet.
- `required` with no candidate is `ABORTED` + one stop, not a live
  `WAITING_FOR_OPERATOR` state. The supervisor-owned wait is a later stage.
- Derived Plan remains a procedure (new recipe version, run id and
  authorization); no run registry enforces predecessor identity.
- No hardware; the run used the system mock gateway and `MockStack`.
