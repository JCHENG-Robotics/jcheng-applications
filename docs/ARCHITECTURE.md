# Architecture — jcheng-applications

> Status: **Experimental** as of 2026-11-26. Version 0.1.0.
> The offline core, the M2 read-only transport and the M3 supervised write path
> are implemented. There is no runtime process, no DDS participant and no
> device path. `run` refuses a gateway that does not advertise `mock`; the
> deployed gateway and hardware remain M4.
>
> The Shared Execution Core is extracted. An opt-in py_trees engine and an
> independent Run Supervisor are implemented (`--engine tree`); the default is
> still the linear engine, and py_trees is an optional extra pending licence
> review. Declared branches and the three-valued selector are implemented on
> both engines with positional arm facts. The client records S1/S2 stop
> confirmation from the optional R5 receipt fields; the schema review is
> pending and a physical stop is never claimed.

## 1. Position in the ecosystem

`jcheng-applications` is an L4 application. It sits above the system layer,
depends on the published gateway contract, and owns no capability, device or
motion path. It is not the operator console; `jcheng-studio` owns the
human-facing session and diagnostics surface.

```text
operator / script
      │  recipe YAML + explicit Authorization record
      ▼
jcheng-applications core (operator laptop)
      │  Plan (deterministic, offline)
      │  RunReport + hash-chained record (outside the repo)
      │  GatewayPort (M2 read / M3 lease+execute+stop, HTTP)
      ▼
jcheng-system gateway
      │  contract, safety, lease, validation, stop latch
      ▼
hardware bridge -> bus servos / base board
```

Rules that come from the ecosystem and are not negotiable here:

- The system layer is the **only** origin of motion. The application never
  calls a motion service directly, never publishes `/cmd_vel` or
  `/move_action`, and never writes a servo or motor device.
- The application is an **HTTP/WS client** from M2. It joins no DDS domain —
  not the vendor domain 0, not the privileged domain 41, not the capability
  domain 42 — and uses neither `rclpy` nor the `ros2` CLI.
- Cross-repository contracts live only in `jcheng-interfaces`: the gateway
  OpenAPI document, the message schemas and the contract hash.
- `hardware_access: none` in `capability.yaml`. No camera, serial, CAN, GPIO or
  vendor SDK is opened, now or later.
- `/joint_states` from the vendor stack is a **command cache**, not a
  measurement. The application never reads it; completion truth is the
  gateway's `bus_verified` field on the terminal `MotionResult`.
- The application is **not** an e-stop. The platform has no e-stop, brake,
  encoder or self-collision model; the system layer's stop latch is a software
  stop.

## 2. Components

| Component | Runs on | Responsibility | Prohibited |
|---|---|---|---|
| Recipe model | Operator laptop, in process | Parse and validate YAML against the pinned contract; reject unknown fields, kinds, scopes and payloads | Accepting an unversioned or unpinned contract |
| Planner | Operator laptop, in process | Produce a stable `Plan` with one idempotency key per step | I/O, network, time-dependent keys, hidden randomness |
| Shared execution core | Operator laptop, in process | Preflight, command construction, one submission per step, terminal classification and stop-once, used by every driver | Retrying an indeterminate motion, inventing a result, widening authorization |
| Linear runner | Operator laptop, in process | Preflight, submit one intent per step, map the terminal result, request one stop on failure | Retrying an indeterminate motion, inventing a result, widening authorization |
| Tree engine (M1.5, opt-in) | Operator laptop, in process | py_trees `Sequence` over the shared core; one daemon worker; one in-flight step | Direct gateway calls, retry, a second in-flight step |
| Run Supervisor (M1.5) | Operator laptop, own thread | Run deadline, cancellation and the one stop, independent of the tree loop | Driving the tree, renewing or owning the lease |
| Gateway port | Operator laptop, abstract | The only path to the robot; M2 reads and M3 writes go through it | Device, ROS, SSH or vendor-SDK access |
| Read-only transport (M2) | Operator laptop, in process | `GET /v1/health`, `/contract`, `/state` and `/safety` with bounded timeouts and strict parsing | POST, submission, stop, lease mutation, retry |
| Write transport (M3) | Operator laptop, in process | Lease acquire/renew/release, one execute per step, one stop; parses the stop receipt (S1/S2, physical `unverified`); refuses a non-mock gateway | Retry, redirect, non-mock submission, device access, inferring S2 from HTTP 200 |
| Lease session | Operator laptop, in process | Acquire before the first submission, renew at `ttl/3`, release on every terminal path | Holding safety authority; retrying a lost lease |
| Run store | Operator laptop, file | Append-only, hash-chained JSONL outside the repository, mode 0600; includes stop confirmation levels | Tokens, raw lease ids, recordings in git |
| Supervised run | Operator laptop, in process | Compose lease + runner + record for one recipe | Executing motion itself or bypassing the preflight |
| Derived-plan guard | Operator laptop, in process | Require a verified predecessor, new recipe version, new run id and new authorization before any gateway call | Mutating a live plan, resuming an old run or widening authorization |
| Run report | Operator laptop, in memory | Preserve the gateway's outcome, reason code, intent identity, selection records and the terminal `operator_required` flag per run | Relabelling a mock result as hardware, persisting secrets, granting motion authority |
| CLI | Operator laptop | `validate`, `plan`, read-only `check`, mock-only `run`, read-only `verify` | Non-mock `run`; device or DDS access; `verify` creates or edits nothing |

