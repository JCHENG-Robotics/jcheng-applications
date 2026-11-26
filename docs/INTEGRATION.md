# Integration — jcheng-applications

> Status: **Experimental** as of 2026-11-26. The M2 read-only transport is
> implemented locally: four GET reads, bounded timeouts, no retry. No intent
> submission, stop or lease mutation exists. M3 is unblocked on the mock after
> the system-side C2 revision and is not implemented.

## 1. Admission and discovery

The system reads `capability.yaml` and checks the admission fields. For v0.1.0
it finds:

| Field | Value | Meaning |
|---|---|---|
| `name` | `jcheng-applications` | application identity |
| `version` | `0.1.0` | must match `VERSION` |
| `contract.interfaces` | `>=0.1,<0.2` | pinned contract range |
| `contract.contract_hash` | `sha256:0b8d72…24b42` | must match the interfaces manifest |
| `safety.class` | `offline` | no intent is produced in v0.1.0 |
| `safety.scopes` | `[]` | no motion scope is claimed |
| `safety.requires_lease` | `true` | required by the schema; becomes binding at M3, when a gateway session holds the lease reference |
| `hardware_access` | `none` | hard boundary |
| `runtime` | empty | no ROS domain, no topics, no services |

Because the runtime surface is empty, admission means "no runtime object to
discover". The application is not a ROS node and never appears in a DDS graph;
the system layer does not need to arbitrate with it. M2 adds an HTTP/WS
transport; the runtime surface stays empty because HTTP is not a ROS interface.

The class and scopes change at M3 through a contract-first review: the gateway
adapter will submit intent, so the manifest will need a non-offline class and
the scopes its recipes actually use. v0.1.0 does not pre-claim them.

## 2. Gateway transport and contract

The application reaches the robot only through a `jcheng-system` gateway. There
is no second path: no SSH, no vendor SDK, no ROS topic, no serial device.

| Item | Rule |
|---|---|
| Base URL | Operator configuration at M2; no address is hard-coded or committed |
| Authentication | `Authorization: Bearer <token>` per the gateway OpenAPI; the token is read from operator configuration and never enters git |
| Health | `GET /v1/health` is unauthenticated liveness; it carries no motion authority |
| Contract | `GET /v1/contract` returns `ContractInfo`; a hash or range mismatch refuses the run |
| Safety | `GET /v1/safety` returns `SafetyState`; a run requires `ARMED_IDLE` and no stop latch |
| State | `GET /v1/state` returns `truth` (`measured` or `commanded`), `verification`, `fresh`, `profile_hash` and `header.stamp`. A `commanded` snapshot is never treated as measurement |
| Lease | The client session's lease reference is reported by the gateway; the system layer owns the lease and may revoke it |
| Intent validation | `POST /v1/motion/validate` for a dry validation when M2 needs it |
| Intent submission | `POST /v1/motion/execute` with the `Idempotency-Key` header; the gateway and system layer perform the motion |
| Stop | `POST /v1/motion/stop` after a failed or indeterminate submission |
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

The application performs no motion on its own and never holds the system's
control lease. It forwards intents to the gateway, where the system layer
applies the lease, deadman, limits and stop latch.

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

## 5. Failure semantics

| Condition | Required behavior |
|---|---|
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
| Stop path fails | run state preserved; `stop_error` recorded; operator notified |

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
- The repository ships no systemd unit for the robot and no DDS configuration.
  The laptop watchdog and its unit belong to `jcheng-ops`.
- A restart is not a motion command. The runner does not resume a run after a
  process restart; the operator must start a new, separately authorized run.

## 7. Verification stages

| Stage | Evidence |
|---|---|
| v0.1.0 admission and offline core | `python3 -m pytest -q`; `bash scripts/check_repo_gate.sh` |
| M2 read-only gateway client | `GET`-only health, contract, state and safety reads against a loopback mock (L2, 2026-11-26); contract-mismatch, auth, timeout and unreachable refusal; no retry; CI pending |
| M3 supervised execution | Paused by the system-side C2 probe; no submission path exists |
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
