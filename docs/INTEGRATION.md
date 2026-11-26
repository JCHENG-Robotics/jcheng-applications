# Integration — jcheng-applications

> Status: **Experimental** as of 2026-11-26. The M2 read-only transport and the
> M3 supervised write path are implemented. M3 acquires and releases a client
> lease and submits only to a gateway that advertises `mock`; the deployed
> gateway and hardware remain M4.

## 1. Admission and discovery

The system reads `capability.yaml` and checks the admission fields. For the M3
manifest it finds:

| Field | Value | Meaning |
|---|---|---|
| `name` | `jcheng-applications` | application identity |
| `version` | `0.1.0` | must match `VERSION` |
| `contract.interfaces` | `>=0.1,<0.2` | pinned contract range |
| `contract.contract_hash` | `sha256:0b8d72…24b42` | must match the interfaces manifest |
| `safety.class` | `motion_source` | a gateway intent source, not a DDS publisher |
| `safety.scopes` | `arm`, `gripper` | exactly the recipe scopes shipped here |
| `safety.requires_lease` | `true` | binding: the run acquires a lease before the first submission and releases it on every terminal path |
| `hardware_access` | `none` | hard boundary |
| `runtime` | empty | no ROS domain, no topics, no services |

Because the runtime surface is empty, admission means "no runtime object to
discover". The application is not a ROS node and never appears in a DDS graph;
the system layer does not need to arbitrate with it. M2/M3 use HTTP; the
runtime surface stays empty because HTTP is not a ROS interface.

M3 moved the class to `motion_source` and the scopes to `arm`/`gripper` through
the contract-first review. The runtime surface stays empty because HTTP is not
a ROS interface; the application remains a gateway client, not a DDS node.

## 2. Gateway transport and contract

The application reaches the robot only through a `jcheng-system` gateway. There
is no second path: no SSH, no vendor SDK, no ROS topic, no serial device.

| Item | Rule |
|---|---|
| Base URL | Operator configuration; no address is hard-coded or committed |
| Authentication | `Authorization: Bearer <token>` per the gateway OpenAPI; the token is read from operator configuration and never enters git |
| Derived Plan | `run --derived-from <run-id>` reads the predecessor record from the same store and requires a new recipe version, run id and authorization session before any gateway call |
| Health | `GET /v1/health` is unauthenticated liveness; it carries no motion authority |
| Contract | `GET /v1/contract` returns `ContractInfo`; a hash or range mismatch refuses the run |
| Safety | `GET /v1/safety` returns `SafetyState`; a run requires `ARMED_IDLE` and no stop latch |
| State | `GET /v1/state` returns `truth` (`measured` or `commanded`), `verification`, `fresh`, `profile_hash` and `header.stamp`. A `commanded` snapshot is never treated as measurement |
| Lease | `POST /v1/leases` acquires a client reference, `POST /v1/leases/{id}/renew` renews at `ttl/3`, `DELETE /v1/leases/{id}` releases. The system layer owns the lease and may revoke it |
| Intent validation | `POST /v1/motion/validate` for a dry validation when M2 needs it |
| Intent submission | `POST /v1/motion/execute` with the `Idempotency-Key` header; the gateway and system layer perform the motion. M3 refuses a gateway without the `mock` capability |
| Stop | `POST /v1/motion/stop` after a failed or indeterminate submission; the response is parsed into a `StopReceipt`. A legacy response is S1 only; S2 requires `confirmation: backend_confirmed`; `physical_stop` is always `unverified` |
| Events | `GET /v1/events` and the WebSocket envelope for state, safety, lease and motion events |
| Ports | Deployment detail, not contract; the adapter reads the configured URL and assumes nothing |
| Middleware | No DDS, no RMW, no `CYCLONEDDS_URI`. HTTP/WS only |

