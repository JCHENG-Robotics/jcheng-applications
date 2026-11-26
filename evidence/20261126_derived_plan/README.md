# Derived Plan identity guard — evidence

> Date: 2026-11-26. Level: **L2** (run-store and loopback mock; no robot).
> A Derived Plan is a new run, never a mutation of a live plan.

## 1. Rules

`jcheng-applications run --derived-from <predecessor-run-id>` requires:

1. the predecessor's JSONL record exists and its hash chain verifies;
2. the same recipe name (a revision of the predecessor, not a different recipe);
3. a **new recipe version**;
4. a **new run id**;
5. a **new authorization session id**.

The guard runs **before** `check_mock`, lease acquisition, any gateway call and
any tree import. A refusal raises `DerivedPlanError` (a `ValueError`), so the
CLI exits 2 and nothing is submitted. The derived `run_started` record carries
`derived_from`; the predecessor's own evidence is unchanged.

## 2. Tests

`tests/test_derived.py` covers:

| Case | Result |
|---|---|
| valid revision, run id and session | evidence with predecessor version/session |
| same recipe version | refused |
| same run id | refused |
| same authorization session | refused |
| missing predecessor | refused |
| different recipe name | refused |
| tampered predecessor chain | treated as missing, refused |
| derived supervised run | `SUCCEEDED`, record `derived_from` set |
| refusal before gateway | zero `/v1` requests |

## 3. Verification

```text
with the tree extra: 181 passed
without py_trees:    161 passed, 3 skipped
gate:                181 passed, OK
```

Raw output: [`pytest-tree.txt`](pytest-tree.txt),
[`pytest-bare.txt`](pytest-bare.txt), [`gate.txt`](gate.txt).

## 4. Limitations

- The guard proves identity, not task correctness: the new recipe revision is
  still validated and authorized normally.
- A predecessor is looked up in the same store directory; a record kept
  elsewhere is not found.
- There is no live-plan mutation path; an operator starts a new run.
- No hardware and no physical-stop claim.
