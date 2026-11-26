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
| Runtime dependencies | `PyYAML` (MIT); optional `tree` extra: `py_trees==2.6.0` (BSD-3), `pydot==4.0.1`, `pyparsing==3.3.3` (MIT) |
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
| `py_trees` 2.6.0 | optional `tree` extra for the M1.5 engine; not the default engine | BSD-3-Clause | Low; tested in an isolated venv and in CI, wheel `78155ffa…89837` |
| `pydot` (transitive of py_trees) | graph rendering only; not used by the tick path | MIT | Low; pinned 4.0.1 in the `tree` extra |
| `pyparsing` (transitive of pydot) | parser used by pydot | MIT | Low |
| HTTP/WS client | M2/M3 gateway transport | Python standard library `http.client` (PSF) | None; no third-party client |
| Recipe files | operator-authored intent sequences | Organization decision | Decide before accepting third-party recipes |

## 4. Open questions

- [ ] Confirm the copyright holder and year.
- [ ] Confirm the intended license for application repositories.
- [ ] Decide whether recipe files are code or data.
- [ ] Decide whether `py_trees` 2.6.0 (BSD-3) is approved as the optional
      `tree` extra and whether a notice is required. It declares `pydot>=1.4`;
      pydot 4.0.1 and pyparsing 3.3.3 are pinned with it.
- [ ] Decide whether the tree engine may become the default engine after the
      review; it is opt-in today.
- [ ] Confirm that pydot 4.x is acceptable for py_trees 2.6 rendering; the
      tick path does not use it and rendering compatibility is untested.
- [ ] Record the ecosystem-wide decision in `.github/docs/license-review.md`.

## 5. Related

- [`.github/docs/license-review.md`](https://github.com/JCHENG-Robotics/.github/blob/main/docs/license-review.md)
- [Acceptance report](ACCEPTANCE.md)
- [README](README.md)
