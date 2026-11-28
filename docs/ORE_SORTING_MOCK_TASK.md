# Minimal Mock Ore-Sorting Task

> Status: **offline reference task** (2026-11-28). Synthetic observations and
> mock execution only. No camera, perception model, IK solver, DDS, ROS,
> Gazebo runtime or hardware is involved.

## 1. Purpose

The existing `HOME → GRIPPER` reference proves the execution chain. This task
adds the smallest task-shaped loop that still exercises the real application
interfaces:

```text
synthetic observation
  → candidate grasp target
  → six-step recipe
  → existing lease + linear runner + mock gateway
  → task result
  → synthetic benchmark samples
```

It is not a second executor. The recipe is built with
`jcheng_applications.model`, and execution uses `run_supervised` exactly as the
existing reference does.

## 2. What is synthetic

| Item | Status |
|---|---|
| Observation | `data_source: synthetic`; two hand-declared objects |
| Grasp target | fixed reference joint positions; **not IK** |
| Workspace box | a synthetic rejection bound; not the measured ArmPi workspace |
| Execution | loopback mock gateway, `evidence_class: L2` |
| Result | mock step completion; not a real pick or accuracy claim |
| Benchmark | synthetic samples; observed suite has no business threshold |

The ArmPi capability match remains `matched_with_unverified`: DOF is matched;
reach, payload, gripper aperture and camera resolution stay unverified;
perception requirements are deferred to `jcheng-perception`.

## 3. Flow and recipe

The task selects the first reachable ore instance inside the synthetic box and
builds six steps:

1. `home-initial` — `HOME`, arm;
2. `pre-grasp` — `JOINT_STEP`, arm;
3. `grasp` — `GRIPPER` close, gripper;
4. `transfer` — `JOINT_STEP`, arm;
5. `place` — `GRIPPER` open, gripper;
6. `home-return` — `HOME`, arm.

A gangue instance may be present in the observation but is never selected in
this minimal task. No classification success rate is claimed.

## 4. Eight cases

| Case | Expected status | Motion |
|---|---|---|
| `normal` | `SUCCEEDED` | 6 mock steps completed |
| `no-target` | `NO_TARGET` | none |
| `unreachable` | `UNREACHABLE` | none |
| `grasp-failure` | `GRASP_FAILED` | 3 steps, one stop |
| `timeout` | `TIMEOUT` | 2 steps, one stop |
| `authorization-failure` | `AUTHORIZATION_FAILED` | none; run `ABORTED` |
| `stop-request` | `STOP_REQUESTED` | 2 steps, one stop |
| `capability-unmet` | `CAPABILITY_UNMET` | none; DOF threshold raised to 9 |

Only `normal` sets `success: true`. A mock success is step completion on the
loopback gateway, not a real grasp.

## 5. Entry point

```bash
python3 scripts/ore_sorting_mock_task.py \
  --device-snapshot arm=/tmp/b1/arm.yaml \
  --device-snapshot gripper=/tmp/b1/gripper.yaml \
  --device-snapshot camera=/tmp/b1/camera.yaml \
  --scenario-requirements /tmp/b1/requirements.json \
  --out /tmp/b1/ore-task \
  --benchmark-root /path/to/jcheng-benchmark \
  --repository-sha applications=<sha> \
  --repository-sha devices=<sha> \
  --repository-sha scenarios=<sha> \
  --repository-sha benchmark=<sha>
```

`--repository-sha NAME=SHA` is optional and records fixed revisions in the
summary. `--case all` is the default; a single case can be selected. Exit code 0 means
every selected case matched its expected classification and the benchmark
discrimination check passed.

Outputs:

- `case-<name>.json` — observation, target, task status, run report, artifact
  hashes and limitations;
- `samples.jsonl` — one `data_source: synthetic` sample per case;
- `task_summary.json` — case matrix, `cell_roles`, `scenario_requirements`
  (scenario/evaluation IDs and hashes), `repository_shas`, `artifact_valid`,
  `capability_satisfied`, `offline_execution_allowed`,
  `deployment_ready: false`, `motion_authorized: false`;
- `benchmark-{observed,test-pass,test-fail}/` — suite and report.

## 6. Benchmark discrimination

The observed suite declares no threshold and must be `inconclusive`. Two
additional suites use **test thresholds** (0.10 pass, 0.99 fail) only to prove
that the benchmark CLI distinguishes PASS, FAIL and INCONCLUSIVE. They are not
ore-sorting business acceptance criteria and are not derived from real data.

## 7. Non-goals

- No YOLO/instance segmentation, 3D localization, grasp-pose network, IK
  optimization or collision-aware planning.
- No Gazebo runtime validation; simulation remains an offline asset check.
- No real robot, motion authorization, deployment or hardware output.
- No claim that the ArmPi Ultra meets reach, payload or aperture requirements.
