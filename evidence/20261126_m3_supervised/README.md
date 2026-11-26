# M3 supervised run — L3 evidence

> Date: 2026-11-26. Operator: JerichoCheng. Evidence class: **L3 (mock gateway
> process, no robot, no hardware)**.
> The gateway is the real `jcheng_system_core` gateway process with
> `MockStack`; the application is the real M3 CLI. No device, DDS or SSH was
> used. Physical motion is **not** claimed.

## 1. Setup

- `jcheng-system` mock gateway on `127.0.0.1:18766`, random token, 20 Hz
  `TickThread`, `MockStack` (mock hardware).
- Armed through the gateway HTTP API by the operator
  (`confirm_phrase = I_HAVE_A_CLEAR_WORKSPACE_AND_SUPPORT`); the application
  never arms the system.
- Recipe: three steps, each a single-joint `JOINT_STEP` (0.03 rad on joint1,
  then joint2) and a `GRIPPER` close to 0.10 rad, all with `verify_bus: true`.
- Authorization: operator `operator`, session `m3-e2e`, scopes `arm` and
  `gripper`, approved, timezone-aware timestamp.
- Store: `/tmp/m3-runs`, outside the repository.

The application CLI:

```bash
JCHENG_GATEWAY_TOKEN=... PYTHONPATH=src python3 -m jcheng_applications run \
  /tmp/m3-recipe.yaml \
  --authorization /tmp/m3-auth.json \
  --run-id m3-e2e-003 \
  --url http://127.0.0.1:18766 \
  --store-dir /tmp/m3-runs \
  --evidence-class L3
```

## 2. Results

| Run | Result | What it shows |
|---|---|---|
| `m3-e2e-001` | `FAILED` (`REJECTED / INVALID_COMMAND`) | The system rejects a multi-joint `JOINT_STEP`; the app requested one stop, released the lease and persisted the failure |
| `m3-e2e-002` | `ABORTED`, no steps | Preflight refused because the safety was still latched; nothing was submitted |
| `m3-e2e-004` | `SUCCEEDED`, 3/3 `COMPLETED` | Lease acquired, three steps submitted with stable keys, lease released, record persisted; `record_error: null` |

`m3-e2e-004` CLI output: [`cli-success.json`](cli-success.json).
Record: [`record-success.jsonl`](record-success.jsonl).
Rejected run: [`cli-rejected.json`](cli-rejected.json),
[`record-rejected.jsonl`](record-rejected.jsonl).
Preflight refusal: [`record-preflight-aborted.jsonl`](record-preflight-aborted.jsonl).

### 2.1 Record verification

`verify.txt` re-reads the three records: every chain verifies and every file is
mode `0600`:

```text
m3-e2e-001 verify: True mode: 0o600
m3-e2e-002 verify: True mode: 0o600
m3-e2e-004 verify: True mode: 0o600
```

The records contain `run_started`, one `step` per reached step and
`run_finished`; the final payload carries a `report_hash`. The raw gateway
lease id is not stored; the record keeps only its SHA-256 (`lease_ref`).

## 3. Finding: single-joint `JOINT_STEP`

The first attempt used five joints in one `JOINT_STEP` (the shipped offline
example does the same). The system safety profile accepts **exactly one joint**
per `JOINT_STEP` and rejected it with `INVALID_COMMAND`. The application's
recipe validator allows a list, so this is a real app/profile mismatch; recipe
authors must split multi-joint steps. The shipped example remains an offline
example and would be rejected by the deployed gateway. `RECIPES.md` records the
constraint.

## 4. Limitations

- Mock hardware only; no physical motion, no physical-stop claim.
- The run completed in under the lease TTL/3, so this end-to-end run exercised
  acquire and release but not renewal; `tests/test_supervised.py` covers
  renewal and renewal failure with a short TTL.
- R5 (`StopResult` S1/S2 confirmation fields) is not in the v1 contract; this
  evidence does not claim a backend-confirmed or physical stop.
- The deployed `armpi_debug_gateway` was not contacted; M4 requires the same
  C1–C5 probes and a separately authorized hardware session.
- The gateway token was random, kept in a `0600` temporary file and is not in
  this evidence.

## 5. Local tests

```text
python3 -m pytest -q
127 passed
```

See [`pytest.txt`](pytest.txt). The suite covers the write transport, lease
acquire/renew/release, renewal failure, non-mock refusal, stop-on-rejection,
record chaining/tampering and the CLI-facing authorization parser.
