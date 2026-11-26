# Task engine and hybrid strategy selection — design

> Status: **In progress** as of 2026-11-26. Design revision 2.
> The technical direction was approved by the operator on 2026-11-26 with ten
> normative revision requirements; §2 maps each one to the section that
> answers it.
>
> **Implementation is partial.** M2's read-only transport is in
> `src/jcheng_applications/transport.py`; the Shared Execution Core is in
> `src/jcheng_applications/core/` and `runner.py` delegates to it; the M1.5
> probe and report are in `scripts/m1_5_pytrees_probe.py` and
> [`M1_5_VALIDATION.md`](M1_5_VALIDATION.md). The production tree runtime, Run
> Supervisor, branches and selection are **not** implemented.
>
> M2 remains the mainline. M1.5 is a parallel, isolated validation and must not
> delay M2. **M3 is unblocked on the mock**: the system-side C2/C5/C5b revision
> passed (R2 evidence in `jcheng-system/e2e/reports/20261126_concurrency_probe_r2/`);
> M3 is still not implemented. The deployed gateway must repeat C1–C5 before
> M4. No hardware execution is opened before M4, and every M4 session needs
> separate, explicit operator authorization. Offline evidence and hardware
> evidence are never mixed.

## 1. Scope

| In scope for this design | Out of scope (v0.2) |
|---|---|
| A tree-based executor over the existing `GatewayPort` | Any DDS, ROS, `rclpy`, `ros2` CLI or device path |
| A shared Execution Core used by both runners | Automatic retry of any motion |
| A Run Supervisor independent of the tree | Navigate / base localization / docking |
| Declared recovery branches and a deterministic selector | Perception closed loop at runtime |
| Three-valued guards and selection records | Learned policies producing pose, joint targets or action chunks |
| Append-only run evidence | A live operator channel inside this process |
| Offline mock validation (M1.5, M2, M3, S3) | Hardware claims without a separately authorized M4 session |

The engine executes intents; it never executes motion. The system layer remains
the only origin of motion, and the gateway remains the only path to it
(see [ARCHITECTURE.md](ARCHITECTURE.md) §1 and [INTEGRATION.md](INTEGRATION.md) §2).

## 2. Normative requirements and traceability

| ID | Requirement (operator, 2026-11-26) | Decision in this design | Section |
|---|---|---|---|
| N1 | Non-blocking leaves; `RUNNING` tracks the synchronous gateway request; cancel and stop on an independent supervision path; verify the gateway has effective concurrent stop semantics | Single-worker submit path; leaf polls a future and returns `RUNNING`; supervisor owns its own transport connection and issues stop independently; concurrency probes C1–C5 are a **prerequisite**, not an assumption | §6, §7, §15, §17 K1 |
| N2 | Linear Runner and BT Runner share one Execution Core | `core/` owns preflight, command building, classification, stop policy and report assembly; **sequencing:** extraction lands after M2 stabilizes and the M1.5 differential passes, as one refactor. Neither route refactors `runner.py` before that | §5 |
| N3 | Differential tests compare gateway call sequence, count, parameters, idempotency keys, stop behaviour and report semantics — not only `StepReport` | A call-log gateway recorder is the oracle; the differential matrix asserts full call logs and final reports | §16 T-D |
| N4 | No automatic motion retry in v0.2; `UNKNOWN`, `TIMEOUT` and unconfirmed results block later motion | Core-level `motion_blocked` latch, set by the supervisor and enforced before command construction; no retry decorator exists | §5, §7, §9 |
| N5 | Recipe → Plan remains the only compilation entry; recovery uses declared branches or a separately authorized Derived Plan | Compiled `Plan` carries declared branches with their own keys; a Derived Plan is a new recipe revision, new run id and new authorization | §8 |
| N6 | Lease, deadline, cancellation and evidence are owned by a Run Supervisor independent of ordinary tree branches | Supervisor thread with its own state machine, transport connection, monotonic deadline and evidence sink | §7, §12 |
| N7 | Human takeover is `WAITING_FOR_OPERATOR` and carries no motion authority by default | A supervisor state that stops, latches, releases and exits; resumption is a new authorized run | §11 |
| N8 | Guards stay three-valued and fail-closed; the selector must not turn an execution failure into the next motion attempt | Branch guards read task facts derived from **verified** measurements; execution outcomes never select a branch | §9 |
| N9 | Learned strategies may propose skills and parameters only, with room for a future versioned contract | `SkillProposal` v0.2 accepts `params` and `skill` at compile time only; `pose`, `joint_target` and `action_chunk` are reserved contract items | §10 |
| N10 | Navigate, perception closed loop and dynamic recovery may not bypass existing contracts; new contracts need a design review first | Contract review trigger table; every item is out of scope until `jcheng-interfaces` ships the schema and this document is revised | §13.3 |

## 3. Constraints carried in from the ecosystem

- L4 calls the gateway over HTTP/WS only. No DDS domain (0, 41, 42, 43), no
  `rclpy`, no `ros2` CLI, no vendor topic, no device, no SSH, no vendor SDK.
- `hardware_access: none` and an empty `runtime` surface stay in
  `capability.yaml` until M3 passes the contract-first review.
- `/joint_states` is a command cache, never measurement. Completion truth is
  `MotionResult.bus_verified` plus `measured_positions`, both from the gateway.
- The application is not an e-stop. The system layer's stop latch is a software
  stop; no brake, encoder or self-collision model exists.
- Every run requires a per-session `Authorization` record. The application
  trusts the record; `jcheng-studio` owns the human interaction.
- Cross-repository contracts live only in `jcheng-interfaces`. A field or
  endpoint this design needs that is absent there is a contract review item,
  not a private extension.

## 4. Architecture