The authoritative documents are
[`jcheng-gateway.v1.yaml`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/openapi/jcheng-gateway.v1.yaml),
[`jcheng-ws.v1.json`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/ws/jcheng-ws.v1.json)
and
[`messages.v1.schema.json`](https://github.com/JCHENG-Robotics/jcheng-interfaces/blob/main/schemas/messages.v1.schema.json).
Endpoints the application needs that are absent from the OpenAPI document are
added to `jcheng-interfaces` first; this repository does not invent a private
endpoint.

## 3. Intent identity and idempotency

- A plan binds each step to a SHA-256 idempotency key over the run id, step
  index, name, kind, profile and canonical parameters.
- The same runner and payload produce the same key; a changed payload or run id
  produces a different key.
- The command carries `intent_id`, `idempotency_key`, `lease_id`, `source`,
  `kind`, `profile` and the pinned `contract` object, matching `MotionCommand`.
- `source` is `jcheng-applications/<run_id>`. The gateway records it with the
  intent.
- The runner does not retry. A transport error leaves the outcome unknown; the
  next action is an operator inspection, not a second submission.

## 4. Authorization and sessions

The application performs no motion on its own. From M3 it acquires a **client
lease reference** through the gateway; the system owns the lease, may revoke or
pre-empt it at any moment, and applies the deadman, limits and stop latch.

- Every real-robot motion requires separate, explicit, per-session operator
  authorization. A previous session's approval does not carry over.
- The `Authorization` record names the operator and session, lists the covered
  scopes, is explicitly approved and carries an ISO 8601 timestamp with a
  timezone.
- The recipe's scopes must be a subset of the authorized scopes. A scope gap
  refuses the run before any gateway call.
- `jcheng-studio` owns the human-facing session, token handling and experiment
  records. This repository trusts the supplied record and does not authenticate
  an operator by itself.
- The application never acquires a token over SSH and never reads the robot's
  token file.

### 4.1 Execution engine

`run` defaults to the linear engine. `--engine tree` selects the opt-in
py_trees engine; `--deadline-s` is available only there. The tree's independent
Run Supervisor owns the deadline, cancellation and the one stop. A step in
flight when the supervisor halts becomes `UNKNOWN` and the run is
`INDETERMINATE`; a late `COMPLETED` is evidence only. py_trees is the optional
`tree` extra and the licence review is open.

## 5. Failure semantics

| Condition | Required behavior |
|---|---|
| Gateway does not advertise `mock` | Refused before a lease or submission; `run` exits non-zero |
| Lease acquisition fails | `run` exits non-zero; nothing submitted |
| Lease renewal fails during the run | One stop; later submissions are blocked; the run cannot succeed |
| Gateway unreachable at preflight | `ABORTED`; nothing submitted; no retry loop |
| `GET /v1/contract` hash or range mismatch | `ABORTED`; no fallback gateway |
| Safety not `ARMED_IDLE`, stop latched or unknown | `ABORTED`; nothing submitted |
| Missing or empty lease reference | `ABORTED`; nothing submitted |
| `401` / `403` | `ABORTED`; no credential retry loop; operator must intervene |
| `409` conflict / idempotency conflict | `FAILED`; the gateway's reason is preserved |
| `422` invalid command | `FAILED`; the recipe or its parameters must be corrected |
| `503` unavailable during submission | `INDETERMINATE`; one stop request; no retry |
| `MotionResult.outcome = REJECTED` | `FAILED`; one stop request |
| `MotionResult.outcome = ABORTED` | `ABORTED`; one stop request |
| `MotionResult.outcome = TIMEOUT` or `UNKNOWN` | `INDETERMINATE`; one stop request |
| `COMPLETED` without `bus_verified` for arm/gripper/full | `INDETERMINATE`; one stop request |
| Stop path fails | run state preserved; `stop_error` recorded; `stop_confirmation`/`physical_stop` stay `None`; `operator_required` set; exit 3 |
| Unconfirmed/aborted outcome, lease loss or required branch with no candidate | `operator_required` set; exit 3; no motion authority |
| Run record cannot be written | The report is still returned; `record_error` is set and `run` exits 2; the motion may have happened |

The application has no authority to reset the system layer. A wedged gateway or
a latched stop is the system layer's incident, handled by `jcheng-ops` and the
operator runbook.

## 6. Security and operations

- No listener, no inbound port and no public interface. The application is a
  client.
- The gateway token lives only in operator configuration with mode 0600. It is
  never logged, exported, committed or placed in a URL query string.
- Run reports and exported evidence live outside the repository from M3; the
  repository gate rejects databases, recordings and env files.
- `verify <run-id> --store-dir <dir>` audits one stored record without a
  network call or store creation. Exit `0` means the hash chain verifies and a
  terminal `run_finished` entry exists, **not** that the run succeeded; `1`
  covers a missing record, a broken chain or an incomplete run; `2` is a usage
  error such as an unsafe run id. It prints the terminal state, step outcomes
  and the recorded stop confirmation levels.
- The repository ships no systemd unit for the robot and no DDS configuration.
  The laptop watchdog and its unit belong to `jcheng-ops`.
- A restart is not a motion command. The runner does not resume a run after a
  process restart; the operator must start a new, separately authorized run.

## 7. Verification stages

| Stage | Evidence |
|---|---|
| v0.1.0 admission and offline core | `python3 -m pytest -q`; `bash scripts/check_repo_gate.sh` |
| M2 read-only gateway client | `GET`-only health, contract, state and safety reads against a loopback mock (L2, 2026-11-26); contract-mismatch, auth, timeout and unreachable refusal; no retry; CI `applications-gate` passed |
| M3 supervised execution | Real `jcheng-system` gateway process + `MockStack` on loopback (L3, 2026-11-26): lease acquire/release, 3/3 steps, stop-on-rejection, hash-chained record. No robot. Evidence: `evidence/20261126_m3_supervised/` |
| Stop confirmation receipt | Optional R5 fields parsed on both engines (L1–L3, 2026-11-26): absent → S1, explicit `backend_confirmed` → S2, `physical_stop` only `unverified`, stop failure → no level. The real system mock gateway (legacy response) records S1 only. Evidence: `tests/test_stop_confirmation.py`; `evidence/20261126_stop_confirmation/` |
| Run-record audit | `verify` checks the hash chain and terminal entry, prints state/steps/stop levels, makes no network call and does not create the store (L1, 2026-11-26). Evidence: `tests/test_audit.py`; `evidence/20261126_record_audit/` |
| M1.5 tree engine | Opt-in `--engine tree` against the same system mock gateway (L3, 2026-11-26): 3/3 steps, lease released; L2 deadline/rejection/one-stop tests. Evidence: `evidence/20261126_m1_5_tree/` |
| M4 supervised hardware | separately authorized L5 session with system limits and stop latch active |

No hardware claim is valid before M4. Synthetic, mock and replay results are
never reported as hardware validation.

## 8. Related

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway OpenAPI, WS envelope, message schemas.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — gateway owner, lease and motion authority.
- [`jcheng-studio`](https://github.com/JCHENG-Robotics/jcheng-studio) — operator console, session authorization and records.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — unattended health, watchdog and incident runbook.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — components, state machine and invariants.
- [`RECIPES.md`](RECIPES.md) — recipe format, step kinds and authorization.
- [README](../README.md) / [ROADMAP](../ROADMAP.md) / [ACCEPTANCE](../ACCEPTANCE.md).
