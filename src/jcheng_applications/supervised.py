"""One supervised recipe run: lease, execute, release, persist."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from .lease import LeaseSession
from .model import Authorization, Recipe, build_plan
from .runstore import RunStore, plan_digest
from .runner import RunReport, TaskRunner
from .write_transport import LeaseInfo


@dataclass(frozen=True)
class SupervisedResult:
    report: RunReport
    record_path: Path | None
    record_error: str | None
    lease: LeaseInfo
    lease_released: bool
    renewals: int
    lease_error: str | None
    release_error: str | None


def _lease_scope(recipe: Recipe) -> str:
    scopes = set(recipe.scopes)
    if not scopes:
        raise ValueError("recipe has no scopes")
    return "full" if len(scopes) > 1 else next(iter(scopes))


def run_supervised(
    gateway: Any,
    recipe: Recipe,
    authorization: Authorization,
    *,
    run_id: str,
    store: RunStore,
    recipe_hash: str,
    ttl_ms: int = 30_000,
    evidence_class: str = "L2",
    require_mock: bool = True,
) -> SupervisedResult:
    """Acquire a lease, run the recipe, release the lease and persist the report.

    The lease is acquired before the runner's preflight so the preflight sees a
    real lease reference. Any refusal still releases the lease. The report is
    written even when the run is refused or indeterminate.
    """

    if evidence_class not in {"L2", "L3"}:
        raise ValueError("evidence_class must be L2 or L3")
    if require_mock:
        gateway.check_mock()

    plan = build_plan(recipe, run_id)
    session = LeaseSession(
        gateway,
        holder=authorization.operator,
        scope=_lease_scope(recipe),
        ttl_ms=ttl_ms,
    )
    info = session.acquire()
    try:
        report = TaskRunner(gateway).run(recipe, authorization, run_id=run_id)
    finally:
        released = session.release()

    lease_ref = hashlib.sha256(info.lease_id.encode("utf-8")).hexdigest()
    record_error = None
    try:
        record_path = store.write_report(
            report,
            run_id=run_id,
            recipe_hash=recipe_hash,
            plan_hash=plan_digest(plan),
            evidence_class=evidence_class,
            lease_ref=lease_ref,
            lease_released=released,
        )
    except Exception as exc:  # noqa: BLE001 - keep the report, flag the evidence failure
        record_path = None
        record_error = f"{type(exc).__name__}: {exc}"
    return SupervisedResult(
        report=report,
        record_path=record_path,
        record_error=record_error,
        lease=info,
        lease_released=released,
        renewals=session.renewals,
        lease_error=session.error,
        release_error=session.release_error,
    )