```text
CLI (operator)
  │
  ├─ Recipe loader ──► Compiler ──► Plan (steps + declared branches + plan hash)
  │                                   │
  │                                   ▼
  ├─ TreeRuntime (py_trees) ──tick──► Leaves ──► Execution Core ──► GatewayPort
  │        ▲                              │                         (submit path,
  │        │ events / halt               │                          own connection)
  │        │                              ▼
  └─ Run Supervisor ─────────────► stop / lease / evidence ────► GatewayPort
           (independent thread, own connection, monotonic deadline)
```

Two independent paths to the gateway are deliberate:

| Path | Owner | Purpose | Concurrency |
|---|---|---|---|
| Submit path | Execution Core, one worker thread | `validate` and `execute` for one plan step at a time | At most one in-flight intent |
| Supervision path | Run Supervisor, its own thread and connection | `stop`, lease renew/release, safety reads | Must remain responsive while a submit is blocked |

The design is only valid if the gateway processes the supervision path while an
`execute` request is in flight. That is **not verified today** (§17 K1); the
concurrency probes in §15 are a gate for M3, not a post-hoc check.

### 4.1 Components

| Component | Runs in | Responsibility | Must not |
|---|---|---|---|
| Compiler | Process, pure | Recipe + authorization + run id → immutable `Plan`, plan hash, branch keys | Perform I/O, read the clock, generate randomness |
| Execution Core | Process, no transport of its own | Preflight, command construction, one submission, result classification, stop-once, report assembly | Know about trees, `py_trees`, status enums or UI |
| TreeRuntime | Process, one thread | Tick the plan tree at a bounded rate, poll pending work, expose status | Talk to the gateway directly, construct commands, retry |
| Run Supervisor | Process, own thread | Lease lifecycle, run deadline, cancellation, stop-once, motion-block latch, evidence, operator wait | Drive the tree's normal branches or interpret `py_trees` statuses |
| Strategy registry | Process, data | Built-in, versioned branch templates and fact projections | Load plugins, evaluate code, rank by anything learned in v0.2 |
| Transport adapter (M2) | Process, HTTP/WS | Implement `GatewayPort`, enforce connect/read timeouts, no fallback | Make safety decisions or retry |
| Evidence sink | Process, file | Append-only hashed records outside the repository | Store tokens, recordings or run data in git |

## 5. Execution Core

The shared core is extracted. M2 is merged and the M1.5 differential passed, so
`runner.py` now delegates to `core/` while keeping its public API and the 89
v0.1.0 tests unchanged. The linear runner and the M1.5 tree driver call the
same `StepExecutor` and `classify`; authorization, lease, safety, submission
and failure handling have one implementation and cannot drift. T-C06 is
partially verified: the 11-case behavioural differential passes, while the full
`RunReport` object is not yet compared.

```text
core/
  preflight.py   # authorization, contract, safety and lease checks (pure)
  command.py     # build_command; the only place a MotionCommand is constructed
  classify.py    # (step, MotionResult) -> Decision; pure and exhaustively tested
  stop.py        # StopOnce: at most one stop request per run
  report.py      # StepReport / RunReport assembly
  executor.py    # StepExecutor.submit(step, context) -> StepOutcome
```

```python
@dataclass(frozen=True)
class RunContext:
    run_id: str
    lease_id: str
    authorization: Authorization
    contract: ContractInfo
    deadline_monotonic: float
    motion_blocked: threading.Event   # set by the supervisor only

class StepExecutor:
    def submit(self, step: PlanStep, ctx: RunContext) -> StepOutcome:
        """Build, submit exactly once, classify. Never retries."""
        if ctx.motion_blocked.is_set():
            raise MotionBlocked("a previous outcome is unconfirmed")
        ...
```

Invariants owned by the core, independent of the driver:

- **One submission per step, ever.** No loop, no backoff, no retry helper.
- **`motion_blocked` is checked before the command is built.** A tree bug, a
  selector bug or a future driver cannot submit after an unconfirmed outcome.
- **Stop-once.** The first non-success after a submission requests one stop; a
  failed stop is recorded, never retried.
- **No fabricated result.** `StepOutcome` copies the gateway's `outcome`,
  `reason_code`, `message`, `bus_verified` and `measured_positions`; a missing
  field stays missing.

The linear `TaskRunner` becomes a driver over `StepExecutor` and stays in the
tree as the differential oracle. It is not a second production path after M3.

## 6. Tree runtime and non-blocking leaves

`py_trees` 2.6 (BSD-3, pure Python, no ROS) is the candidate runtime. It is
imported by `tree/` only; the rest of the package does not depend on it. It is
**not** dependency-free: it declares `pydot>=1.4` (the 2026-11-26 probe
resolved pydot 4.0.1 plus pyparsing). pydot is used for rendering, not ticking,
and must be locked with it. If the dependency is rejected at review, the
fallback is a small internal runtime with the same leaf protocol — not
BehaviorTree.CPP, which is C++ with no official Python bindings and would put a
second toolchain in an HTTP-only L4 repository.

Tick rate: 20 Hz nominal, monotonic clock, bounded jitter. A tick never blocks
on the network.

### 6.1 Leaf protocol

```python
class SubmitStep(Behaviour):
    def initialise(self):
        self._future = self._worker.submit(self._core.submit, self._step, self._ctx)
        self._deadline = monotonic() + self._step_timeout_s

    def update(self):
        if self._future.done():
            outcome = self._future.result()       # never raises; core maps errors
            self._decision = outcome.decision
            return Status.SUCCESS if outcome.decision is Decision.CONTINUE else Status.FAILURE
        if monotonic() > self._deadline:
            self._supervisor.request_stop("step deadline exceeded")
            return Status.RUNNING                 # the terminal result is still the truth
        return Status.RUNNING

    def terminate(self, status):
        if status is Status.INVALID and not self._future.done():
            self._supervisor.cancel_in_flight()   # stop + INDETERMINATE
```

Rules:

