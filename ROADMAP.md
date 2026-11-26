# jcheng-applications roadmap

> Status: **v0.1.0** as of 2026-11-26. The offline orchestration core is
> complete; the gateway transport, persisted run records and hardware
> acceptance are not.

## Current state

| Item | State | Evidence |
|---|---|---|
| Admission manifest (`capability.yaml`) | Complete (2026-11-26) | `capability.yaml`; `tests/test_contract.py` |
| Recipe model, validation and deterministic planning | Complete (2026-11-26) | `src/jcheng_applications/model.py`; `tests/test_recipe.py` |
| Fail-closed runner over `GatewayPort` | Complete (2026-11-26) | `src/jcheng_applications/runner.py`; `tests/test_runner.py` |
| Offline `validate` / `plan` CLI | Complete (2026-11-26) | `src/jcheng_applications/cli.py`; `tests/test_cli.py` |
| Offline example recipe | Complete (2026-11-26) | `recipes/example-supervised-transfer.yaml` |
| Repository gate and CI | Complete (2026-11-26) | `scripts/check_repo_gate.sh`; `.github/workflows/ci.yml` |
| HTTP/WS gateway client | Planned | — |
| Session and lease binding, persisted run records | Planned | — |
| Supervised execution of one recipe through a gateway | Planned | — |
| Hardware acceptance | Not started | — |

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M0 | Repository scaffold and admission manifest | Complete (2026-11-26) | The document set, gate and CI `applications-gate` pass; the manifest claims no runtime surface | `evidence/20261126_scaffold/` |
| M1 | Offline orchestration core | Complete (2026-11-26) | Recipes validate against the v0.1.0 contract; plans are deterministic; the runner refuses bad authorization, contract, safety and lease preconditions and never retries an indeterminate step; no transport exists | `src/jcheng_applications/`; `tests/test_recipe.py`, `tests/test_gateway.py`, `tests/test_runner.py` |
| M2 | Read-only gateway client and admission | Planned | The client implements `GET /v1/health`, `/v1/contract`, `/v1/state` and `/v1/safety` against the published OpenAPI; a mock gateway proves contract-mismatch and unreachable-gateway refusal; no intent is submitted | — |
| M3 | Supervised execution of one recipe | Planned | A session-bound run acquires and releases the system lease, submits one recipe with stable idempotency keys, honours stop and pre-emption, and produces a persisted run report; proven against a mock gateway (L3), no robot | — |
| M4 | Supervised hardware acceptance | Planned | Every motion session is separately authorized; the system layer applies the lease, limits and stop latch; L5 evidence is recorded alongside the system layer's own | — |

## Next

1. **M2 — read-only gateway client and admission.** Implement the HTTP/WS
   adapter behind `GatewayPort`, with a strict parser for `ContractInfo`,
   `SafetyState` and the event stream. Prove `GET`-only behavior, bounded
   timeouts, contract-hash refusal and no fallback against a mock gateway.
   Prerequisites: `jcheng-interfaces` v0.1.0 and a `jcheng-system` gateway
   contract; no robot session is required.
2. **M3 — supervised execution of one recipe.** Add the session and lease
   binding, the append-only run record and the stop/pre-emption path. The
   recipe still runs only against a mock gateway; an L3 report is required
   before any robot session is proposed.

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
- Automatic retry of a rejected, aborted, timed-out or unverified step.
- Storing tokens, run databases, recordings or model weights in git.

## Dependencies

- `jcheng-interfaces` `>=0.1,<0.2`; contract hash
  `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42`.
- `jcheng-system` API `>=0.1,<0.2`; the gateway is the only robot-facing path
  and the system layer is the only origin of motion.
- `jcheng-studio` owns the human-facing session authorization and diagnostics.
  This repository consumes an `Authorization` record; it does not authenticate
  an operator by itself.
- `jcheng-ops` owns the unattended watchdog and restart policy.
- Real-robot work requires separate, explicit operator authorization per
  session and is never triggered by CI or unattended runs.
