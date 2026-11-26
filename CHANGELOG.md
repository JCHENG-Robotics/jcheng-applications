# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
