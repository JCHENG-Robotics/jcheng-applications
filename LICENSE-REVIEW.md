# License review — jcheng-applications

> Status: **pending** as of 2026-11-26.
> No `LICENSE` file exists. This repository follows the organization rule that
> no license is added before the review is signed off
> (`.github/docs/license-review.md`).

## 1. Current facts

| Item | Fact |
|---|---|
| `LICENSE` file | none |
| Code in v0.1.0 | Python orchestration core, tests, a shell gate and documentation |
| Runtime dependencies | `PyYAML` (MIT) |
| Test dependencies | `pytest` (MIT) |
| Robot or vendor code | none copied |
| Model weights or datasets | none |
| Ported code | none; the core is new |

## 2. Decision recorded on 2026-11-26

The repository is created **without** a `LICENSE` file until the ecosystem
review closes. Adding a license now would pre-empt the organization decision.

The review must decide before the ecosystem reaches v1.0:

1. The copyright holder and year for new repositories.
2. Whether MIT is the intended license for application repositories, matching
   the frozen `jcheng-manipulation` baseline.
3. Whether recipes authored by operators are code or data for licensing and
   contribution purposes.
4. How run reports and exported evidence are handled without implying they are
   covered by the repository license.

## 3. Third-party inventory

| Dependency | Use | Known/probable license | Risk |
|---|---|---|---|
| Python standard library | code | PSF | Low |
| PyYAML | recipe parsing | MIT | Low |
| pytest | tests only | MIT | Low |
| `py_trees` 2.6.0 (M1.5 candidate) | tree runtime for the task engine, not a v0.1.0 or runtime dependency | BSD-3-Clause | Low; tested in an isolated venv, wheel `78155ffa…89837` |
| `pydot` (transitive of py_trees) | graph rendering only; not used by the tick path | MIT | Low; pin before adoption. 4.0.1 resolved in the probe |
| `pyparsing` (transitive of pydot) | parser used by pydot | MIT | Low |
| HTTP/WS client (TBD at M2) | gateway transport | TBD | Decide before adoption |
| Recipe files | operator-authored intent sequences | Organization decision | Decide before accepting third-party recipes |

## 4. Open questions

- [ ] Confirm the copyright holder and year.
- [ ] Confirm the intended license for application repositories.
- [ ] Decide whether recipe files are code or data.
- [ ] Decide whether `py_trees` 2.6.0 (BSD-3) is approved as the M1.5 tree
      runtime and whether a notice is required. It declares `pydot>=1.4`;
      pydot and pyparsing must be locked with it.
- [ ] Confirm that pydot 4.x is acceptable for py_trees 2.6 rendering; the
      tick path does not use it and rendering compatibility is untested.
- [ ] Decide the notice requirement for the M2 HTTP/WS client.
- [ ] Record the ecosystem-wide decision in `.github/docs/license-review.md`.

## 5. Related

- [`.github/docs/license-review.md`](https://github.com/JCHENG-Robotics/.github/blob/main/docs/license-review.md)
- [Acceptance report](ACCEPTANCE.md)
- [README](README.md)
