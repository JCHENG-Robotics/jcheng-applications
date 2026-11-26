# Architecture — jcheng-applications

> Status: **Experimental** as of 2026-11-26. Version 0.1.0.
> v0.1.0 is an offline core: no HTTP/WS client, no runtime process, no DDS
> participant and no device path. The gateway adapter is planned for M2.

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
      │  RunReport (in memory)
      │  GatewayPort (M2: HTTP/WS)
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
| Runner | Operator laptop, in process | Authorize, preflight, submit one intent per step, map the terminal result, request one stop on failure | Retrying an indeterminate motion, inventing a result, widening authorization |
| Gateway port | Operator laptop, abstract | The only path to the robot; M2 implements it over HTTP/WS | Device, ROS, SSH or vendor-SDK access |
| Run report | Operator laptop, in memory | Preserve the gateway's outcome, reason code and intent identity per step | Relabelling a mock result as hardware, persisting secrets |
| CLI | Operator laptop | Offline `validate` and `plan` only | A `run` command before M2 |

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

The application holds a client-side lease **reference**, never the lease
itself. The system layer may revoke or pre-empt it at any moment; the next
submission is then rejected and the run fails closed.

## 5. Invariants

| Invariant | Enforcement |
|---|---|
| No hardware access | `capability.yaml`; `tests/test_no_hardware.py` |
| No DDS domain, `rclpy`, `ros2` CLI or vendor topic | static scan; review of every transport change |
| No network client in v0.1.0 | static scan of `src/`, `scripts/` and `recipes/` |
| No direct motion execution | static scan; gateway-only boundary; system lease |
| Deterministic plans | SHA-256 over run, index, name, kind, profile and canonical params |
| One submission per step | the runner's loop; no retry path |
| Explicit per-session authorization | `Authorization` validation before any gateway call |
| No fabricated result | terminal outcome copied from the gateway; unknown stays unknown |
| No secrets or run data in git | gate scan; `.gitignore`; review |
| Fail closed | refusal is the default; ambiguity stops the run |

## 6. Failure semantics

| Condition | Required behavior |
|---|---|
| Authorization missing, unapproved, naive timestamp or scope gap | `ABORTED`; nothing submitted |
| Contract hash or range mismatch | `ABORTED`; no fallback gateway |
| Safety not `ARMED_IDLE` or stop latched | `ABORTED`; nothing submitted |
| Missing lease reference | `ABORTED`; nothing submitted |
| Gateway unreachable at preflight | `ABORTED`; nothing submitted |
| Gateway unreachable during a submission | `INDETERMINATE`; one stop request; no retry |
| `REJECTED` terminal result | `FAILED`; one stop request |
| `ABORTED` terminal result | `ABORTED`; one stop request |
| `TIMEOUT` or `UNKNOWN` terminal result | `INDETERMINATE`; one stop request; operator inspection |
| `COMPLETED` without `bus_verified` on an arm/gripper/full step | `INDETERMINATE`; one stop request |
| Stop request fails | run state preserved; `stop_error` recorded |
| Malformed gateway payload | `INDETERMINATE`; one stop request |

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
- [README](../README.md) / [ROADMAP](../ROADMAP.md) / [ACCEPTANCE](../ACCEPTANCE.md).
