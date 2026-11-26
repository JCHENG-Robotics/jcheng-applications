# Acceptance report — jcheng-applications

> Status: **inconclusive** as of 2026-11-26 for v0.1.0.
> Operator: JerichoCheng. Reviewer: pending.
> Scope: the repository scaffold, the offline orchestration core and its
> mock-gateway tests. The local gate and tests pass; the first CI run is
> recorded in `evidence/20261126_scaffold/` after the initial push.
> No gateway is contacted, no intent is submitted to a real system and no
> device is opened. No hardware claim is made.

## 1. Gates

| Gate | Command | Level | Result | Evidence |
|---|---|---|---|---|
| Repository gate | `bash scripts/check_repo_gate.sh` | L0 | pass (local, 2026-11-26) | `evidence/20261126_scaffold/gate.txt` |
| Unit tests | `python3 -m pytest -q` | L1 | pass (89 tests, local, 2026-11-26) | `tests/` |
| Offline runner against a fake gateway | `python3 -m pytest -q tests/test_runner.py` | L2 | pass (local, 2026-11-26) | `tests/test_runner.py` |
| CLI validate/plan | `jcheng-applications validate` / `plan` | L1 | pass (local, 2026-11-26) | `tests/test_cli.py` |
| CI | `.github/workflows/ci.yml` job `applications-gate` | L0/L1/L2 | pending first run | `evidence/20261126_scaffold/README.md` |
| Mock end-to-end through a gateway process | — | L3 | not applicable (no transport) | — |
| Replay of a recorded run | — | L4 | not applicable (no persisted run) | — |
| Hardware | — | L5 | not applicable; no hardware session authorized | — |

## 2. Verified claims

| Claim | Level | Evidence |
|---|---|---|
| The manifest parses, matches `VERSION`, pins the v0.1.0 contract hash and declares `hardware_access: none` with an empty runtime surface | L0 | `capability.yaml`; `tests/test_contract.py` |
| Recipes are rejected for an unknown field, kind, profile, scope, parameter, duplicate step name, scope-union mismatch or a non-finite value | L1 | `tests/test_recipe.py` |
| A recipe with an arm, gripper or full scope must require a verified bus read; a gripper command must set its nested `verify_bus` | L1 | `tests/test_recipe.py` |
| Plans are deterministic for the same run and payload and produce different keys for a different run or payload | L1 | `tests/test_recipe.py` |
| The runner refuses missing approval, a scope gap, a naive authorization timestamp, a contract mismatch, a non-`ARMED_IDLE` safety state and a missing lease before submitting anything | L2 | `tests/test_runner.py` |
| A rejected, aborted, timed-out, unknown or unverified step stops the run, requests one stop and is never retried | L2 | `tests/test_runner.py` |
| A completion without a verified bus read is `INDETERMINATE` for arm and gripper steps; a base pulse does not require that proof | L2 | `tests/test_runner.py` |
| A submission exception is treated as an unknown outcome and is not retried | L2 | `tests/test_runner.py` |
| A stop failure is recorded without hiding the run state | L2 | `tests/test_runner.py` |
| No device path, ROS/DDS interface, vendor topic, network client or motion-execution call appears in source, recipes or scripts | L0 | `tests/test_no_hardware.py` |
| No model weight, run database, operator env file or oversized file is tracked | L0 | `scripts/check_repo_gate.sh` |
| The required document set exists and relative links resolve | L0 | `scripts/check_repo_gate.sh` |

## 3. Not verified

| Item | Reason | Risk |
|---|---|---|
| HTTP/WS gateway client | Not implemented (M2) | No live contract, health or safety read exists |
| Contract and safety checks against a real gateway | No transport | The preflight logic is proven only against typed fakes |
| Session and lease binding | Not implemented (M3) | A real run cannot acquire or release a system lease |
| Intent submission to a real gateway | Not implemented (M3) | No command has reached a gateway |
| Persisted run record and evidence export | Not implemented (M3) | `RunReport` is in memory only |
| Stop and pre-emption against the system layer | No transport | Only the client-side request path is tested |
| Any hardware behavior | No L5 session | Nothing is known about real-arm execution |
| Operator authentication | Owned by `jcheng-studio` | This repository trusts the supplied authorization record |

## 4. Limitations

- v0.1.0 is an offline library and CLI. It is not used by the running robot.
- The example recipe is uncalibrated and is not a hardware program. Joint names
  and target positions are placeholders.
- The run report is not persisted, signed or exported. It cannot serve as
  evidence until M3 adds the append-only record.
- The authorization record is supplied by the caller. This repository has no
  login, token or operator identity provider; `jcheng-studio` owns that surface.
- A completion claim always depends on the gateway's `bus_verified` value. The
  application never reads `/joint_states` and never treats it as a measurement.
- No motion authority and no e-stop. The platform has no e-stop, brake, encoder
  or self-collision model; the system layer's stop latch is a software stop.
- Mock and fake-gateway results are never reported as hardware validation.
- The reviewer sign-off is pending; this report is not an organization-wide
  acceptance.

## 5. Sign-off

| Role | Name | Date |
|---|---|---|
| Operator | JerichoCheng | 2026-11-26 |
| Reviewer | pending | — |

## 6. Machine-readable report

None. When produced, reports must validate against
`jcheng-interfaces/schemas/acceptance-report.v1.schema.json`. A `RunReport` is
not an acceptance report and does not replace this document.
