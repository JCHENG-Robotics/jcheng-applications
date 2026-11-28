# Reference integration — ArmPi Ultra × ore_sorting (offline)

> Status: **offline reference, synthetic data** as of 2026-11-28. It runs two
> pinned artifacts through the existing recipe model, mock gateway, lease and
> linear runner. It does not touch hardware, DDS, ROS, a device, a deployed
> gateway or a real task result. No value produced here is a measurement, a
> scenario acceptance result or a benchmark threshold.

## 1. Purpose and position

The Phase 2 design in
[`.github` docs](https://github.com/JCHENG-Robotics/.github/blob/main/docs/boundary-consolidation/24-phase2-source-of-truth-design.md)
names **ArmPi Ultra × ore_sorting** as the first offline vertical. This
repository owns the application-side consumer only:

```text
device snapshot (pinned fixture)          scenario requirements (pinned fixture)
  identity, version, content_hash           scenario/task/evaluation IDs
  capabilities[id, parameters, feedback]    capabilities[id, required, parameters]
                     \                      /
                      \                    /
                  capability match (offline, strict)
                            │
                applications recipe + deterministic plan
                            │
        loopback mock gateway -> lease -> linear runner -> stop
                            │
        synthetic JSONL samples + reference_report.json
                            │  optional subprocess
                 jcheng-benchmark CLI (offline)
```

The script is
[`scripts/armpi_ore_sorting_reference.py`](../scripts/armpi_ore_sorting_reference.py).
It uses only the standard library, PyYAML and this repository's existing
modules. It does not import a sibling repository's product code; the benchmark
is reached through its CLI in a subprocess.

## 2. Inputs

Both artifacts are YAML or JSON. Unknown fields, non-JSON YAML values,
duplicate IDs and malformed paths are rejected.

### 2.1 Device snapshot — `jcheng.device_snapshot.v1`

| Field | Rule |
|---|---|
| `schema` | exactly `jcheng.device_snapshot.v1` |
| `source_device` | stable ID such as `hiwonder.armpi-ultra` |
| `device_version` | `MAJOR.MINOR.PATCH` |
| `content_hash` | canonical hash of the document, see §2.3 |
| `generated_by` | non-empty generator identity |
| `source_path` | non-empty source profile path |
| `capabilities[]` | `{id, parameters, feedback}`; IDs are unique; `parameters` is a flat mapping; `feedback` is a string list |
| `joints[]` | `{name, min_rad, max_rad}`, unique names, `min_rad <= max_rad` |
| `safety_overlay` | mapping; recorded, never applied as a limit here |

### 2.2 Scenario requirements — `jcheng.scenario_requirements.v1`

| Field | Rule |
|---|---|
| `schema` | exactly `jcheng.scenario_requirements.v1` |
| `scenario_id` | stable ID such as `ore_sorting` |
| `scenario_version` | `MAJOR.MINOR.PATCH` |
| `content_hash` | canonical hash of the document, see §2.3 |
| `task_id` / `evaluation_id` | `<id>@MAJOR.MINOR.PATCH`; the version must equal `scenario_version` |
| `capabilities[]` | `{id, required, parameters?}`; IDs are unique |
| `constraints[]` | `{id, statement}`, unique IDs |
| `provenance` | non-empty mapping |
| `simulation_assets[]` | optional `{id, path}`; a relative path under `--simulation-root` |

A requirement parameter is an expectation, not a device value:

```yaml
parameters:
  reach_m: {op: ">=", value: 0.45}   # explicit comparison
  dof: 4                             # shorthand for == 4
```

Supported operators are `>=`, `<=`, `>`, `<`, `==`, `!=`. An ordering
comparison requires two numbers; comparing a boolean or a string is rejected.
The reference never assumes a comparison direction.

### 2.3 Canonical content hash

`content_hash` is `sha256:` followed by the SHA-256 of the canonical JSON
encoding of the artifact with the `content_hash` field removed (sorted keys,
no whitespace, `ensure_ascii`). Any other edit without regeneration is
reported as `STALE_DEVICE_HASH` or `STALE_SCENARIO_HASH`; the script does not
silently pin a new value.

### 2.4 Capability vocabulary

The fixture capability IDs mirror the scenario requirement vocabulary
(`manipulation.arm.reach_m`, `perception.scene_state`, ...). The device
registry currently uses native IDs (`arm.position_control`, ...). The mapping
between the two is a **fixture decision for this reference**, not an
interfaces contract. A real devices generator and a second real consumer are
required before the vocabulary is promoted.

## 3. Commands

Default offline run (no benchmark, no simulation root):

```bash
python3 scripts/armpi_ore_sorting_reference.py \
  --device-snapshot tests/integration/fixtures/armpi-ultra.device-snapshot.yaml \
  --scenario-requirements tests/integration/fixtures/ore-sorting.scenario-requirements.yaml \
  --out /tmp/armpi-ore-sorting-reference
```

With the benchmark CLI:

```bash
python3 scripts/armpi_ore_sorting_reference.py \
  --device-snapshot tests/integration/fixtures/armpi-ultra.device-snapshot.yaml \
  --scenario-requirements tests/integration/fixtures/ore-sorting.scenario-requirements.yaml \
  --out /tmp/armpi-ore-sorting-reference \
  --benchmark-root ../jcheng-benchmark
```

`--benchmark-root` must point at a `jcheng-benchmark` checkout containing
`src/jcheng_benchmark`. The script spawns
`python -m jcheng_benchmark run ...` with `PYTHONPATH` set to that `src`
directory, a 60-second timeout and the synthetic artifacts it just wrote.

With simulation asset checking:

```bash
python3 scripts/armpi_ore_sorting_reference.py \
  --device-snapshot ... \
  --scenario-requirements ... \
  --out /tmp/armpi-ore-sorting-reference \
  --simulation-root ../jcheng-simulation
```

The check only verifies that every declared `simulation_assets[].path` exists
as a file under the root. It does not launch a plant, source a ROS workspace
or join domain 43.

## 4. Outputs

| File | Content |
|---|---|
| `reference_report.json` | device/scenario IDs, versions and hashes; capability match; plan; mock results; lease checks; stop checks; simulation check; benchmark status; limitations |
| `plan.json` | the deterministic plan with `scenario_id`, `scenario_version`, `task_id`, `evaluation_id`, step order and idempotency keys |
| `synthetic_samples.jsonl` | one sample per executed mock step, every line carrying `data_source: synthetic` and `data_kind: mock_step` |
| `benchmark_suite.yaml` | written only with `--benchmark-root`; `data_source: synthetic`, no thresholds |
| `benchmark_report.json` / `.md` | written by the benchmark CLI, not by this repository |

The reference report contains no wall-clock timestamp and no host name. For
fixed inputs and a fixed output directory layout it is byte-identical across
runs. The external benchmark report does carry its own timestamp; the
reference report records only its result, metrics and verified suite/dataset
hashes.

### 4.1 What the plan does and does not say

The generated recipe is a two-action reference skeleton:

1. `home` — `HOME`, `arm` scope, `verify_bus: true`;
2. `prepare-gripper` — `GRIPPER`, `gripper` scope, conservative constants
   (`position_rad: 1.0`, `max_velocity_rad_s: 0.1`, `duration_s: 1.0`,
   `verify_bus: true`).

The steps are constants of the reference, not task targets. The report sets
`task_evaluation.evaluated: false`; no success threshold, pick success rate or
ore-sorting acceptance criterion is produced. A mock step that returns
`COMPLETED` with `bus_verified: true` is step completion on the loopback mock,
nothing more.

### 4.2 Capability match

The match is strict and fails closed:

| Condition | Error |
|---|---|
| A required capability is not declared by the device snapshot | `INCOMPATIBLE_CAPABILITY` |
| A device capability lacks a required parameter | `INCOMPATIBLE_PARAMETER` |
| A parameter comparison is unsatisfied or type-incompatible | `INCOMPATIBLE_PARAMETER` |
| An optional capability is absent | recorded in `capability_match.optional_absent`; not assumed available |

### 4.3 Mock execution, lease and stop

The run composes the existing `tests/mock_gateway.py` loopback server with
`HttpGateway`, `run_supervised`, `LeaseSession` and the linear `TaskRunner`.
Two runs are performed:

- **happy path** — both plan steps return `COMPLETED` with a verified bus
  read. Expected state `SUCCEEDED`; no stop is requested.
- **failure path** — the first result is scripted as `REJECTED`. Expected
  state `FAILED`; exactly one intent is submitted, exactly one stop is
  requested, the second step is never submitted, and the stop receipt is S1
  `request_accepted` with `physical_stop: unverified`.

Both runs must acquire and release a lease and write the hash-chained
supervised record. The record is written to a temporary directory outside the
repository and is removed after the check; it is not an evidence artifact.

### 4.4 Benchmark

The suite metric is `mock_step_completion` (a `success_rate` over the
`synthetic` samples). It is deliberately **not** `pick_success_rate` from the
scenario evaluation profile. No threshold is declared, so the benchmark's own
result is `inconclusive` and informational; the reference never upgrades it to
`pass`. The dataset includes the happy path and the deliberately injected
rejection, and the report labels both as synthetic mock step outcomes.

The script verifies that the benchmark report hashed the same suite and
dataset it wrote. A missing report, a non-zero unexpected exit, a timeout or a
hash mismatch is recorded as `benchmark.status: failed` and exits non-zero; no
metric is invented.

If `--benchmark-root` is omitted, the report contains
`benchmark_executed: false` and `status: skipped`.

## 5. Simulation assets

| Situation | Result |
|---|---|
| No `simulation_assets` declared | `not_checked` |
| Assets declared, no `--simulation-root` | `not_checked`, `exists: null` |
| Assets declared, root provided, all files present | `ok` |
| Assets declared, root provided, a file is missing | `missing`; report written, exit 1 |
| A path is absolute or contains `..` | schema rejection before any run |

The `ore_sorting` fixture declares no simulation assets because the current
scenario and simulation repositories do not ship an ore-cell asset. A missing
asset is never reported as present.

## 6. Exit codes and failure behaviour

| Code | Meaning |
|---|---|
| 0 | every reference check ran; the benchmark was skipped, or executed and its own result was `pass`/`inconclusive` |
| 1 | input/schema/hash/version/capability/simulation/mock/benchmark failure; the message names the check |
| 2 | argparse usage error |

A capability, hash or schema failure happens before any mock run. A missing
simulation asset or a benchmark failure happens after the mock run; the
reference report is still written so the failure is traceable, and the exit
code is non-zero.

## 7. Boundaries

- No SSH, DDS, ROS, `rclpy`, `ros2` CLI, device, serial, camera or vendor SDK.
- No deployed gateway and no non-mock submission; the only HTTP target is
  `127.0.0.1`.
- No cross-repository product import; the benchmark is a subprocess CLI and
  the scenario/simulation repositories are read only as pinned artifacts.
- No safety authority: the mock stop is a software request; a physical stop is
  never claimed.
- No scenario acceptance, no benchmark threshold and no hardware evidence.
- No change to the existing recipe model, runner, gateway or lease behaviour.
- No new third-party dependency (PyYAML and the standard library only).

## 8. Not implemented

- A real devices snapshot generator and a devices-native capability
  vocabulary mapping.
- Direct ingestion of the `ore_sorting` scenario profile files; the fixture is
  a pinned consumer copy with provenance.
- Perception execution, scene evaluation, ore picking or trajectory
  generation. The plan is a two-action skeleton.
- Simulation plant execution, ROS launch or domain 43.
- Benchmark thresholds, a baseline, hardware data or recorded data.
- Storage of the reference run as evidence; the supervised record is
  temporary.
- Promotion of either schema to `jcheng-interfaces`. That requires a second
  real consumer and a contract proposal.

## 9. Tests and gate

```bash
python3 -m pytest tests/integration -q   # 23 offline tests
python3 -m pytest -q                     # full repository suite
bash scripts/check_repo_gate.sh          # document set, links, secrets, tests
```

The integration tests cover: valid match and byte-identical repeatability,
JSON inputs, unknown capability, missing required capability, missing required
field, stale device/scenario hashes, scenario version mismatch, incompatible
parameter, unknown schema, mock execution, lease lifecycle, stop and
fail-closed behaviour, synthetic labelling, benchmark skipped/available/
failing, an invalid benchmark root, and simulation not_checked/ok/missing/
path-escape. A local stub package stands in for the benchmark repository, so
the tests need no sibling checkout.

## 10. Related

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — system position and invariants.
- [`INTEGRATION.md`](INTEGRATION.md) — gateway admission, transport and stop.
- [`RECIPES.md`](RECIPES.md) — recipe syntax and authorization.
- [`TASK_ENGINE.md`](TASK_ENGINE.md) — executor design and branch selection.
- [`tests/integration/`](../tests/integration/) — fixtures and tests.
- [jcheng-scenarios](https://github.com/JCHENG-Robotics/jcheng-scenarios) —
  scenario source of truth.
- [jcheng-devices](https://github.com/JCHENG-Robotics/jcheng-devices) — device
  registry and capability profiles.
- [jcheng-benchmark](https://github.com/JCHENG-Robotics/jcheng-benchmark) —
  evaluation CLI and reports.
