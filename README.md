# jcheng-applications

[简体中文](README.zh-CN.md) | **English**

> Status: **Experimental** as of 2026-11-26. Version 0.1.0.
> Admission plus an offline task-orchestration core: recipe validation,
> deterministic planning and a fail-closed runner over an abstract gateway
> port. M2 adds one read-only transport (GET only, bounded timeouts, no
> retry); no intent is submitted and there is no runtime process. M3 is
> unblocked on the mock after the system-side C2 revision passed; it is not
> implemented. Nothing here is used by the running robot.
> English canonical; see [README.zh-CN.md](README.zh-CN.md) for the Chinese translation.

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
| Inputs | Recipe YAML, an explicit per-session `Authorization` record, and gateway `ContractInfo` / `SafetySnapshot` / `MotionResult` values (M2/M3) |
| Outputs | A deterministic `Plan` with stable idempotency keys, and an in-memory `RunReport`; gateway intent requests from M3; persisted run records from M3 |
| Motion authority | None. The system layer owns the control lease, deadman, limits, stop latch and motion execution |
| Hardware access | `none` |
| Owned devices | none |
| Forbidden | joining DDS domains 0/41/42, `rclpy` or the `ros2` CLI, device access, vendor topics, direct motion execution, `/cmd_vel`, SSH to the robot, tokens in git, fabricated or interpolated state, automatic retry of an indeterminate motion, unattended hardware |

The division of authority is structural, not a coding convention:

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
│   ├── TASK_ENGINE.md              # design: tree executor, supervisor and strategy selection
│   └── M1_5_VALIDATION.md          # isolated py_trees technical validation report
├── recipes/
│   └── example-supervised-transfer.yaml  # offline example; not a hardware program
├── src/jcheng_applications/
│   ├── model.py                    # recipe model, contract constants and planner
│   ├── gateway.py                  # GatewayPort protocol and contract parsers
│   ├── transport.py                # M2 read-only GET transport (no submission)
│   ├── runner.py                   # fail-closed runner and run report
│   └── cli.py                      # validate/plan plus read-only check CLI
├── tests/                          # contract, recipe, gateway, runner and CLI tests
├── scripts/check_repo_gate.sh      # repository gate (local and CI)
├── scripts/m1_5_pytrees_probe.py   # isolated candidate-runtime probe (not a runtime dependency)
├── evidence/20261126_scaffold/     # M0/M1 evidence record
├── evidence/20261126_m1_5_pytrees/ # M1.5 isolated probe evidence
├── VERSION
├── README.md / README.zh-CN.md
├── ROADMAP.md / CHANGELOG.md / ACCEPTANCE.md
└── LICENSE-REVIEW.md
```

## Requirements

- Python 3.10 or newer (CI uses 3.12), `PyYAML` 6.x and `pytest` 7.4+.
- `py_trees` 2.6 (BSD-3) is the candidate tree runtime for M1.5, subject to the
  license review. v0.1.0 does not import it and it is not a dependency yet.
- No ROS installation, now or later: this repository joins no DDS graph and
  uses no RMW. The gateway is an HTTP/WS boundary.
- The M2 transport uses the standard library `http.client`; no third-party
  HTTP dependency is added. It is GET-only and has bounded timeouts.
- The gate makes no network request. Transport tests bind an ephemeral
  loopback port; no robot, gateway or network listener is required.
- A `jcheng-system` gateway and an operator-authorized session are required for
  any execution (M3). M3 is unblocked on the mock but not implemented; the M2
  `check` command reads only.

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

# CI equivalent.
python3 -m pytest -q
bash scripts/check_repo_gate.sh
```

There is deliberately **no `run` command**. `check` reads health, contract,
state and safety and nothing else. A command that appeared to run without the
M2/M3 gateway path and a passing C2 would invite a hardware claim this
repository cannot support. The runner is exercised against an in-process fake
gateway in the tests.

## Validation status

| Claim | Status | Evidence |
|---|---|---|
| Repository gate, document set and unit tests pass | Verified (L0/L1, 2026-11-26) | `evidence/20261126_scaffold/`; [CI run 45738303645](https://github.com/JCHENG-Robotics/jcheng-applications/actions/runs/45738303645) |
| Recipes are validated against the pinned contract, scopes and per-kind payload rules | Verified (L1, 2026-11-26) | `tests/test_recipe.py` |
| Plans are deterministic and idempotency keys are run-bound | Verified (L1, 2026-11-26) | `tests/test_recipe.py`; `src/jcheng_applications/model.py` |
| The runner refuses missing authorization, scope gaps, a contract mismatch, a non-`ARMED_IDLE` safety state and a missing lease before submitting anything | Verified (L2, 2026-11-26) | `tests/test_runner.py` |
| A rejected, aborted, timed-out or unverified step stops the run and is never retried | Verified (L2, 2026-11-26) | `tests/test_runner.py` |
| No hardware, DDS or motion-execution path exists; network access is confined to the GET-only `transport.py` | Verified (L0, 2026-11-26) | `tests/test_no_hardware.py` |
| Read-only gateway admission: four GET reads, bounded timeouts, no retry, contract/auth/format refusal | Partially verified (L2, 2026-11-26) | `tests/test_transport.py`; `evidence/20261126_m2_transport/`; loopback mock only; CI pending |
| Intent submission to a real gateway | Unverified; M3 not implemented | C2/C5/C5b pass on the mock (r2); M3 unblocked |
| Persisted run records and operator session linkage | Unverified | Not implemented (M2) |
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
