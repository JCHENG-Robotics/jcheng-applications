# jcheng-applications roadmap

> Status: **v0.1.0** as of 2026-11-26. The offline orchestration core and the
> M2 read-only transport are complete and CI-green. M3 supervised execution is
> complete **on the mock** (L3: real gateway process + MockStack); the deployed
> gateway and hardware remain M4. The task-engine design is **In progress** in
> [`docs/TASK_ENGINE.md`](docs/TASK_ENGINE.md); M1.5 extracted the Shared
> Execution Core and `runner.py` delegates to it with an unchanged API.

## Current state

| Item | State | Evidence |
|---|---|---|
| Admission manifest (`capability.yaml`) | Complete (2026-11-26) | `capability.yaml`; `tests/test_contract.py` |
| Recipe model, validation and deterministic planning | Complete (2026-11-26) | `src/jcheng_applications/model.py`; `tests/test_recipe.py` |
| Fail-closed runner over `GatewayPort` | Complete (2026-11-26) | `src/jcheng_applications/runner.py`; `tests/test_runner.py` |
| Offline `validate` / `plan` CLI | Complete (2026-11-26) | `src/jcheng_applications/cli.py`; `tests/test_cli.py` |
| Offline example recipe | Complete (2026-11-26) | `recipes/example-supervised-transfer.yaml` |
| Repository gate and CI | Complete (2026-11-26) | `scripts/check_repo_gate.sh`; `.github/workflows/ci.yml` |
| Read-only gateway client (GET only) | Complete (2026-11-26) | `src/jcheng_applications/transport.py`; `tests/test_transport.py`; `evidence/20261126_m2_transport/`; [CI run 45748905495](https://github.com/JCHENG-Robotics/jcheng-applications/actions/runs/45748905495) |
| Session and lease binding, persisted run records | Complete (2026-11-26) | `src/jcheng_applications/lease.py`; `runstore.py`; `tests/test_supervised.py` |
| Supervised execution of one recipe through a gateway | Complete, mock/L3 (2026-11-26) | `src/jcheng_applications/write_transport.py`; `supervised.py`; `evidence/20261126_m3_supervised/` |
| Shared Execution Core | Complete, local (2026-11-26); T-C06 partially verified | `src/jcheng_applications/core/`; `tests/test_core.py`; `evidence/20261126_shared_core/` |
| Production tree runtime and Run Supervisor | Planned (M1.5) | probe-level only |
| Declared branches, three-valued guards and selection records | Planned (S3) | — |
| Hardware acceptance | Not started | — |

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M0 | Repository scaffold and admission manifest | Complete (2026-11-26) | The document set, gate and CI `applications-gate` pass; the manifest claims no runtime surface | `evidence/20261126_scaffold/` |
| M1 | Offline orchestration core | Complete (2026-11-26) | Recipes validate against the v0.1.0 contract; plans are deterministic; the runner refuses bad authorization, contract, safety and lease preconditions and never retries an indeterminate step; no transport exists | `src/jcheng_applications/`; `tests/test_recipe.py`, `tests/test_gateway.py`, `tests/test_runner.py` |
| M1.5 | Isolated task-engine technical validation (parallel, does not gate M2) | In progress (local, 2026-11-26) | The candidate `py_trees` runtime is validated outside the production package: non-blocking leaves over a single worker, a supervisor thread with deadline/stop-once, and an 11-case failure differential. The Shared Execution Core is extracted and both drivers call it; `runner.py` keeps its public API. The production tree runtime and supervisor are not built; T-C06 behavioural parity passes, full report object pending | `scripts/m1_5_pytrees_probe.py`; `evidence/20261126_shared_core/`; `docs/M1_5_VALIDATION.md` |
| M2 | Read-only gateway client and admission | Complete (2026-11-26) | The client implements `GET /v1/health`, `/v1/contract`, `/v1/state` and `/v1/safety` with bounded timeouts, GET-only behavior, no retry and no fallback; contract-mismatch and unreachable refusal are tested against a loopback mock. CI `applications-gate` passed | `src/jcheng_applications/transport.py`; `tests/test_transport.py`; `evidence/20261126_m2_transport/`; [CI run 45748905495](https://github.com/JCHENG-Robotics/jcheng-applications/actions/runs/45748905495) |
| M3 | Supervised execution of one recipe | Complete, mock/L3 (2026-11-26) | A session-bound run acquires and releases the system lease, submits one recipe with stable idempotency keys, honours stop and pre-emption, and produces a persisted hash-chained run report; proven against the real `jcheng-system` gateway process with `MockStack` (L3), no robot. The deployed gateway still needs C1–C5 before M4, and `JOINT_STEP` is single-joint in the system profile | `src/jcheng_applications/write_transport.py`, `lease.py`, `runstore.py`, `supervised.py`; `evidence/20261126_m3_supervised/` |
| S3 | Declared branches and strategy selection (parallel after M3) | Planned | A recipe with declared facts and branches validates; branch scopes join the authorization union; the selector is three-valued, deterministic and cannot read execution outcomes; a required branch with no candidate enters `WAITING_FOR_OPERATOR`; a Derived Plan needs a new version, run id and authorization; proven against a mock gateway (L2), no robot | — |
| M4 | Supervised hardware acceptance | Planned | Every motion session is separately authorized; the system layer applies the lease, limits and stop latch; L5 evidence is recorded alongside the system layer's own | — |

### P0 resolution (2026-11-26)

The original `jcheng-system` probe failed C2 (two terminal results, a late
`COMPLETED`, ignored lease loss). After R1–R4, the re-run passes C1–C5, C5b and
a 30-iteration stop/execute stress loop with zero double terminals. Evidence:
`jcheng-system/e2e/reports/20261126_concurrency_probe_r2/`; revision record:
`jcheng-system/docs/EXECUTION-MODEL-REVISION.md`. **M3 is unblocked on the
mock.** Two things remain outside this repository: R5 (`StopResult`
confirmation levels) needs a `jcheng-interfaces` review, and the deployed
`armpi_debug_gateway` must repeat C1–C5 before M4. There is still no
application-layer workaround: the app must not suppress a late `COMPLETED`,
rewrite an outcome or retry around a stop race. M3 now exercises this path on
the mock without any workaround.

## Next

1. **M1.5 — tree runtime after the shared core.** The core extraction and the
   11-case differential are done; the production tree runtime and supervisor
   remain. Extend T-C06 to the full `RunReport` object and keep the 11-case
   call-log matrix in [`docs/TASK_ENGINE.md`](docs/TASK_ENGINE.md) §16 green.
2. **M3 residuals.** The mock/L3 path is complete. Before M4: document the
   single-joint `JOINT_STEP` constraint in `RECIPES.md`, obtain R5
   (`StopResult` confirmation levels), and run C1–C5 on the deployed
   `armpi_debug_gateway`.
3. **S3 — declared branches and selection.** Add the recipe `facts`/`branches`
   extension, three-valued guards, the deterministic selector and the Derived
   Plan rules. Mock gateway only.
4. **M4 — supervised hardware acceptance.** One separately authorized session
   with the system layer's lease, limits and stop latch; L5 evidence.

## Out of scope

- Joining any DDS domain, using `rclpy` or the `ros2` CLI, or publishing vendor
  topics.
- Opening cameras, serial, CAN, GPIO or any robot device.
- Executing motion directly, publishing `/cmd_vel` or `/move_action`, or
  writing a servo or motor.
- Holding the system's safety authority, replacing its lease policy or acting
  as an e-stop.
- Being the operator console. Connection diagnostics, joint debugging, session
  authorization UI and experiment records belong to `jcheng-studio`.
- Rebuilding perception, planning or the frozen `jcheng-manipulation` baseline.
- Unattended motion, CI-triggered motion or cross-network real-time control.
- Automatic retry of a rejected, aborted, timed-out or unverified step, and
  any recovery branch anchored on an execution failure.
- Navigate, base localization or docking, perception closed loop and learned
  runtime motion; each needs a contract review before it is scheduled.
- A live operator channel or an in-process resume of an indeterminate run.
- Storing tokens, run databases, recordings or model weights in git.

## Dependencies

- `jcheng-interfaces` `>=0.1,<0.2`; contract hash
  `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42`.
- `jcheng-system` API `>=0.1,<0.2`; the gateway is the only robot-facing path
  and the system layer is the only origin of motion.
- `jcheng-studio` owns the human-facing session authorization and diagnostics.
  This repository consumes an `Authorization` record; it does not authenticate
  an operator by itself.
- `py_trees` 2.6 (BSD-3) is the candidate tree runtime for M1.5, subject to the
  license review. It is not a v0.1.0 dependency and no code imports it yet.
- `jcheng-ops` owns the unattended watchdog and restart policy.
- Real-robot work requires separate, explicit operator authorization per
  session and is never triggered by CI or unattended runs.
