# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Stop confirmation receipt (client-side, forward-compatible with the open R5
  contract proposal): `HttpGateway.stop` returns a `StopReceipt`. Missing R5
  fields are S1 `request_accepted`; S2 is recorded only for an explicit
  `confirmation: backend_confirmed`; `physical_stop` is limited to `unverified`.
  Both engines store `RunReport.stop_confirmation` / `physical_stop`, the run
  record and CLI persist them, and a failed stop leaves both `None` with
  `stop_error` set. No schema, tag or contract hash changed.
- `tests/test_stop_confirmation.py`: receipt parsing, invalid-field refusal, and
  S1/S2/failure/no-stop behaviour on the linear and tree engines.
- `jcheng-applications verify <run-id> --store-dir <dir>`: read-only record
  audit. It verifies the hash chain, requires a terminal `run_finished` entry
  and prints state, steps and stop confirmation levels. Exit 0 means the record
  verifies and is complete, not that the run succeeded; 1 covers
  missing/tampered/incomplete; 2 is usage. It makes no network call and does
  not create the store (`RunStore(create=False)`).
- `tests/test_audit.py`: complete, stop-level, tampered, unparseable,
  incomplete, missing and unsafe-id cases.
- L3 legacy compatibility check: the real `jcheng_system_core` mock gateway
  returns a `StopResponse` without R5 fields after a rejected step; the app
  records `request_accepted` / `unverified`, releases the lease and verifies the
  hash chain. Evidence: `evidence/20261126_stop_confirmation/`.
- Derived Plan identity guard: `run --derived-from <run-id>` requires a
  hash-chain-verified predecessor record, the same recipe name, a new recipe
  version, a new run id and a new authorization session. The guard runs before
  `check_mock`, lease acquisition or any gateway call; a violation raises
  `DerivedPlanError` and exits 2 with nothing submitted. The derived
  `run_started` record carries `derived_from`.
- `RunStore.load_started` (verified predecessor lookup) and
  `write_report(..., derived_from=...)`; `tests/test_derived.py` and
  `evidence/20261126_derived_plan/` cover the rules, a tampered chain and a
  no-gateway refusal.
- Terminal `WAITING_FOR_OPERATOR` semantics: `RunReport.operator_required` and
  `operator_reason`, set by both engines for unconfirmed/aborted outcomes, stop
  failure, lease loss and required-branch/deadline/cancel. The CLI prints the
  state and exits **3**; it has no motion authority and a resumption is a new
  authorized run. Records persist both fields.
- `tests/test_waiting.py` and `evidence/20261126_waiting/`: eight cases across
  both engines plus a loopback CLI exit-3 test.
- S3 v0 declared branches (recipe format 0.2, additive): optional `facts` and
  `branches`. A fact is a threshold on a positional
  `MotionResult.measured_positions[index]` from a main step and is
  three-valued; a branch is anchored after a main step with structured
  `{fact, equals}` conditions.
- Plan compilation gives branch steps their own `branch_id`/`branch_index`
  keys and a global report index; branch scopes join the recipe scope union.
  `Plan` carries `facts` and `branches`; the CLI `plan` output includes them.
- `core/facts.py` and `core/strategy.py`: fail-closed fact evaluation and a
  deterministic selector ordered by `(-priority, id)`. A selection record lists
  every candidate and guard; execution failure never evaluates a branch.
- The linear runner executes declared branches after a `CONTINUE` anchor,
  including `required` handling (`ABORTED` + one stop when no candidate) and a
  branch-step failure path. `RunReport` carries selection records; the run
  store persists them and `measured_positions`.
- `recipes/example-declared-branch.yaml`, `tests/test_branches.py` and
  `evidence/20261126_s3_branches/`: L3 against the real system mock gateway
  (fact `index 0=0.03`, guard `PASS`, branch selected, 2/2 steps).
- The tree engine now executes declared branches: `SelectionLeaf`, a py_trees
  `Selector` over per-branch sequences and a closed no-branch fallback. Four
  branch cases have exact linear/tree report parity; L3 evidence is
  `evidence/20261126_s3_tree/`; `tests/test_tree_branches.py`.
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

- S3 v0 runs on both engines. The tree engine evaluates the selector once per
  anchor, ticks only the selected branch, and propagates a selected branch
  failure instead of falling back. Facts are positional arm positions only;
  named-joint and gripper facts need a `jcheng-interfaces` change (proposal
  merged). A required branch with no candidate is a terminal
  `WAITING_FOR_OPERATOR` (`operator_required` + exit 3), not a live channel;
  a clean `REJECTED`/`FAILED` and a preflight refusal are not operator-required.
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