- A leaf returns `RUNNING` while the gateway call is pending. It never sleeps.
- **One worker, one in-flight submit** per run (`max_workers=1`). Structural, not
  conventional, exclusion of parallel motion.
- Cancellation of an in-flight `execute` is **not** a clean cancel. It sets
  `motion_blocked`, issues one stop and makes the step `INDETERMINATE`.
- `terminate()` must drain the worker or hand it to the supervisor with a
  bounded grace period. An abandoned future is a motion with an unknown
  outcome and a process that cannot exit cleanly.
- The transport adapter must bound connect and read timeouts. An unbounded
  socket call makes the drain guarantee impossible; this is an M2 acceptance
  criterion.

### 6.2 Leaves

| Leaf | Calls | Notes |
|---|---|---|
| `Preflight` | core preflight | One-shot; no submission |
| `SubmitStep` | core submit | The only motion leaf; one per plan step |
| `EvaluateFacts` | fact projections | Pure; reads `measured_positions` from verified results |
| `SelectBranch` | strategy selector | Pure; may select only declared branches |
| `WaitForSupervisor` | supervisor state | Used for `WAITING_FOR_OPERATOR`; returns `FAILURE` on halt |
| `Record` | evidence sink via supervisor | Always-run; also on failure and abort |

`Record` is not inside the Verify subtree. It runs for every terminal path,
including preflight refusal, tree exception and operator cancellation.

## 7. Run Supervisor

The supervisor is a separate thread with its own state machine and its own
transport connection. It never depends on the tree thread for progress.

| Responsibility | Detail |
|---|---|
| Lease | Acquire at preflight (M3), renew at `ttl/3`, release on terminal; a lost lease latches motion and stops |
| Deadline | Wall-clock run deadline on a monotonic clock; on expiry, stop, then classify from the terminal result |
| Cancellation | Operator request or deadline; halts the tree, drains the worker, sets the latch |
| Stop | At most one stop per run, on its own connection, even while a submit is blocked |
| Latch | Sets `motion_blocked` on any unconfirmed outcome, cancellation in flight, lease loss or stop failure |
| Evidence | Writes every state transition and step result, and a final record with the report hash |
| Operator wait | Enters `WAITING_FOR_OPERATOR`; see §11 |

Supervisor state machine:

```text
INIT ──► PREFLIGHT ──refusal──► CLOSED (ABORTED, nothing submitted)
             │ pass
             ▼
          RUNNING ──success──► CLOSED (SUCCEEDED)
             │  │
             │  ├─ failure ──► CLOSED (FAILED / ABORTED)
             │  │
             │  ├─ UNKNOWN / TIMEOUT / unverified / stop failure / lease loss
             │  │                  └──► STOPPING ──► WAITING_FOR_OPERATOR
             │  │
             │  └─ cancel / deadline ──► STOPPING ──► CLOSED or WAITING_FOR_OPERATOR
             ▼
         (all terminal paths release the lease if held, write evidence, close)
```

`motion_blocked` is set before entering `STOPPING` and is never cleared inside
the process. A new run is a new process invocation, a new authorization and a
new lease.

The supervisor also enforces the **run deadline even when the tree is healthy**:
a plan that idles in a branch beyond the deadline is stopped, not extended.

### 7.1 Stop confirmation levels

An HTTP 200 from `/v1/motion/stop` means the gateway accepted the request. It
does **not** mean the machine stopped. The engine records three distinct levels
and never upgrades one into another:

| Level | Meaning | Evidence | May the app claim it? |
|---|---|---|---|
| S1 request accepted | The gateway returned 200 and latched safety | HTTP status + `StopResult` | Yes, as "stop accepted" |
| S2 backend stop confirmed | The gateway's hold/zero command returned and the session is terminal `ABORTED`; no further motion will be issued | Gateway terminal result / confirmation field (contract proposal) | Yes, as "backend confirmed" |
| S3 physical stop | The arm or base actually stopped | Not measurable on this platform: no e-stop, brake, encoder or force sensing | **Never.** No evidence exists |

Consequences:

- A run report, CLI line or Studio message must name S1 or S2, never "stopped"
  unqualified and never S3.
- A `COMPLETED` after an accepted stop is a **system defect**, not an app
  condition. The app fails the run and surfaces the contradiction; it does not
  rewrite the outcome, suppress the late result or retry. The 2026-11-26 C2
  probe is exactly this case.
- The supervisor's `stop_error` is about S1/S2 only. A failed stop request is
  recorded as failed; it is never reported as a successful physical stop.

## 8. Plan compilation, declared branches and Derived Plans

### 8.1 One compilation entry

`Compiler.compile(recipe, authorization, run_id)` is the only function that
produces a `Plan`. Tree nodes receive frozen `PlanStep` objects and cannot
construct steps, alter parameters or mint keys.

```text
Plan:
  run_id, recipe_hash, contract_hash, plan_hash
  steps:    [PlanStep(index, name, kind, profile, params, scopes, key)]
  branches: {branch_id -> Branch(after_step, required, conditions, steps[], scope_union)}
  facts:    {fact_id -> FactProjection(source_step, projection)}
```

- `recipe_hash`: SHA-256 over the canonical validated recipe.
- `plan_hash`: SHA-256 over the compiled plan, including branch steps and keys.
- Main-step keys keep the v0.1.0 input (`run_id, index, name, kind, profile,
  params`) so existing golden hashes stay valid.
- Branch-step keys add `branch_id` and `branch_index`; they are distinct from
  every main-step key and deterministic per run.

### 8.2 What may trigger a branch

A branch is eligible only when its **anchor step completed with a verified bus
read** and its declared fact projections evaluate `PASS`. Facts are derived
from `measured_positions` (for example, a gripper joint at or below a declared
angle) or from run parameters. Facts never include `MotionResult.outcome`.

