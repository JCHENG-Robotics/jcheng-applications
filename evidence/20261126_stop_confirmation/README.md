# Stop confirmation receipt — evidence

> Date: 2026-11-26. Level: **L1/L2** (loopback mock gateway; no robot).
> Client-side support for the open R5 proposal
> `jcheng-interfaces/docs/proposals/stop-confirmation-levels.md`. No schema,
> tag or contract hash changed.

## 1. Rule

`POST /v1/motion/stop` proves **S1** (request accepted). It does not prove that
the backend stopped (S2) and can never prove a physical stop (S3).

| Gateway response | Recorded |
|---|---|
| `accepted: true`, no R5 fields (legacy) | `stop_confirmation = request_accepted`, `physical_stop = unverified` |
| `accepted: true`, `confirmation: backend_confirmed`, `physical_stop: unverified` | `stop_confirmation = backend_confirmed`, `physical_stop = unverified` |
| `confirmation: request_accepted` | `stop_confirmation = request_accepted` |
| invalid `confirmation` | `GatewayProtocolError`; no level recorded |
| any `physical_stop` other than `unverified` | `GatewayProtocolError`; the app never records a physical claim |
| stop request raises / non-200 | `stop_error` set; both levels `None` |
| no stop requested | both levels `None` |

The parser is `gateway.stop_receipt_from_mapping`; `HttpGateway.stop` returns a
`StopReceipt`; `core.stop.receipt_fields` refuses to trust a malformed receipt
partially. Both drivers write `RunReport.stop_confirmation` and
`RunReport.physical_stop`; `RunStore` persists them and the CLI prints them.

## 2. Tests

`tests/test_stop_confirmation.py`:

| Case | Result |
|---|---|
| missing optional fields | S1 + `unverified` |
| explicit `backend_confirmed` | S2 + `unverified` |
| invalid confirmation / physical value / accepted value | refused |
| HTTP transport returns the receipt | S2 parsed |
| linear run, legacy response | report and record S1; chain verifies |
| tree run, legacy response | report and record S1; chain verifies |
| linear/tree, backend confirmed | S2 recorded |
| stop request 503 | no level; `stop_error`; `operator_required` |
| successful run | no stop, no level |
| malformed receipt object | `(None, None)`, no partial trust |

## 3. Verification

```text
with the tree extra: 197 passed
without py_trees:    175 passed, 5 skipped
gate:                175 passed, 5 skipped, OK
```

Raw output: [`pytest-tree.txt`](pytest-tree.txt),
[`pytest-bare.txt`](pytest-bare.txt), [`gate.txt`](gate.txt).

## 4. Limitations

- The R5 fields are not in the published schema yet. A deployed gateway may
  omit them; the client then records S1 only. S2 is never inferred.
- The mock scripts the fields; this is not gateway evidence. The system-side
  C1/C2 probes and the deployed-gateway repeat remain separate.
- `physical_stop` is a constant `unverified`. There is no e-stop, brake,
  encoder or force sensing on this platform.
- No hardware and no real-robot claim.
