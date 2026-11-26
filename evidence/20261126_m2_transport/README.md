# M2 read-only transport — evidence

> Date: 2026-11-26. Operator: JerichoCheng. Level: **L2 (local, loopback mock)**.
> Status: **In progress**. CI evidence is pending; no live gateway and no
> hardware were contacted.

## 1. Scope

The M2 adapter in `src/jcheng_applications/transport.py`:

- `GET /v1/health` (unauthenticated), `/v1/contract`, `/v1/state`, `/v1/safety`.
- Bounded connect/read timeouts (default 2 s), size-bounded responses.
- Strict parsing; a `commanded` state is never treated as measurement.
- No POST, no submission, no stop, no lease mutation, no retry, no redirect.
- Contract range/hash refusal at admission.

## 2. Commands and results

```text
python3 -m pytest -q
103 passed

bash scripts/check_repo_gate.sh
103 passed
Repository gate OK
```

Raw output: [`pytest.txt`](pytest.txt), [`gate.txt`](gate.txt).

The 103 tests are the 89 v0.1.0 tests plus 13 transport tests and one static
GET-only AST test. The v0.1.0 tests are unchanged.

## 3. Transport coverage

| Area | Test |
|---|---|
| Four GET reads only; paths | `test_admission_is_get_only_and_reports_ready` |
| Health unauthenticated, safety authenticated | `test_health_is_unauthenticated_and_safety_is_authenticated` |
| Contract mismatch refusal | `test_contract_mismatch_refuses` |
| 401 and no retry | `test_unauthorized_is_not_retried` |
| Read timeout bounded | `test_read_timeout_is_bounded` |
| Unreachable bounded, no retry | `test_unreachable_is_bounded_and_never_retried` |
| Malformed JSON | `test_malformed_json_refuses` |
| Oversized body | `test_oversized_response_refuses` |
| `commanded` is not measurement | `test_commanded_state_is_not_measured` |
| URL validation (userinfo/path/query/port) | `test_endpoint_rejects_userinfo_path_query_and_bad_port` |
| Environment configuration | `test_endpoint_from_env` |
| 5xx classified, one request | `test_server_error_is_unavailable_and_not_retried` |
| Static GET-only proof | `test_transport_is_get_only_and_has_no_motion_call` |

## 4. Not proven

- No live `jcheng-system` gateway was contacted; all servers are loopback mocks.
- CI has not run this change yet.
- TLS is implemented through `HTTPSConnection` but untested.
- The WebSocket event stream is not part of M2 and is not implemented.
- No intent, stop, lease, motion or hardware behavior exists in this stage.

## 5. Boundary

The transport is the only source file allowed to import `http.client`. The
static scan still rejects `rclpy`, ROS 2, device paths, vendor topics,
`requests`, `urllib`, `socket`, `subprocess` and motion-execution tokens
everywhere else, including the new M1.5 probe.