The presentation surface is deliberately absent. `jcheng-studio` owns the
operator UI, the session record and the authorization interaction; this
repository consumes an `Authorization` record and does not authenticate a
human by itself.

## 3. Run state machine

```text
                 recipe + authorization + run_id
                              │
                              ▼
                     ┌─────────────────┐
                     │   preflight     │
                     │ authorization   │
                     │ contract pin    │
                     │ safety ARMED_IDLE│
                     │ lease present   │
                     └────────┬────────┘
                              │ refusal
                              ├──────────────────────────► ABORTED (no submission)
                              │ pass
                              ▼
                     ┌─────────────────┐
                     │ submit step i   │
                     └────────┬────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │ COMPLETED           │ REJECTED             │ ABORTED / TIMEOUT / UNKNOWN
        │ + bus verified      │                      │ or no bus verification
        ▼                     ▼                      ▼
   next step i+1           FAILED               INDETERMINATE / ABORTED
        │
        └── all steps ──────────────────────────────► SUCCEEDED
```

- Preflight failures never submit an intent and never request a stop.
- After the first attempted submission, any non-success requests exactly one
  stop. A stop failure is recorded in `stop_error`, not hidden.
- There is no retry. `TIMEOUT` and `UNKNOWN` mean the motion may have happened;
  the operator must inspect the bus state before doing anything else.
- A `COMPLETED` result that lacks `bus_verified` for an arm, gripper or full
  scope is `INDETERMINATE`, not success.

## 4. Authority and responsibility

| Decision | Owner |
|---|---|
| Whether a motion is allowed | `jcheng-system` (contract, lease, deadman, limits, stop latch) |
| Which steps the operator intends | The recipe author, authorized per session by the operator |
| Whether the session is authorized | `jcheng-studio` (human-facing) and the operator-supplied `Authorization` record |
| Whether a step completed | The gateway's terminal `MotionResult`; the application only interprets it |
| Whether a run is a hardware result | The evidence record, not the run state |
| Whether the operator must inspect before any new run | `RunReport.operator_required`; the report grants no motion authority |

The application holds a client-side lease **reference**, never the lease
itself. The system layer may revoke or pre-empt it at any moment; the next
submission is then rejected and the run fails closed.

## 5. Invariants

| Invariant | Enforcement |
|---|---|
| No hardware access | `capability.yaml`; `tests/test_no_hardware.py` |
| No DDS domain, `rclpy`, `ros2` CLI or vendor topic | static scan; review of every transport change |
| Network access confined to `transport.py` and `write_transport.py`; M2 is GET-only; M3 refuses non-mock gateways | static scan; AST test `test_transport_is_get_only_and_has_no_motion_call`; `check_mock` |
| Lease acquired before the first submission and released on every terminal path | `LeaseSession`; `tests/test_supervised.py` |
| The run record is append-only, hash-chained and outside the repository | `RunStore`; `verify.txt`; gate scan |
| A record audit is read-only and reports integrity, not success | `verify`; `RunStore(create=False)`; `tests/test_audit.py` |
| No direct motion execution | static scan; gateway-only boundary; system lease |
| Deterministic plans | SHA-256 over run, index, name, kind, profile and canonical params |
| One submission per step | the linear loop and the tree's single daemon worker; no retry path |
| py_trees is imported only by `tree/runtime.py`; the default linear engine and the gate do not import it | import review; optional `tree` extra; tree tests skip without it |
| The Run Supervisor owns deadline/cancel/one-stop on its own thread | `tree/runtime.py`; `tests/test_tree_runtime.py` |
| Explicit per-session authorization | `Authorization` validation before any gateway call |
| No fabricated result | terminal outcome copied from the gateway; unknown stays unknown |
| Stop confirmation is not upgraded | `StopReceipt` parser: absent R5 fields are S1, S2 requires `backend_confirmed`, `physical_stop` only `unverified`; `tests/test_stop_confirmation.py` |
| No secrets or run data in git | gate scan; `.gitignore`; review |
| Fail closed | refusal is the default; ambiguity stops the run |