| Event | May select a branch? | Why |
|---|---|---|
| Step `COMPLETED` + `bus_verified: true`, fact predicate true | Yes, if declared | The measured state is known; the motion is a new, pre-authorized intent |
| Step `COMPLETED` without bus verification | No | `INDETERMINATE`; motion is blocked |
| `REJECTED`, `ABORTED`, `TIMEOUT`, `UNKNOWN`, malformed result | No | Execution failure is terminal; a second motion is not a retry |
| Operator cancellation or deadline | No | The run is stopping |
| Guard fact `UNKNOWN` | No | Fail-closed |

This is the concrete reading of N4 and N8: **v0.2 has zero automatic motion
after any execution failure**, and a selector can never convert a failure into
an attempt.

`measured_positions` enters through the strict parser that already validates
`MotionResult`: finite numbers, known joint names, one value per joint. A
missing, non-finite or unknown-joint value makes the fact `UNKNOWN`, never
zero and never a guess.

### 8.3 Declared branch format (proposed, not implemented)

Additive recipe fields, reviewed at S3. v0.1.0 recipes remain valid; the
`jcheng-interfaces` contract hash does not change because commands are
unchanged.

```yaml
facts:
  gripper_closed:
    from_step: close-gripper
    measured: { joint: gripper_joint, at_most_rad: 0.15 }

branches:
  - id: grasp.re-approach.v1
    after: close-gripper
    required: false
    when:
      - { fact: gripper_closed, equals: true }
    steps:
      - name: reopen
        kind: GRIPPER
        profile: gripper
        scopes: [gripper]
        params: { gripper: { position_rad: 1.0, max_velocity_rad_s: 0.5, duration_s: 1.0, verify_bus: true } }
```

Rules:

- `after` must name an earlier main step; a branch cannot anchor on another
  branch (no branch chains in v0.2).
- Branch steps are compiled at plan time and count toward the recipe's scope
  union. `Authorization` must cover them, otherwise the recipe is rejected
  before any gateway call. There is no mid-run scope widening.
- `required: true` with no eligible branch enters `WAITING_FOR_OPERATOR`.
  `required: false` continues the main plan.
- Conditions are structured comparisons over an allowlisted fact vocabulary.
  No expression strings, no `eval`, no callable names from data.

### 8.4 Derived Plan

A Derived Plan is the only way to do something the compiled plan did not
declare. It is not a runtime recovery; it is a new run.

| Rule | Enforcement |
|---|---|
| New recipe revision (different `version`) when parameters change | Compiler records and compares versions; evidence keeps the predecessor run id |
| New `run_id` | Keys differ; no key reuse |
| New `Authorization` with a new `session_id` and `approved_at` | Preflight refuses a reused record |
| No mutation of a live plan | The tree is halted and rebuilt; the old process exits |
| Operator initiated, never automatic | No code path from a failure handler to a new compile |

## 9. Strategy selection and guards

A strategy is a declared way to reach one task goal. In v0.2 every strategy is
a compiled branch; the registry is built-in, versioned and data-only.

```python
@dataclass(frozen=True)
class Strategy:
    id: str                 # "grasp.re-approach.v1"
    goal: str               # "grasp"
    branch_id: str          # must exist in Plan.branches
    risk_class: str         # R0 observe / R1 declared motion (both pre-authorized)
    priority: int           # author-declared, not learned
    conditions: tuple[Condition, ...]
```

Guard evaluation is three-valued:

| Value | Meaning | Selectable |
|---|---|---|
| `PASS` | The fact is present, fresh and satisfies the comparison | Yes |
| `FAIL` | The fact is present and violates the comparison | No |
| `UNKNOWN` | The fact is missing, stale, unparseable or out of range | **No** |

Selection algorithm:

1. Filter to branches declared in the plan for this anchor.
2. Evaluate every condition; `UNKNOWN` fails closed.
3. Rank eligible branches by `(risk_class, priority, id)` — stable and
   author-declared, never inferred from past failures.
4. Select the first; if none is eligible and the branch set is `required`,
   enter `WAITING_FOR_OPERATOR`; otherwise continue the main plan.
5. Write a selection record listing every candidate, every guard value, the
   chosen branch and the reason.

The selector has no access to `StepReport.outcome`, `RunState` or any failure
history. N8 is enforced by the type of its input, not by discipline.

## 10. Learned policy boundary

v0.2 accepts one proposal artifact, consumed at **plan compile time**:

```yaml
kind: params            # or "skill"
goal: grasp
strategy: grasp.re-approach.v1
params: { approach_offset_m: 0.010 }
policy_hash: sha256:...
confidence: 0.82
ood_score: 0.11
produced_at: 2026-11-26T10:00:00+08:00
```

| Rule | Detail |
|---|---|
| Allowed kinds | `params`, `skill` |
| `params` | May set only keys declared in the target strategy's schema; unknown keys reject the proposal |
| `skill` | May name only a registered strategy; it cannot add a step or a branch |
| Hash | `policy_hash` is mandatory; a missing or mismatching hash refuses the proposal |
| Authority | Confidence and OOD scores may demote or disable a strategy; they may never promote one past a guard |
| Runtime | No runtime proposal path in v0.2. Proposals are files attached to a compile, recorded in the plan |
| Reserved | `pose`, `joint_target`, `action_chunk` require a `jcheng-interfaces` schema, a safety review, a contract bump and a revision of this document |

`jcheng-learning` stays offline in this design. It produces the artifact; it
never joins the application's process, never calls the gateway and never gains
a motion scope.

## 11. Human takeover

`WAITING_FOR_OPERATOR` is a supervisor state with **no motion authority**.

| Aspect | Rule |
|---|---|
| Entry | `UNKNOWN`/`TIMEOUT`/unverified completion, stop failure, lease loss, required branch with no eligible candidate, operator cancel |
| On entry | Stop once (if anything was submitted), halt the tree, drain the worker with a bounded grace, release the lease if held, set `motion_blocked`, write evidence |
| Authority | None. The state cannot submit, validate, arm, reset or clear the latch |
| Exit | Operator closes the run (`CLOSED`), or starts a new run with a new authorization |
| No resume | v0.2 has no in-process resume; a restart is not a motion command |
| Future | A live handover channel (studio ↔ applications) is a contract review item, not part of v0.2 |

