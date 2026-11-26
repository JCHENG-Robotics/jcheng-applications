# M1.5 production tree runtime — evidence

> Date: 2026-11-26. Evidence class: **L3 for the end-to-end run (system mock
> gateway process, no robot)**, L2 for the tree/supervisor unit tests.
> The tree engine is **opt-in** (`--engine tree`); the default remains the
> linear engine. py_trees is an optional `tree` extra, and the licence review
> is still open.

## 1. What was built

- `src/jcheng_applications/tree/runtime.py`: a py_trees 2.6 engine whose leaves
  call the shared `StepExecutor`; one daemon worker enforces one in-flight
  step; a `StepLog` keeps completed and late outcomes separate.
- `RunSupervisor`: an independent thread owning the run deadline, cancellation
  and the one stop; the tree loop only reads `halted`.
- `run_supervised(..., engine="tree", deadline_s=...)` and
  `jcheng-applications run --engine tree --deadline-s ...`.
- The default `linear` engine is unchanged; a bare interpreter does not import
  py_trees and the tree tests skip.

## 2. L3 run

The real `jcheng-system` gateway process with `MockStack` on loopback; the
operator armed it through HTTP. Recipe: two single-joint `JOINT_STEP` commands
(0.03 rad) and a `GRIPPER` close (0.10 rad), all `verify_bus: true`.

```bash
JCHENG_GATEWAY_TOKEN=... PYTHONPATH=src \
  /tmp/jcheng-m15-venv/bin/python -m jcheng_applications run \
  /tmp/m15-recipe.yaml --authorization /tmp/m15-auth.json \
  --run-id m15-tree-001 --url http://127.0.0.1:18766 \
  --store-dir /tmp/m15-tree-runs --evidence-class L3 \
  --engine tree --deadline-s 10
```

Result: exit 0, `SUCCEEDED`, 3/3 `COMPLETED`, lease released,
`record_error: null`, `renewals: 0` (the run finished before `ttl/3`).

- [`cli-success.json`](cli-success.json)
- [`report-summary.json`](report-summary.json) — sanitized `run_started` and
  `run_finished`; raw JSONL stays outside the repository
- [`verify.txt`](verify.txt) — chain verified, file mode 0600

## 3. Unit and differential evidence

| Run | Result |
|---|---|
| Bare interpreter (no py_trees) | `127 passed, 1 skipped` — [`pytest-bare.txt`](pytest-bare.txt) |
| With the `tree` extra (py_trees 2.6.0) | `131 passed` — [`pytest-tree.txt`](pytest-tree.txt) |
| Repository gate with py_trees | `131 passed`, gate OK — [`gate.txt`](gate.txt) |

`tests/test_tree_runtime.py` covers:

- tree success matches the linear engine on state and step outcomes;
- rejection stops exactly once and records `FAILED`;
- a 0.1 s deadline against a 0.3 s execute returns `INDETERMINATE`, one
  synthetic `UNKNOWN/DEADLINE` step, one stop, lease released;
- an unknown engine and `deadline_s` on the linear engine are refused.

## 4. Limitations

- py_trees is not a default dependency. `pyproject.toml` exposes it as the
  optional `tree` extra; `LICENSE-REVIEW.md` still records the open review
  (py_trees 2.6.0 + pydot 4.0.1 + pyparsing 3.3.3).
- The supervisor owns deadline/cancel/stop. Lease renewal remains in
  `LeaseSession`; the two threads are independent.
- No hardware: the system gateway used `MockStack`; no physical motion and no
  physical stop are claimed.
- R5 (`StopResult` S1/S2) and the deployed `armpi_debug_gateway` remain M4
  prerequisites.
