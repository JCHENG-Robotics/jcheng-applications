# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- M1.5 opt-in production tree engine: `src/jcheng_applications/tree/runtime.py`
  runs a `py_trees` `Sequence` over the shared `StepExecutor` with one daemon
  worker (one in-flight step), a `StepLog` that separates completed and late
  outcomes, and an independent `RunSupervisor` thread owning the run deadline,
  cancellation and the one stop.
- `jcheng-applications run --engine tree --deadline-s ...`: the tree engine is
  selected explicitly; the default remains linear. A deadline/cancel with a
  step in flight yields `UNKNOWN` and `INDETERMINATE`; a late `COMPLETED` is
  evidence only.
- Optional `tree` extra pinning `py_trees==2.6.0`, `pydot==4.0.1` and
  `pyparsing==3.3.3`; CI installs it, the default gate does not need it, and
  the tree tests skip on a bare interpreter.
- `tests/test_tree_runtime.py` and `evidence/20261126_m1_5_tree/`: a 4-case
  differential against the linear engine, rejection/one-stop, deadline/late
  result, and an L3 run against the real system mock gateway (3/3 steps).
- T-C06 in `tests/test_tree_regression.py`: 12 scripted cases with a fixed
  clock assert exact `RunReport` equality between the linear and tree engines,
  including steps, abort reason and stop error. Evidence:
  `evidence/20261126_t_c06/`.
- M3 supervised execution on the mock gateway: `write_transport.py` implements
  lease acquire/renew/release, one execute per step and one stop; it refuses a
  gateway whose health does not advertise `mock`. `lease.py` renews at
  `ttl/3`, releases on every terminal path, and blocks submissions after a
  renewal failure. `runstore.py` writes append-only, hash-chained JSONL records
  (mode 0600) outside the repository. `supervised.py` composes lease, runner
  and record.
- `jcheng-applications run`: the first command that submits motion. It requires
  an operator `Authorization` JSON, a `JCHENG_GATEWAY_TOKEN`, a mock gateway
  and a store outside the repository; it exits 2 if the record cannot be
  written even when the run finished.
- `authorization_from_mapping`: strict parser for the operator record (unknown
  fields, non-boolean approval, unknown or duplicate scopes are rejected).
- `capability.yaml` moves to `safety.class: motion_source` with scopes
  `arm`/`gripper`, `requires_lease: true`; the runtime surface stays empty
  because this is an HTTP gateway client, not a DDS node.
- `tests/test_write_transport.py`, `tests/test_supervised.py`: loopback
  coverage of lease lifecycle, renewal failure, mock-only refusal, stop on
  rejection, record chaining/tampering and record-write failure.
- `evidence/20261126_m3_supervised/`: L3 evidence against the real
  `jcheng-system` gateway process with `MockStack` (3/3 steps, lease released,
  record verified; no robot).
- `docs/TASK_ENGINE.md`: design for a tree-based task engine, a shared
  Execution Core, an independent Run Supervisor, declared recovery branches,
  three-valued guards and a deterministic selector. Design only: no runtime,
  no transport and no hardware path.
- Roadmap M1.5 (offline task-engine validation, parallel to M2) and S3
  (declared branches and selection).
- `transport.py`: M2 read-only gateway client. Four GET reads (`/v1/health`,
  `/v1/contract`, `/v1/state`, `/v1/safety`), bounded connect/read timeouts,
  strict parsing, no retry, no redirect and a size bound. The token is sent
  only to authenticated endpoints.
- `jcheng-applications check`: read-only admission; prints the health,
  contract and safety report and exits non-zero when the gateway is not
  ready. No submission and no motion.
- `tests/test_transport.py`: loopback mock coverage for GET-only behavior,
  auth scoping, contract mismatch, timeout, unreachable, malformed and
  oversized responses, and no retry.
- The static boundary test now allows `http.client` in `transport.py` only
  and proves by AST that the adapter is GET-only.
- `scripts/m1_5_pytrees_probe.py` and `docs/M1_5_VALIDATION.md`: isolated
  validation of `py_trees==2.6.0` in a disposable venv. Eleven checks pass:
  non-blocking `RUNNING`, sequence memory, `terminate(INVALID)`, late future,
  hung worker, app-side stop race, single worker, nominal call parity, an
  independent supervisor thread (M15-J) and an 11-case failure differential
  (M15-K). After the core extraction, both drivers use the same
  `StepExecutor`/`classify`.
- The probe found that py_trees 2.6 declares `pydot>=1.4` (resolved 4.0.1 plus
  pyparsing); the licence review records it. It is not a runtime dependency.

### Changed

- The default execution engine stays linear. The py_trees engine is opt-in and
  the `LICENSE-REVIEW.md` review of py_trees/pydot/pyparsing remains open; the
  engine is not promoted to the default in this change.
- `RECIPES.md` records the deployment constraint found in L3: the system
  profile accepts exactly one joint per `JOINT_STEP`; a multi-joint step is
  syntactically valid but rejected by the gateway. The shipped offline example
  remains multi-joint and is not a hardware program. The documentation no
  longer lists `max_acceleration_rad_s2` for `JOINT_STEP`; the validator never
  accepted it.
- M3 is complete on the mock; the deployed gateway and R5 remain M4
  prerequisites. The application never works around a gateway stop race.
- Extracted the Shared Execution Core into `src/jcheng_applications/core/`:
  preflight, command construction, terminal classification, one submission per
  step and stop-once. `runner.py` delegates to it with an unchanged public API
  and the 89 v0.1.0 tests still pass. The M1.5 tree driver now calls the same
  `StepExecutor`/`classify`, so the 11-case failure differential is structural
  rather than a mirror. T-C06 is partially verified: the behavioural fields
  match, the full `RunReport` object is not yet compared.
- The roadmap records M2 as the mainline and states that M1.5 must not delay
  it, and that gateway concurrency probes are a prerequisite for hardware.
- M3 is unblocked on the mock: the system-side R1–R4 revision now passes
  C1–C5, C5b and the stop/execute stress loop (r2 evidence). M3 is still not
  implemented; the deployed gateway must repeat the probes before M4.

## [0.1.0] — 2026-11-26

### Added

- Initial repository scaffold: bilingual documentation set, admission
  manifest, repository gate, no-hardware/no-DDS/no-network static scan and
  `applications-gate` CI.
- Offline recipe model and validator for the `jcheng-interfaces` v0.1.0
  `MotionCommand` contract, including per-kind payload rules, scope checks and
  the bus-verification requirement for arm and gripper steps.
- Deterministic planner with run-bound SHA-256 idempotency keys.
- Fail-closed runner over an abstract `GatewayPort`: explicit per-session
  authorization, contract and safety preflight, one submission per step, no
  retry, stop request after a failed or indeterminate step, and a `RunReport`
  that preserves the gateway's reason codes.
- Offline `jcheng-applications validate` and `jcheng-applications plan` CLI.
- Example recipe `recipes/example-supervised-transfer.yaml`, explicitly marked
  as an uncalibrated offline example.
- Architecture, integration and recipe documents covering the L4 boundary,
  gateway contract, authorization rules and failure semantics.

### Security

- The tree contains no gateway token, operator environment file, model weight
  or recorded data; the gate rejects them.