The CLI must print the state, the run id, the evidence path and the last gateway
reason code, and exit non-zero. It must not prompt for "retry".

## 12. Evidence

Every run writes one append-only JSONL file outside the repository.

| Field group | Content |
|---|---|
| Identity | `run_id`, `recipe_hash`, `plan_hash`, `contract_hash`, `authorization_hash`, `supervisor_version` |
| Program | step ids, kinds, profiles, canonical parameter hashes, idempotency keys (hashes), intent ids |
| Supervision | state transitions with monotonic and wall-clock timestamps, deadline, cancel source |
| Steps | gateway outcome, reason code, `bus_verified`, measured positions used as facts |
| Selection | candidates, guard values, chosen branch, reason |
| Stop | attempt count (0 or 1), result, error |
| Terminal | final run state, report hash, predecessor run id if any |

- Each record carries `seq`, `prev_digest` and `digest`
  (`sha256(prev_digest || canonical_record)`), so truncation and edits are
  detectable.
- Directory `0700`, file `0600`, token never written; `lease_id` is stored as a
  hash.
- Evidence lives under the operator's state directory, never in git; the
  repository gate continues to reject databases and recordings.
- Mock, replay and hardware records carry an explicit `evidence_class`
  (`L2`, `L3`, `L5`). A mock record is never relabelled.

## 13. Interface boundaries

### 13.1 Internal modules

| Module | May call | Must not call | Enforcement |
|---|---|---|---|
| `model`, `compile` | stdlib | clock, I/O, network, gateway | pure-function review; tests with a frozen input |
| `core` | `GatewayPort`, `threading` | `tree`, `py_trees`, transport adapter internals | import-graph test |
| `tree` | `py_trees`, `core`, supervisor handle | `GatewayPort` directly, transport, device | import-graph test; leaf review |
| `supervisor` | `GatewayPort` (own client), evidence sink | `tree` internals, `py_trees` | import-graph test |
| `strategy` | dataclasses | `core`, `gateway`, `tree` | pure data; no callables from data |
| `transport` (M2) | HTTP/WS library | `core` decisions, `tree` | adapter tests; no retry |
| `cli` | composition root | motion before M3 | CLI review |

Dependency direction is one-way: `cli → tree/supervisor → core → gateway
protocol → transport adapter`. `tree` and `supervisor` are siblings; neither
imports the other's internals.

### 13.2 External boundaries

| Peer | Boundary | This design |
|---|---|---|
| `jcheng-system` gateway | HTTP/WS, OpenAPI v1 | Uses only documented endpoints; no fallback, no private endpoint |
| `jcheng-interfaces` | schemas + contract hash | Read-only; any new field is a contract change first |
| `jcheng-studio` | authorization record + future operator channel | Consumes a record; owns no human UI here |
| `jcheng-learning` | proposal artifact | Compile-time file only; no runtime link, no DDS |
| `jcheng-ops` | none | No interaction; no systemd unit, no watchdog ownership |

### 13.3 Contract review triggers

Any of these needs a `jcheng-interfaces` change, a contract bump, a
`capability.yaml` update and a revision of this document **before** code:

| Trigger | Why it is needed | Status |
|---|---|---|
| Concurrent stop guarantee while `execute` is in flight | N1 depends on it; if the gateway serializes, the supervision path cannot pre-empt | To verify (probe C2); owner `jcheng-system` |
| Bus state read outside a `MotionResult` | Facts and verification without a preceding motion | Not requested; would add an endpoint |
| Mobile / Navigate skill through the gateway | The base has no odometry and `BASE_PULSE` is not navigation | Not requested; out of scope |
| Perception target or observation read | Closed-loop manipulation | Not requested; out of scope |
| Pose / joint target / action chunk proposals | N9 reserved kinds | Not requested; future |
| Live operator handover channel | Live takeover instead of a new run | Not requested; studio-owned |

## 14. State machines

### 14.1 Leaf

```text
IDLE ──initialise──► RUNNING ──future done: CONTINUE──► SUCCESS
                        │  └──future done: FAIL/ABORT/INDETERMINATE──► FAILURE
                        └──terminate(INVALID)──► cancel in flight ──► FAILURE
```

No leaf returns `SUCCESS` for a step whose decision is not `CONTINUE`. The tree
never retries a `FAILURE`.

### 14.2 Run state mapping (unchanged from v0.1.0)

| Supervisor outcome | `RunState` |
|---|---|
| All steps `COMPLETED` and verified where required | `SUCCEEDED` |
| `REJECTED`, `422`, `409` | `FAILED` |
| Preflight refusal, `ABORTED`, safety latch, lease loss | `ABORTED` |
| `TIMEOUT`, `UNKNOWN`, unverified `COMPLETED`, malformed result, stop failure | `INDETERMINATE` |

`WAITING_FOR_OPERATOR` is a supervisor state, not a fifth run state; the run
report keeps its existing four values.

## 15. Stage acceptance criteria

Offline and hardware are strictly separated. M1.5, M2, M3 and S3 produce L2/L3
evidence only. M4 is the first stage that may touch hardware and requires a
separate authorization per session.

### M1.5 — isolated task-engine validation (parallel, does not gate M2)

