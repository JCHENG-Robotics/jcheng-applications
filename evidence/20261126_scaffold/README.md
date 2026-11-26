# M0/M1 evidence — repository scaffold and offline orchestration core

> Status: **complete** as of 2026-11-26.
> Scope: the document set, admission manifest, repository gate, offline core
> and its tests for `jcheng-applications` v0.1.0. No gateway is contacted, no
> intent is submitted to a real system, no operator session exists and no
> hardware session is used.

## 1. Environment

| Item | Value |
|---|---|
| Repository | `JCHENG-Robotics/jcheng-applications` (public, created 2026-11-26) |
| Initial commit | pending first push |
| Operator host | operator laptop, Linux x86_64, Python 3.12.3, pytest 7.4.4 |
| CI host | `ubuntu-latest`, Python 3.12, pytest 8.3.4, PyYAML 6.0.2 |
| Robot | not used |
| Gateway | not contacted |
| Motion | none; no intent was submitted |
| DDS | not joined |

## 2. Commands and results

| Command | Result | Log |
|---|---|---|
| `python3 -m pytest -q` | pass, 89 tests | `gate.txt` |
| `bash scripts/check_repo_gate.sh` | pass | `gate.txt` |
| Manifest schema validation against `capability-manifest.v1.schema.json` | pass | `gate.txt` |
| `jcheng-applications validate recipes/example-supervised-transfer.yaml` | pass | `gate.txt` |
| `jcheng-applications plan recipes/example-supervised-transfer.yaml --run-id evidence-001` | pass | `gate.txt` |
| CI `applications-gate` | pending first push | — |

## 3. Claims supported

- The admission manifest parses, matches `VERSION`, pins the v0.1.0 contract
  hash and declares `hardware_access: none` with an empty runtime surface.
- Recipes are validated against the pinned contract, scopes and per-kind
  payload rules; arm, gripper and full steps require a verified bus read.
- Plans are deterministic and idempotency keys are run-bound.
- The runner refuses missing authorization, a scope gap, a contract mismatch, a
  non-`ARMED_IDLE` safety state and a missing lease before submitting anything.
- A rejected, aborted, timed-out, unknown or unverified step stops the run,
  requests one stop and is never retried.
- The static scan finds no hardware, DDS, network-client or motion-execution
  token in source, recipes or scripts.
- No model weight, run database, operator env file or recording is tracked.
- The documentation set required by the organization standard exists and its
  relative links resolve.

## 4. Not covered

- HTTP/WS gateway client, live contract/safety reads and intent submission
  (M2/M3).
- Persisted run records and operator session linkage (M3).
- Stop and pre-emption against a real gateway (M3).
- Any hardware or motion behavior (M4).
- Reviewer sign-off; the operator is the only account.

## 5. Related

- [Acceptance report](../../ACCEPTANCE.md)
- [Roadmap](../../ROADMAP.md)
- [Architecture](../../docs/ARCHITECTURE.md)
- [Recipes](../../docs/RECIPES.md)
