"""One supervised recipe run: lease, execute, release, persist."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any, Callable

from .core.preflight import PreflightRefusal
from .derived import validate_derived_plan
from .gateway import GatewayProtocolError
from .lease import LeaseSession
from .model import Authorization, Recipe, build_plan
from .runstore import RunStore, plan_digest
from .runner import RunReport, TaskRunner, aborted_report
from .tree import TreeEngineUnavailable
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
    derived_from: str | None = None


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
    engine: str = "linear",
    deadline_s: float | None = None,
    clock: Callable[[], datetime] | None = None,
    derived_from: str | None = None,
) -> SupervisedResult:
    """Acquire a lease, run the recipe, release the lease and persist the report.

    ``engine="linear"`` is the default. ``engine="tree"`` requires the optional
    ``tree`` extra (py_trees) and is the M1.5 production tree runtime. The
    lease is acquired before preflight so the preflight sees a real lease
    reference; any refusal still releases it. The report is written even when
    the run is refused or indeterminate.
    """

    if evidence_class not in {"L2", "L3", "L5"}:
        raise ValueError("evidence_class must be L2, L3 or L5")
    if require_mock and evidence_class == "L5":
        raise ValueError("L5 evidence requires a hardware target")
    if not require_mock and evidence_class != "L5":
        raise ValueError("a hardware target requires L5 evidence")
    if engine not in {"linear", "tree"}:
        raise ValueError("engine must be 'linear' or 'tree'")
    if deadline_s is not None and engine != "tree":
        raise ValueError("deadline_s requires the tree engine")
    if deadline_s is not None and deadline_s <= 0:
        raise ValueError("deadline_s must be positive")

    if derived_from is not None:
        validate_derived_plan(
            recipe=recipe,
            authorization=authorization,
            run_id=run_id,
            derived_from=derived_from,
            store=store,
        )

    tree_run = None
    if engine == "tree":
        try:
            from .tree.runtime import run_tree
        except ModuleNotFoundError as exc:
            raise TreeEngineUnavailable(
                "the tree engine requires the optional 'tree' extra "
                "(pip install -e '.[tree]')"
            ) from exc
        tree_run = run_tree

    if require_mock:
        gateway.check_mock()
    else:
        gateway.check_hardware()

    plan = build_plan(recipe, run_id)
    session = LeaseSession(
        gateway,
        holder=authorization.operator,
        scope=_lease_scope(recipe),
        ttl_ms=ttl_ms,
    )
    info = session.acquire()
    try:
        if tree_run is None:
            runner = (
                TaskRunner(gateway)
                if clock is None
                else TaskRunner(gateway, clock=clock)
            )
            report = runner.run(recipe, authorization, run_id=run_id)
        else:
            tree_kwargs: dict[str, Any] = {"deadline_s": deadline_s}
            if clock is not None:
                tree_kwargs["clock"] = clock
            try:
                report = tree_run(
                    gateway,
                    recipe,
                    authorization,
                    plan=plan,
                    run_id=run_id,
                    lease_id=info.lease_id,
                    **tree_kwargs,
                )
            except (PreflightRefusal, GatewayProtocolError) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                report = (
                    aborted_report(recipe, authorization, run_id, reason)
                    if clock is None
                    else aborted_report(
                        recipe, authorization, run_id, reason, clock=clock
                    )
                )
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
            derived_from=derived_from,
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
        derived_from=derived_from,
    )