| Exit criterion | Evidence |
|---|---|
| Shared core extracted; `runner.py` delegates with an unchanged public API; the existing 89 v0.1.0 tests pass | `src/jcheng_applications/core/`; `tests/test_core.py`; existing `tests/` |
| `py_trees` 2.6 runs in a disposable venv: non-blocking `RUNNING`, `terminate(INVALID)`, sequence memory, single worker | [`M1_5_VALIDATION.md`](M1_5_VALIDATION.md); `evidence/20261126_m1_5_pytrees/probe.json` |
| Nominal gateway-call parity with the linear runner: same sequence, parameters, idempotency keys and zero stops | M15-I |
| Late future, hung worker and stop race are non-success and latch | M15-E/F/G |
| Run Supervisor thread with its own state machine, latch, deadline, stop-once and evidence | probe-level M15-J; the production supervisor is not implemented; T-S01–T-S10 |
| Failure-path differential (call log + stop + report semantics) | 11-case shared-core differential M15-K; T-C06 partially verified, full `RunReport` object pending |
| No transport dependency is added; `capability.yaml` unchanged | `pyproject.toml`; static scan |
| Repository gate and CI `applications-gate` pass | `bash scripts/check_repo_gate.sh` |

Progress 2026-11-26: M15-A–K pass, including an independent supervisor thread
(M15-J) and an 11-case failure differential (M15-K). The Shared Execution Core
is extracted; both the linear runner and the tree driver call it, and
`runner.py`'s public API is unchanged. The production tree runtime and Run
Supervisor are still not built, and T-C06 compares behavioural fields rather
than the full `RunReport` object. M1.5 is In progress, not Complete. It does not
prove gateway behaviour, concurrency, lease, stop, physical safety or full
report parity.

### M2 — read-only gateway client (mainline)

| Exit criterion | Evidence |
|---|---|
| `GET /v1/health`, `/v1/contract`, `/v1/state`, `/v1/safety` implemented with bounded connect and read timeouts | L2 mock; L3 on the real gateway, reads only |
| Contract hash or range mismatch refuses; no fallback endpoint | L2 |
| Unreachable gateway and timeout are bounded and classified, never retried | L2 |
| The client opens no DDS participant and no device | static scan |
| No intent is submitted | call-log assertion |

### M3 — supervised execution of one recipe (mainline)

| Exit criterion | Evidence |
|---|---|
| Lease acquire/renew/release through the gateway, renewal independent of the tree tick | L3 mock |
| One submission per step, stable keys, stop-once, cancellation and deadline | L3 mock |
| The fake gateway models two concurrent connections; stop during a mock motion is issued while `execute` is pending and resolves the run | L3 mock (probe C2/C3) |
| `UNKNOWN`/`TIMEOUT` blocks every later submission at the core, not only in the tree | L3 mock |
| Persisted, hash-chained run record; mock label preserved | L3 |
| Gateway concurrency evidence from `jcheng-system` is linked | L3; blocker for M4 |

**Unblocked on the mock (2026-11-26).** The first C2 probe failed; after the
system-side R1–R4 revision, C1–C5, C5b and a 30-iteration stress loop pass.
M3 may proceed against a mock gateway. The deployed gateway still has to pass
the same probes before M4. There is no application-layer workaround.

### S3 — declared branches and selection (parallel after M3)

| Exit criterion | Evidence |
|---|---|
| Recipe format with `facts` and `branches` validates; v0.1.0 recipes unchanged | L1 |
| Branch scopes are part of the recipe union and must be authorized | L1 |
| Selector is three-valued, deterministic and cannot read execution outcomes | L1 |
| No eligible required branch enters `WAITING_FOR_OPERATOR`; optional branch continues | L2 |
| Derived Plan requires a new version, run id and authorization | L1/L2 |
| Proposal artifact accepts only `params`/`skill`, requires a hash and is recorded | L1 |
| Everything above runs against a mock gateway only | L2 |

### M4 — supervised hardware acceptance (separate authorization)

| Exit criterion | Evidence |
|---|---|
| Probes C1–C5 pass on the deployed gateway | L5; system-owned evidence linked |
| One supervised task without Navigate or perception closed loop | L5 |
| Stop during a real motion is demonstrated in a dedicated dry-run first | L5 |
| The run record carries `evidence_class: L5`; the mock label is never reused | L5 |
| Operator authorization is per session and named | L5 |

Preconditions not met means M4 does not start. A failed bound stays failed.

## 16. Test matrix

State is `Planned` for every row; this document does not claim a pass.

### 16.1 Static and boundary

| ID | Test | Proves | Level |
|---|---|---|---|
| T-B01 | Forbidden hardware/DDS/network token scan | The L4 boundary holds after the refactor | L0 |
| T-B02 | Import-graph test over `tree/`, `supervisor/`, `core/`, `strategy/` | The dependency direction in §13.1 | L0 |
| T-B03 | No `eval`, `exec`, `importlib`, entry points or callables from data | No plugin surface, no remote shell | L0 |
| T-B04 | `capability.yaml` still `hardware_access: none`, empty runtime | No premature runtime claim | L0 |
| T-B05 | Dependency allowlist: `PyYAML`, `py_trees`, `pytest` only | The licence and supply-chain review stays valid | L0 |
| T-B06 | `MotionCommand` constructed only in `core/command.py` | Single compilation/submission entry | L0 |

### 16.2 Core equivalence

| ID | Test | Proves | Level |
|---|---|---|---|
| T-C01 | M1.5 nominal parity: tree submissions vs the linear golden call log (example recipe, all-success) | The isolated tree driver constructs the same commands and keys | L2 |
| T-C02 | `MotionBlocked` raised before command construction | The latch is structural | L2 |
| T-C03 | At most one stop request per run, including stop failure | Stop-once | L2 |
| T-C04 | Exhaustive classification table (outcome × `bus_verified` × scope) | No decision gap | L1 |
| T-C05 | Golden idempotency keys for v0.1.0 recipes | Key stability for existing plans | L1 |
| T-C06 | **Shared Core regression** (after extraction): linear and tree runners produce identical call logs, stop behaviour and step reports for the 11-case matrix; the existing 89 tests pass unchanged. **Partially verified 2026-11-26**: the full `RunReport` object is not yet compared | The refactor changed no behaviour | L2 |

