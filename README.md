# jcheng-applications

**English** | [简体中文](README.zh-CN.md)

> Status: **Experimental** as of 2026-11-26. Version 0.1.0.
> Admission plus a task-orchestration core: recipe validation, deterministic
> planning, a fail-closed runner over an abstract gateway port, an M2 read-only
> transport and an M3 supervised `run` against a mock gateway. The shared
> execution core is extracted; an opt-in M1.5 py_trees engine and independent
> Run Supervisor are available behind `--engine tree`. S3 v0 declared branches
> and a three-valued selector run on both engines with positional arm facts.
> The default is still the linear engine. No hardware is used and nothing here
> runs on the robot.
> English canonical; the Chinese README is a translation.

`jcheng-applications` is the L4 task-orchestration application of the JCHENG
ecosystem. It turns an operator-authored recipe into a deterministic sequence
of intent requests and asks a `jcheng-system` gateway to evaluate them. It is
not the operator console — that is [`jcheng-studio`](https://github.com/JCHENG-Robotics/jcheng-studio) —
and it is not a motion authority. The system layer owns the lease, deadman,
limits, stop latch and the single motion entry point; the application only
proposes one step at a time and reports what the gateway answered.

## Scope and boundary

| Item | This repository |
|---|---|
| Layer | L4 task orchestration (see the [repository taxonomy](https://github.com/JCHENG-Robotics/.github/blob/main/docs/repo-taxonomy.md)) |
| Runs on | Operator laptop or workstation; never on the robot |
| Inputs | Recipe YAML, an explicit per-session `Authorization` record, and gateway `ContractInfo` / `SafetySnapshot` / `MotionResult` values |
| Outputs | A deterministic `Plan` with stable idempotency keys; a `RunReport`; gateway intent requests; a hash-chained run record outside the repository (M3) |
| Motion authority | None. The system layer owns the control lease, deadman, limits, stop latch and motion execution |
| Hardware access | `none` |
| Owned devices | none |
| Forbidden | joining DDS domains 0/41/42, `rclpy` or the `ros2` CLI, device access, vendor topics, direct motion execution, `/cmd_vel`, SSH to the robot, tokens in git, fabricated or interpolated state, automatic retry of an indeterminate motion, unattended hardware |

The division of authority is structural, not a coding convention:

- M3 acquires a **client lease reference** through the gateway before the first
  submission and releases it on every terminal path. The system layer owns the
  lease, may revoke it at any time, and remains the only origin of motion.
- A recipe is a request for a sequence of intents, not a program with direct
  access to a device.
- The runner submits at most one intent per step and never retries. A timeout,
  an unknown outcome or a completion without a verified bus read stops the run.
- A run requires an explicit, approved, scope-covering operator authorization
  for that session. A previous session never carries over.
- The system layer may reject, abort, pre-empt or stop at any point. The
  application reports that outcome unchanged.
- The application is not an e-stop. The platform has no e-stop, brake, encoder
  or self-collision model; the system layer's stop latch is a software stop.

## Repository layout

```text
jcheng-applications/
├── capability.yaml                 # admission manifest (offline, hardware_access: none)
├── docs/
│   ├── ARCHITECTURE.md             # planes, components and the run state machine
│   ├── INTEGRATION.md              # gateway contract, admission and failure semantics
│   ├── RECIPES.md                  # recipe format, steps, idempotency and authorization
│   ├── REFERENCE_INTEGRATION.md    # pinned artifact reference and cell matching
│   ├── ORE_SORTING_MOCK_TASK.md    # eight-case synthetic task over the mock gateway
│   ├── TASK_ENGINE.md              # design: tree executor, supervisor and strategy selection
│   └── M1_5_VALIDATION.md          # isolated py_trees technical validation report
├── recipes/
│   ├── example-supervised-transfer.yaml  # v0.1 offline example; not a hardware program
│   └── example-declared-branch.yaml      # v0.2 facts/branch example; mock-sized values
├── src/jcheng_applications/
│   ├── model.py                    # recipe model, contract constants and planner
│   ├── gateway.py                  # GatewayPort protocol and contract parsers
│   ├── transport.py                # M2 read-only GET transport (no submission)
│   ├── write_transport.py          # M3 lease/execute/stop transport (mock-only)
│   ├── lease.py                    # lease acquire, renew, release
│   ├── runstore.py                 # hash-chained run records outside the repo
│   ├── supervised.py               # lease + engine + persisted report
│   ├── core/                       # shared preflight, command, classify, executor, stop-once
│   ├── tree/                       # opt-in py_trees engine + independent Run Supervisor
│   ├── runner.py                   # fail-closed linear runner over the shared core
│   └── cli.py                      # validate/plan/check/run CLI
├── tests/                          # contract, recipe, transport, lease, tree and supervised-run tests
├── scripts/check_repo_gate.sh      # repository gate (local and CI)
├── scripts/m1_5_pytrees_probe.py   # isolated candidate-runtime probe (not a runtime dependency)
├── evidence/20261126_scaffold/     # M0/M1 evidence record
├── evidence/20261126_m1_5_pytrees/ # M1.5 isolated probe evidence
├── evidence/20261126_m3_supervised/# M3 L3 supervised-run evidence
├── evidence/20261126_m1_5_tree/    # M1.5 tree engine L3 evidence
├── evidence/20261126_t_c06/        # T-C06 full RunReport parity evidence
├── evidence/20261126_s3_branches/  # S3 declared-branch linear L3 evidence
├── evidence/20261126_s3_tree/      # S3 tree branch L3 and parity evidence
├── evidence/20261126_waiting/      # WAITING_FOR_OPERATOR evidence
├── evidence/20261126_derived_plan/ # Derived Plan identity guard evidence
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## Requirements

- Python 3.10 or newer (CI uses 3.12), `PyYAML` 6.x and `pytest` 7.4+.
- `py_trees` 2.6 (BSD-3) is an **optional** `tree` extra for the M1.5 engine:
  `pip install -e '.[tree]'`. The default linear engine and the repository gate
  do not import it; the license review is still open. The extra pins
  `py_trees==2.6.0`, `pydot==4.0.1` and `pyparsing==3.3.3`.
- No ROS installation, now or later: this repository joins no DDS graph and
  uses no RMW. The gateway is an HTTP/WS boundary.
- The two transports use the standard library `http.client`; no third-party
  HTTP dependency is added. The M2 adapter is GET-only; the M3 adapter submits
  only to a gateway that advertises the `mock` capability.
- The gate makes no network request. Transport tests bind an ephemeral
  loopback port; no robot, gateway or network listener is required.
- `run` requires a `jcheng-system` gateway, an operator-supplied
  `Authorization` JSON and the gateway token in the environment. It refuses a
  non-mock gateway and writes its record outside the repository.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'

# Validate the shipped offline example.
jcheng-applications validate recipes/example-supervised-transfer.yaml

# Print the deterministic plan. No gateway is contacted.
jcheng-applications plan recipes/example-supervised-transfer.yaml \
  --run-id demo-001 --json

# M2 read-only admission: GET only, no motion. The token comes from the
# environment and is never passed on the command line.
export JCHENG_GATEWAY_URL=http://127.0.0.1:8766
export JCHENG_GATEWAY_TOKEN=...   # set by the operator; never commit it
jcheng-applications check --url "$JCHENG_GATEWAY_URL"

# M3 supervised run: mock gateway only. The recipe must use single-joint
# JOINT_STEP commands; the system profile accepts one joint per step.
jcheng-applications run path/to/m3-recipe.yaml \
  --authorization /path/to/authorization.json \
  --run-id demo-002 \
  --url http://127.0.0.1:18766 \
  --store-dir ~/.local/state/jcheng-applications/runs \
  --evidence-class L3

# M4 hardware target: this example grants no authorization. The operator must
# supply a separate per-session record and the gateway must advertise hardware.
jcheng-applications run path/to/m4-recipe.yaml \
  --authorization /path/to/authorization.json \
  --run-id demo-004 \
  --target hardware \
  --evidence-class L5 \
  --url http://<gateway-host>:8766 \
  --store-dir ~/.local/state/jcheng-applications/runs

# Audit one stored record: read-only, no network. Exit 0 means the chain
# verifies and the run has a terminal entry, not that the run succeeded.
jcheng-applications verify demo-002 \
  --store-dir ~/.local/state/jcheng-applications/runs

# CI equivalent.
python3 -m pytest -q
bash scripts/check_repo_gate.sh
```

`run` is the only command that submits motion, and it is mock-only: it refuses
a gateway whose health does not advertise `mock`, requires an explicit
`Authorization` record, acquires and releases a client lease, and persists a
hash-chained record outside the repository. `check` reads health, contract,
state and safety and nothing else. No command opens a device, joins DDS or
executes motion itself. Exit codes: 0 `SUCCEEDED`, 1 `FAILED`/`ABORTED`,
2 usage or record failure, **3 `WAITING_FOR_OPERATOR`** (no motion authority;
inspect and start a new authorized run). `run --derived-from <run-id>` requires
a verified predecessor record plus a new recipe version, run id and
authorization session; a violation refuses before any gateway call.

## Validation status

| Claim | Status | Evidence |
|---|---|---|
| Repository gate, document set and unit tests pass | Verified (L0/L1, 2026-11-26) | `evidence/20261126_scaffold/`; [CI run 45738303645](https://github.com/JCHENG-Robotics/jcheng-applications/actions/runs/45738303645) |
| Recipes are validated against the pinned contract, scopes and per-kind payload rules | Verified (L1, 2026-11-26) | `tests/test_recipe.py` |
| Plans are deterministic and idempotency keys are run-bound | Verified (L1, 2026-11-26) | `tests/test_recipe.py`; `src/jcheng_applications/model.py` |
| The runner refuses missing authorization, scope gaps, a contract mismatch, a non-`ARMED_IDLE` safety state and a missing lease before submitting anything | Verified (L2, 2026-11-26) | `tests/test_runner.py` |
| A rejected, aborted, timed-out or unverified step stops the run and is never retried | Verified (L2, 2026-11-26) | `tests/test_runner.py` |
| No hardware, DDS or motion-execution path exists; network access is confined to `transport.py` and `write_transport.py`, and the write path refuses non-mock gateways | Verified (L0, 2026-11-26) | `tests/test_no_hardware.py`; `tests/test_supervised.py` |
| Read-only gateway admission: four GET reads, bounded timeouts, no retry, contract/auth/format refusal | Verified (L2, 2026-11-26) | `tests/test_transport.py`; `evidence/20261126_m2_transport/`; [CI run 45748905495](https://github.com/JCHENG-Robotics/jcheng-applications/actions/runs/45748905495) |
| Shared Execution Core; linear and tree engines | Verified (L2, 2026-11-26) | `tests/test_core.py`; T-C06 12-case exact `RunReport` parity; `evidence/20261126_shared_core/`, `evidence/20261126_t_c06/` |
| Opt-in py_trees engine + independent deadline/cancel/stop supervisor | Verified (L2/L3, 2026-11-26) | `tests/test_tree_runtime.py`; `evidence/20261126_m1_5_tree/`; py_trees extra not default; licence review open |
| Declared branches + positional arm facts + three-valued selector (both engines) | Verified (L1–L3, 2026-11-26) | `tests/test_branches.py`; `tests/test_tree_branches.py`; `evidence/20261126_s3_branches/`, `evidence/20261126_s3_tree/`; named/gripper facts pending |
| Terminal `WAITING_FOR_OPERATOR`: `operator_required` + CLI exit 3, no motion authority | Verified (L2, 2026-11-26) | `tests/test_waiting.py`; `evidence/20261126_waiting/`; live channel not implemented |
| Derived Plan identity: verified predecessor, new recipe version, run id and authorization session before any gateway call | Verified (L1/L2, 2026-11-26) | `tests/test_derived.py`; `evidence/20261126_derived_plan/` |
| Supervised execution against the mock gateway: lease acquire/release, 3-step recipe, stop-on-rejection, record | Verified (L3, 2026-11-26) | `evidence/20261126_m3_supervised/`; real `jcheng-system` gateway process + `MockStack`; no robot |
| Stop confirmation levels: legacy response is S1 only, S2 requires `backend_confirmed`, physical stop is always `unverified` | Verified (L1–L3, 2026-11-26) | `tests/test_stop_confirmation.py`; `evidence/20261126_stop_confirmation/` (real system mock gateway, legacy S1); R5 schema review pending |
| M4 target gate: hardware requires L5 + a gateway advertising `hardware`; mock/L5 mismatch refused before lease | Verified (L1, 2026-11-27) | `tests/test_m4_target.py`; `evidence/20261127_m4_target/`; no hardware run |
| Persisted, hash-chained run records outside the repository | Verified (L2/L3, 2026-11-26) | `tests/test_supervised.py`; `evidence/20261126_m3_supervised/verify.txt` |
| Read-only record audit: chain, terminal entry, stop levels; missing/tampered/incomplete exit non-zero | Verified (L0/L1, 2026-11-26) | `tests/test_audit.py`; `evidence/20261126_record_audit/` |
| Intent submission to a real robot gateway | Unverified | M4 requires a separate authorized session and C1–C5 on the deployed gateway |
| Any hardware behavior | Unverified | No L5 session; no runtime |

Full report: [ACCEPTANCE.md](ACCEPTANCE.md).

## Related repositories

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — gateway OpenAPI, WS envelope, message schemas and contract hash.
- [`jcheng-system`](https://github.com/JCHENG-Robotics/jcheng-system) — privileged runtime; gateway owner, lease holder and the only origin of motion.
- [`jcheng-studio`](https://github.com/JCHENG-Robotics/jcheng-studio) — L4 operator console; the human-facing surface that authorizes and observes sessions.
- [`jcheng-perception`](https://github.com/JCHENG-Robotics/jcheng-perception) — advisory perception targets, surfaced through the gateway.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — unattended health and watchdog policy, owned outside this repository.
- [`.github`](https://github.com/JCHENG-Robotics/.github) — ecosystem roadmap, changelog, acceptance and repository taxonomy.

## License

Pending third-party and copyright review; no `LICENSE` file has been added.
See [LICENSE-REVIEW.md](LICENSE-REVIEW.md).
