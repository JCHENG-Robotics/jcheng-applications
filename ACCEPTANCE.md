# Acceptance report — jcheng-applications

> Status: **pass** as of 2026-11-26 for v0.1.0, limited to the offline/mock/L3
> scope.
> Operator: JerichoCheng. Reviewer: pending.
> Scope: repository scaffold, offline orchestration core, M2 read-only transport,
> M3 supervised run against the real system mock gateway, and the S3
> branch/tree/WAITING/derived-plan features.
> No hardware is used, no deployed gateway is contacted and no robot motion is
> claimed.

## 1. Gates

| Gate | Command | Level | Result | Evidence |
|---|---|---|---|---|
| Repository gate | `bash scripts/check_repo_gate.sh` | L0 | pass | local, 2026-11-26 |
| Unit tests (with tree extra) | `python3 -m pytest -q` | L1 | pass (181 tests) | `tests/` |
| Unit tests (default extras) | `python3 -m pytest -q` | L1 | pass (161 passed, 3 skipped) | `tests/` |
| Offline runner against a fake gateway | `pytest -q tests/test_runner.py` | L2 | pass | `tests/test_runner.py` |
| M2 read-only transport | loopback mock gateway | L2 | pass (GET only, no retry, contract mismatch refusal) | `evidence/20261126_m2_transport/` |
| M3 supervised run | real `jcheng-system` mock gateway + `MockStack` | L3 | pass (lease, idempotency, persisted hash-chained record) | `evidence/20261126_m3_supervised/` |
| S3 declared branches — linear engine | mock gateway | L3 | pass (fact index 0=0.03, branch selected, 2/2 steps) | `evidence/20261126_s3_branches/` |
| S3 declared branches — tree engine | exact linear/tree report parity | L3 | pass | `evidence/20261126_s3_tree/` |
| WAITING_FOR_OPERATOR | both engines + loopback CLI | L2–L3 | pass (CLI exit 3, no motion authority) | `evidence/20261126_waiting/` |
| Derived-plan guard | verified predecessor before any gateway call | L3 | pass (zero-request refusal) | `evidence/20261126_derived_plan/` |
| P0 C1–C5 (system dependency) | `jcheng-system` concurrency probe | L3 | pass (zero double terminals) | `jcheng-system/e2e/reports/20261126_concurrency_probe_r2/` |
| Hardware | — | L5 | not run | — |

## 2. Verified claims

| Claim | Level | Evidence |
|---|---|---|
| The manifest declares `hardware_access: none`, pins the v0.1.0 contract hash and has no runtime surface | L0 | `capability.yaml`; `tests/test_contract.py` |
| Recipes validate deterministically and plans produce stable idempotency keys | L1 | `tests/test_recipe.py` |
| The runner refuses missing authorization, scope gaps, contract mismatch, non-`ARMED_IDLE` safety and missing lease before submitting | L2 | `tests/test_runner.py` |
| A failed, timed-out, unknown or unverified step requests one stop and is never retried | L2 | `tests/test_runner.py` |
| M2 reads gateway health/contract/state/safety with GET only, bounded timeouts, no retry and no fallback | L2 | `tests/test_transport.py` |
| M3 acquires and releases a lease, submits one recipe with stable keys and persists a hash-chained run record | L3 | `evidence/20261126_m3_supervised/` |
| Declared branches select deterministically on both engines with exact report parity | L3 | `tests/test_branches.py`; `tests/test_tree_branches.py` |
| A terminal WAITING_FOR_OPERATOR reports exit 3 and grants no motion authority | L3 | `tests/test_waiting.py` |
| `--derived-from` verifies the predecessor hash chain and requires a new recipe version, run id and authorization before any gateway call | L3 | `tests/test_derived.py` |
| The system P0 C2 double-terminal failure is resolved on the mock (C1–C5, stress loop) | L3 | system probe evidence |

## 3. Not verified

| Item | Reason | Risk |
|---|---|---|
| Deployed `armpi_debug_gateway` C1–C5 | Not probed | M4 cannot start until the deployed gateway repeats the contract |
| Hardware execution | No L5 session | No real-arm or chassis claim |
| Named/gripper facts | Requires a `jcheng-interfaces` proposal | Gripper-dependent branches cannot be expressed yet |
| Live operator channel / resumption | Not implemented | WAITING is terminal; resumption is a new authorized run |
| py_trees promotion | Optional extra; licence review open | Tree engine is not the default |
| Operator authentication | Owned by `jcheng-studio` | This repository trusts the supplied authorization record |

## 4. Limitations

- v0.1.0 is an offline/mock application. It is not used by the running robot.
- The deployed gateway and hardware remain M4; no L5 claim is made.
- The tree engine is opt-in; the default is the linear engine.
- A completion claim always depends on the gateway's `bus_verified` value.
- No motion authority and no e-stop. The system layer's stop latch is a software
  stop; the platform has no e-stop, brake, encoder or self-collision model.
- Mock and fake-gateway results are never reported as hardware validation.

## 5. Sign-off

| Role | Name | Date |
|---|---|---|
| Operator | JerichoCheng | 2026-11-26 |
| Reviewer | pending | — |

## 6. Machine-readable report

None. Per-run reports, when produced, must validate against
`jcheng-interfaces/schemas/acceptance-report.v1.schema.json`.