### 16.3 Differential (tree vs linear)

| ID | Case | Asserts | Level |
|---|---|---|---|
| T-D01 | 3-step recipe, all `COMPLETED` + verified | Call sequence, count, params, keys, no stop, report | L2 |
| T-D02 | Gripper `COMPLETED` without `bus_verified` | One stop, `INDETERMINATE`, no next step | L2 |
| T-D03 | `REJECTED` at step 1 and at step 2 | Stop once, `FAILED`, later steps absent | L2 |
| T-D04 | `ABORTED` and safety latch mid-run | `ABORTED`, stop once | L2 |
| T-D05 | `TIMEOUT`, `UNKNOWN`, malformed result | `INDETERMINATE`, stop once, latch set | L2 |
| T-D06 | Submit raises before/after send | `UNKNOWN`-equivalent, no retry | L2 |
| T-D07 | Contract mismatch, safety not `ARMED_IDLE`, missing lease | Zero submissions and zero stops | L2 |
| T-D08 | Stop raises | `stop_error` recorded, run state preserved | L2 |
| T-D09 | Extra ticks while a leaf is `RUNNING` | Tick count does not change the call log | L2 |

### 16.4 Supervisor and concurrency

| ID | Test | Proves | Level |
|---|---|---|---|
| T-S01 | Leaf returns `RUNNING` while the future is pending; tick rate holds | Non-blocking execution | L2 |
| T-S02 | Stop is issued on the supervisor connection while `execute` is blocked | The independent supervision path | L2 |
| T-S03 | Cancellation drains the worker within the grace; no orphan future | No leaked motion, clean exit | L2 |
| T-S04 | Run deadline expires while a step is `RUNNING` | Stop, then classification from the terminal result | L2 |
| T-S05 | Lease renewal fails | Latch, stop, `ABORTED` | L2 |
| T-S06 | Tree raises; supervisor still writes the final evidence record | Evidence is supervisor-owned | L2 |
| T-S07 | A second `execute` is attempted after `UNKNOWN` | Core refuses; the call log has one execute | L2 |
| T-S08 | **Late Future**: the future completes after the leaf deadline or cancel | Never `SUCCESS`; `INDETERMINATE`; latch set; the late value is evidence only | L2 |
| T-S09 | **Worker Hang**: the worker never returns | Tick loop halts within the bound; transport timeout applies; no second submission; the process can exit | L2 |
| T-S10 | **Stop Race**: stop accepted, then the execute returns late `COMPLETED` | No plan advance, no second submission, `INDETERMINATE`; S1/S2/S3 recorded separately | L2 |

### 16.5 Strategy and guards

| ID | Test | Proves | Level |
|---|---|---|---|
| T-G01 | Guard truth table, including `UNKNOWN` | Fail-closed selection | L1 |
| T-G02 | Selector given a branch id not in the plan | No undeclared branch can run | L1 |
| T-G03 | `REJECTED`/`UNKNOWN`/`TIMEOUT` outcomes | No branch is selected; run is terminal | L2 |
| T-G04 | Branch scope not covered by authorization | Recipe rejected before submission | L1 |
| T-G05 | Branch keys vs main keys | Distinct, deterministic, no reuse | L1 |
| T-G06 | Derived plan with a reused run id or authorization | Refused | L1 |
| T-G07 | Tie-break and candidate ordering | Deterministic selection record | L1 |

### 16.6 Learned proposals

| ID | Test | Proves | Level |
|---|---|---|---|
| T-L01 | Proposal kinds other than `params`/`skill` | Reserved kinds cannot enter v0.2 | L1 |
| T-L02 | Proposal that adds a step or changes a key | Rejected; no plan mutation | L1 |
| T-L03 | Missing or mismatching `policy_hash` | Refused | L1 |
| T-L04 | Runtime proposal API absent | No runtime motion path | L0 |

### 16.7 Gateway mock and transport (M2)

| ID | Test | Proves | Level |
|---|---|---|---|
| T-M01 | Read-only client | No `POST` in M2 | L2 |
| T-M02 | Contract hash mismatch | Refusal without fallback | L2 |
| T-M03 | Unreachable and read timeout | Bounded, classified, no retry | L2 |
| T-M04 | Connection closed mid-response | `INDETERMINATE`, one stop (M3) | L2 |

### 16.8 Gateway concurrency prerequisites (system-owned)

| ID | Probe | Required result | Owner |
|---|---|---|---|
| C1 | `stop` while idle | No side effect, bounded response | `jcheng-system`; PASS 2026-11-26 |
| C2 | `stop` during `execute` on a second connection | Stop returns while `execute` is pending; terminal result reflects the stop | `jcheng-system`; first run FAIL, **r2 PASS** (one `ABORTED` terminal) |
| C3 | Second `execute` while one is in flight | Rejected, never queued | `jcheng-system`; PASS (both runs) |
| C4 | Connection drop during `execute` | Idempotent replay, no second command | `jcheng-system`; PASS (both runs) |
| C5 | Lease revoke during `execute` | `ABORTED` with the lease reason | `jcheng-system`; first run FAIL, **r2 PASS** (`LEASE_REVOKED`) |
| C5b | Lease expiry during `execute` | `ABORTED` with `LEASE_EXPIRED` | `jcheng-system`; first run FAIL, **r2 PASS** |

The first run failed C2, C5 and C5b. After the system-side R1–R4 revision, the
re-run passes C1–C5, C5b and a 30-iteration stress loop: C2 returns
`ABORTED` with one terminal object, and lease loss returns
`LEASE_REVOKED`/`LEASE_EXPIRED`. M3 is unblocked on the mock. Evidence:
`jcheng-system/e2e/reports/20261126_concurrency_probe_r2/`; the original
failure is kept in `.../20261126_concurrency_probe/`. C2–C5b remain hard
prerequisites on the **deployed** gateway before M4.

### 16.9 Acceptance conditions added 2026-11-26

