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
| HTTP/WS client (TBD at M2) | gateway transport | TBD | Decide before adoption |
| Recipe files | operator-authored intent sequences | Organization decision | Decide before accepting third-party recipes |

## 4. Open questions

- [ ] Confirm the copyright holder and year.
- [ ] Confirm the intended license for application repositories.
- [ ] Decide whether recipe files are code or data.
- [ ] Decide the notice requirement for the M2 HTTP/WS client.
- [ ] Record the ecosystem-wide decision in `.github/docs/license-review.md`.

## 5. Related

- [`.github/docs/license-review.md`](https://github.com/JCHENG-Robotics/.github/blob/main/docs/license-review.md)
- [Acceptance report](ACCEPTANCE.md)
- [README](README.md)