## 6. Failure semantics

| Condition | Required behavior |
|---|---|
| Authorization missing, unapproved, naive timestamp or scope gap | `ABORTED`; nothing submitted |
| Contract hash or range mismatch | `ABORTED`; no fallback gateway |
| Safety not `ARMED_IDLE` or stop latched | `ABORTED`; nothing submitted |
| Missing lease reference | `ABORTED`; nothing submitted |
| Gateway does not advertise `mock` | Refused before a lease or submission |
| Lease acquisition fails | `run` exits non-zero; nothing submitted |
| Lease renewal fails during the run | One stop; later submissions are blocked; the run cannot succeed |
| Tree deadline or cancel fires while a step is in flight | One stop; the in-flight step is `UNKNOWN`, the run is `INDETERMINATE`; a late `COMPLETED` is evidence only |
| Tree deadline fires between steps | One stop; no step is submitted; `ABORTED` |
| Gateway unreachable at preflight | `ABORTED`; nothing submitted |
| Gateway unreachable during a submission | `INDETERMINATE`; one stop request; no retry |
| `REJECTED` terminal result | `FAILED`; one stop request |
| `ABORTED` terminal result | `ABORTED`; one stop request |
| `TIMEOUT` or `UNKNOWN` terminal result | `INDETERMINATE`; one stop request; operator inspection |
| `COMPLETED` without `bus_verified` on an arm/gripper/full step | `INDETERMINATE`; one stop request |
| Stop request fails | run state preserved; `stop_error` recorded; no S1/S2 level; `operator_required` set; exit 3 |
| Unconfirmed/aborted outcome, lease loss or required branch with no candidate | `operator_required` and `operator_reason` set; exit 3; no motion authority |
| Clean `REJECTED`/`FAILED` or preflight refusal | `operator_required` stays false; exit 1 (or 2 for a record failure) |
| Malformed gateway payload | `INDETERMINATE`; one stop request |

The M2 read-only transport never enters the run state machine. Its failures are:

| Condition | Behavior |
|---|---|
| `401` / `403` | `GatewayUnauthorized`; no retry; the operator fixes the credential |
| `5xx`, connect error or read timeout | `GatewayUnavailable`; bounded; no fallback endpoint |
| Non-200, redirect or `404` | `GatewayProtocolError`; the endpoint or contract is wrong |
| Contract outside the pin | `GatewayContractMismatch`; admission refuses |
| Malformed or oversized body | `GatewayProtocolError`; nothing is inferred |

## 7. Non-goals

- Replacing the system layer's safety policy, lease, limits or stop latch.
- Becoming a general-purpose robot API or a remote shell.
- Owning the operator UI, session authentication or experiment records.
- Rebuilding perception, planning or the frozen `jcheng-manipulation` baseline.
- Unattended motion, CI-triggered motion or cross-network real-time control.
- Acting as an e-stop or a safety-rated device.

## 8. Related

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway and message contracts.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — gateway owner and sole motion authority.
- [`jcheng-studio`](https://github.com/JCHENG-Robotics/jcheng-studio) — operator console and session authorization.
- [`INTEGRATION.md`](INTEGRATION.md) — gateway admission, transport and failure rules.
- [`RECIPES.md`](RECIPES.md) — recipe syntax, step kinds and authorization.
- [`TASK_ENGINE.md`](TASK_ENGINE.md) — planned tree executor, supervisor, branches and selection (design only).
- [`REFERENCE_INTEGRATION.md`](REFERENCE_INTEGRATION.md) — offline ArmPi Ultra × ore_sorting reference over the mock gateway (synthetic only).
- [README](../README.md) / [ROADMAP](../ROADMAP.md) / [ACCEPTANCE](../ACCEPTANCE.md).