| ID | Condition | Required behavior | Test |
|---|---|---|---|
| A1 | **Late Future** | A future that completes after the leaf deadline or a cancel is never `SUCCESS`; the run is `INDETERMINATE`; the late value is recorded as evidence only | T-S08 |
| A2 | **Worker Hang** | A worker that never returns must not block the tick loop; a bounded drain and a transport timeout apply; no second submission; the process can exit | T-S09 |
| A3 | **Stop Race** | A stop accepted before the terminal result must yield `ABORTED`, not a late `COMPLETED`; no plan advance; S1/S2/S3 are recorded separately | T-S10 |
| A4 | **Shared Core Regression** | After the single extraction, the linear and tree runners produce identical call logs, stop behaviour and reports for the full matrix, and the existing tests pass unchanged | T-C06 |

A1–A3 are app-side rules. They do not repair the system-side C2 defect; they
ensure the application fails closed when the gateway produces a contradictory
result.

## 17. Risk register

| ID | Risk | Sev | Mitigation | Gate |
|---|---|---|---|---|
| K1 | The gateway cannot pre-empt an in-flight `execute`, or produces a contradictory terminal result | High | **Confirmed and fixed on the mock 2026-11-26:** the first probe failed 30/30; after R1–R4, C2 returns one `ABORTED` terminal and C5/C5b `ABORTED` with the lease code. M3 unblocked on the mock. The deployed gateway is unprobed; M4 requires the same probes. No app-side workaround | M4: deployed C1–C5 |
| K2 | A wedged HTTP call blocks process exit and leaves an orphan motion | High | Mandatory connect/read timeouts; single worker; bounded drain; supervisor-owned stop | M2 / M3 |
| K3 | Branch selection becomes a de-facto retry | High | Guards read only verified facts; execution outcomes are not in the selector's input type; T-G03 | S3 |
| K4 | Branch scopes silently widen the authorized surface | Medium | Branch scopes join the recipe union; plan rejected if not covered; T-G04 | S3 |
| K5 | Two executors drift after the refactor | Medium | The single extraction is done; T-C06 compares call logs, stops and step reports across 11 cases; the full report object is still pending | T-C06 |
| K6 | Lease TTL is shorter than a long motion | High | Renewal on the supervisor thread at `ttl/3`; if renewal cannot happen during `execute`, the gateway lease contract is reviewed | M3 |
| K7 | `py_trees` semantics misused (status memory, `terminate`, blackboard) | Medium | Pin 2.6; wrap in `tree/`; leaf tests; no custom composite subclasses. M15-A–I validate the needed subset | M1.5 |
| K8 | Evidence leaks a token or operator data | Medium | Field allowlist, `lease_id` hashed, `0600`, outside git, gate scan | M1.5 |
| K9 | `WAITING_FOR_OPERATOR` is mistaken for a resumable pause | Medium | CLI text, exit code, evidence; resumption is a new run; no prompt to retry | M3 |
| K10 | A learned proposal drifts into a command path | High | Compile-time only; kind allowlist; hash; no runtime API; T-L04 | S3 |
| K11 | Navigate or perception scope creep bypasses the contract | Medium | Explicit non-goals; contract review triggers; no code until approved | Continuous |
| K12 | Deadline uses a wall clock that jumps | Low | Monotonic clock for deadlines; wall clock only in evidence | M1.5 |
| K13 | Third-party licence or supply-chain surprise | Low | `LICENSE-REVIEW.md`; pinned versions. py_trees 2.6 is **not** dependency-free: it requires `pydot>=1.4` (pulled 4.0.1 plus pyparsing) | M1.5 |

## 18. Open questions

1. **Gateway concurrency (answered 2026-11-26).** The first probe failed C2
   and C5/C5b. After R1–R4 the mock passes all of them; M3 is unblocked. The
   open part is the **deployed** `armpi_debug_gateway`, which was not probed
   and is an M4 prerequisite. Owner: `jcheng-system`;
   `docs/EXECUTION-MODEL-REVISION.md`.
2. **Lease TTL vs longest motion.** What is the deployed TTL, and can a renewal
   be served while `execute` is running? Owner: `jcheng-system`.
3. **Recipe format review.** The `facts`/`branches` extension in §8.3 needs a
   review of the predicate vocabulary before implementation. Owner: this
   repository.
4. **Operator channel.** Is `WAITING_FOR_OPERATOR` only a process state with a
   non-zero exit, or does `jcheng-studio` need a live channel? The latter is a
   contract review item. Owner: `jcheng-studio`.
5. **Evidence retention and redaction.** How long are run records kept, and are
   canonical commands retained or only hashed? Owner: operations.
6. **`measured_positions` stability.** Are joint names and units stable across
   robot profiles, and is `profile_hash` sufficient to bind a fact projection?
   Owner: `jcheng-system` / `jcheng-interfaces`.
7. **Merge order (resolved 2026-11-26).** M2 landed `transport.py`; the
   Shared Execution Core extraction landed afterwards as one refactor.
   T-C06 is partially verified; the full `RunReport` comparison is the
   remaining piece.

## 19. Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — components, run state machine and invariants.
- [`RECIPES.md`](RECIPES.md) — implemented recipe format; §8.3 here is a proposal.
- [`INTEGRATION.md`](INTEGRATION.md) — gateway admission, transport and failure semantics.
- [`M1_5_VALIDATION.md`](M1_5_VALIDATION.md) — isolated py_trees probe and its limits.
- [`../ROADMAP.md`](../ROADMAP.md) — M1.5, M2, M3 ordering.
- [`../ACCEPTANCE.md`](../ACCEPTANCE.md) — v0.1.0 evidence; no task-engine claim.
- [`../LICENSE-REVIEW.md`](../LICENSE-REVIEW.md) — planned `py_trees` dependency.
- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway OpenAPI and message schemas.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — gateway, lease and sole motion authority.
