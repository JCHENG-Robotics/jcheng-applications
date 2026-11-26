# Run-record audit — evidence

> Date: 2026-11-26. Level: **L1** (local store; no gateway, no network, no
> robot). `verify` reports record integrity, not that the run succeeded.

## 1. Command

```bash
jcheng-applications verify <run-id> --store-dir <dir>
```

- Read-only: no gateway call, no token, no network, and the store directory is
  opened with `RunStore(create=False)` so a typo does not create an empty store.
- It recomputes the hash chain and requires a terminal `run_finished` entry.
- It prints the terminal state, step outcomes, `operator_required` and the
  recorded stop confirmation levels.

| Exit | Meaning |
|---|---|
| `0` | chain verifies and a terminal `run_finished` entry exists |
| `1` | record missing, chain broken, or no terminal entry (incomplete run) |
| `2` | usage error, e.g. an unsafe run id |

`0` does **not** mean the run succeeded; `state` is reported separately.

## 2. Tests

`tests/test_audit.py`:

| Case | Result |
|---|---|
| complete two-step record | exit 0; `SUCCEEDED`; both steps listed |
| record with stop levels | `request_accepted` / `unverified` printed |
| tampered payload | exit 1; `verified: false`; chain error |
| unparseable JSON | exit 1 (chain failure, not a usage error) |
| `run_started` only | exit 1; `verified: true`, `complete: false` |
| missing record | exit 1; the store directory is not created |
| unsafe run id (`../escape`) | exit 2 |

Sample output: [`audit-output.json`](audit-output.json). It is generated from a
synthetic store under `/tmp`; the raw JSONL is not committed.

## 3. Verification

```text
with the tree extra: 204 passed
without py_trees:    182 passed, 5 skipped
gate:                OK
```

Raw output: [`pytest-tree.txt`](pytest-tree.txt),
[`pytest-bare.txt`](pytest-bare.txt), [`gate.txt`](gate.txt).

## 4. Limitations

- Payload fields are printed as stored; the audit proves integrity, not that a
  gateway result was correct or that a motion happened.
- It audits one run at a time. There is no bulk audit, retention policy or
  redaction tool.
- No hardware and no physical-stop claim. `physical_stop` remains `unverified`
  or absent.
