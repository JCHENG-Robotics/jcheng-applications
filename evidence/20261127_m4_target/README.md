# M4 hardware target gate — evidence

> Date: 2026-11-27. Level: **L1** (loopback mock protocol advertising
> `hardware`; no system gateway, no ROS, no hardware). This proves the
> application-side target guard, not hardware execution.

## 1. Rule

`run` defaults to the mock target. A hardware target is opt-in and requires all
of:

1. `--target hardware`;
2. `--evidence-class L5`;
3. a gateway whose `/v1/health` advertises the `hardware` capability;
4. the normal per-session `Authorization` record with approved scopes.

`--target hardware` with L2/L3, or L5 with the mock target, is refused before
the recipe is loaded, the lease is acquired or any submission is made. The
system layer still owns the lease, safety, limits and stop latch; this gate
does not grant motion authority.

## 2. Tests

`tests/test_m4_target.py`:

| Case | Result |
|---|---|
| hardware target + L3 | exit 2; zero gateway requests |
| mock target + L5 | exit 2; zero gateway requests |
| hardware target + mock-only gateway | exit 2; no lease, no execute |
| hardware target + L5 + hardware gateway | exit 0; 2/2 steps; record `evidence_class: L5` |

The last case uses the loopback mock protocol with `capabilities=["hardware"]`;
it is **not** the system hardware gateway and not a robot run.

## 3. Verification

```text
with the tree extra: 208 passed
without py_trees:    186 passed, 5 skipped
gate:                OK
```

Raw output: [`pytest-tree.txt`](pytest-tree.txt),
[`pytest-bare.txt`](pytest-bare.txt), [`gate.txt`](gate.txt).

## 4. Limitations

- The hardware-capability gateway is a loopback test double, not the R10
  system gateway (`jcheng_vendor/gateway_node.py`).
- No ROS, DDS, device or hardware. The run record is L5-labelled only in the
  test double; it is not hardware evidence and must not be presented as such.
- The real M4 session still needs the chosen system gateway, C1–C5 and a
  separate, explicit per-session authorization.
