# S3 declared branches on the tree engine — evidence

> Date: 2026-11-26. Level: **L3** for the end-to-end tree run (real
> `jcheng-system` gateway process + `MockStack`), L2 for the branch parity
> tests. No robot and no physical motion claim.

## 1. What changed

- `tree/runtime.py` now builds, after each main step with declared branches:
  a `SelectionLeaf` (calls the shared `select_branch` once), then a py_trees
  `Selector` whose children are one `Sequence` per branch
  (`BranchCondition` + branch `SubmitLeaf`s) and a no-branch fallback
  condition.
- A branch step failure fails the branch sequence, the fallback condition is
  false (a branch was selected), and the selector fails; the leaf's stop
  request is preserved.
- A required branch with no candidate makes the selection leaf fail and
  request one stop; the report is `ABORTED` with the same reason as the linear
  engine.
- Branch leaves write to the shared `StepLog` but are not fact sources; only
  main steps feed `main_outcomes` and declared facts.
- `run_supervised` no longer refuses facts/branches for the tree engine.

## 2. L3 run

`--engine tree` against the real system mock gateway, using
[example-declared-branch.yaml](../../recipes/example-declared-branch.yaml):

```text
approach-j1  COMPLETED  measured [0.03, 0, 0, 0, 0]
adjust-j1    COMPLETED  measured [0.05, 0, 0, 0, 0]
selection after approach-j1: adjust-j1.v1 PASS selected
```

State `SUCCEEDED`, 2/2 steps, lease released, record verified.

- [`cli-success.json`](cli-success.json)
- [`report-summary.json`](report-summary.json)
- [`verify.txt`](verify.txt) — chain verified, file 0600

## 3. Tests

| Run | Result |
|---|---|
| With the tree extra | `164 passed` — [`pytest-tree.txt`](pytest-tree.txt) |
| Bare interpreter | `144 passed, 3 skipped` — [`pytest-bare.txt`](pytest-bare.txt) |
| Gate with the tree extra | `164 passed`, gate OK — [`gate.txt`](gate.txt) |

`tests/test_tree_branches.py` asserts **exact `RunReport` equality** between the
linear and tree engines for four branch cases, with a fixed clock:

- selected branch (2 steps);
- optional branch not selected;
- required branch with no candidate (`ABORTED`, one stop);
- branch step failure (`FAILED`, one stop).

## 4. Limitations

- Branch facts remain positional arm positions; named-joint/gripper facts need
  the `jcheng-interfaces` proposal.
- A live `WAITING_FOR_OPERATOR` is not implemented; required/no-candidate is
  `ABORTED` + one stop.
- Derived Plan identity is a documented rule, not a registry.
- No hardware; the tree engine remains opt-in and py_trees is an optional extra
  pending licence review.
