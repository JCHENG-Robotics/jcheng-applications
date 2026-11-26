# WAITING_FOR_OPERATOR semantics — evidence

> Date: 2026-11-26. Level: **L2** (unit and loopback CLI tests; no robot).
> The operator-required state is a **terminal report/CLI state**, not a live
> channel: the process exits 3 and a resumption is a new authorized run.

## 1. What was added

- `RunReport.operator_required` / `operator_reason`, set by both the linear and
  tree engines.
- `jcheng-applications run` prints `WAITING_FOR_OPERATOR: <reason>` and exits
  **3** when operator inspection is required; exit 1 stays for a clean
  `FAILED`/`ABORTED`, exit 2 for usage or a missing record, exit 0 for
  `SUCCEEDED`.
- The run record persists both fields.
- Both engines set the same fields for the full-report parity matrix.

## 2. Cases

| Case | State | `operator_required` |
|---|---|---|
| two verified steps | `SUCCEEDED` | false |
| `COMPLETED` without bus verification | `INDETERMINATE` | true |
| `REJECTED` | `FAILED` | false |
| `ABORTED` result | `ABORTED` | true |
| stop request fails | `FAILED` | true, reason `stop request failed: …` |
| preflight `DISARMED` | `ABORTED` | false |
| required branch, no candidate | `ABORTED` | true |
| CLI with an unverified step | — | exit **3**, `WAITING_FOR_OPERATOR` on stderr |

The tree engine applies the same rules; its outcome loop and the supervisor's
required-branch/deadline/cancel reasons set the same fields. The 12-case
T-C06 and the 4-case branch parity suites pass with exact `RunReport`
equality, including the new fields.

## 3. Verification

```text
164+8 tests with the tree extra: 172 passed
144+8 tests without py_trees:   152 passed, 3 skipped
gate with the tree extra:       172 passed, OK
```

Raw output: [`pytest-tree.txt`](pytest-tree.txt),
[`pytest-bare.txt`](pytest-bare.txt), [`gate.txt`](gate.txt).

## 4. Limitations

- `WAITING_FOR_OPERATOR` is terminal. There is no live operator channel, no
  in-process resume and no motion authority; a new run needs a new
  authorization.
- The reason text is diagnostic; the state field is the machine-readable one.
- No hardware and no physical-stop claim.
