# T-C06 — full RunReport parity

> Date: 2026-11-26. Level: **L2** (loopback mock gateway, no robot).
> `tests/test_tree_regression.py` runs the linear and tree engines against the
> same scripted gateway with a fixed clock and asserts the complete
> `RunReport` dataclasses are equal. Requires the optional `tree` extra; the
> bare gate skips these two test modules.

## 1. Cases

| Case | Script | Expected |
|---|---|---|
| `success` | two verified `COMPLETED` | `SUCCEEDED`, no stop |
| `unverified` | second `COMPLETED` without bus verification | `INDETERMINATE`, one stop |
| `rejected-first` | first step `REJECTED` | `FAILED`, one stop |
| `rejected-second` | second step `REJECTED` | `FAILED`, one stop |
| `aborted` | `ABORTED` | `ABORTED`, one stop |
| `timeout` | `TIMEOUT` | `INDETERMINATE`, one stop |
| `unknown` | `UNKNOWN` | `INDETERMINATE`, one stop |
| `safety-latch` | second result carries `STOP_LATCHED` | `ABORTED`, one stop |
| `execute-unavailable` | HTTP 503 on execute | `INDETERMINATE`, one stop |
| `safety-disarmed` | preflight sees `DISARMED` | `ABORTED`, no submission |
| `stop-fails` | rejection plus HTTP 503 on stop | `FAILED`, `stop_error` equal |
| `malformed` | second execute payload is not a `MotionResult` | `INDETERMINATE`, one stop |

## 2. Result

```text
/tmp/jcheng-m15-venv/bin/python -m pytest -q
143 passed

python3 -m pytest -q          # no py_trees
127 passed, 2 skipped

PATH=/tmp/jcheng-m15-venv/bin:$PATH bash scripts/check_repo_gate.sh
143 passed
Repository gate OK
```

Raw output: [`pytest-tree.txt`](pytest-tree.txt),
[`pytest-bare.txt`](pytest-bare.txt), [`gate.txt`](gate.txt).

The comparison is exact: run id, recipe identity, contract hash, operator,
session, state, timestamps, every `StepReport`, `abort_reason` and
`stop_error`. The fixed clock removes timestamp variance; the same mock port
keeps transport error strings identical.

## 3. Limitations

- Mock gateway and scripted results only; no hardware and no physical stop.
- Deadline/cancellation has no linear equivalent, so those tree-only cases are
  covered by `tests/test_tree_runtime.py`, not by this parity matrix.
- The tree engine remains opt-in; py_trees is an optional extra and the licence
  review is open.
