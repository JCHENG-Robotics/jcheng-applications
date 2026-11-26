"""CLI: validate, plan, check admission, run or audit one recipe.

``validate`` and ``plan`` are offline. ``check`` performs the M2 read-only
admission (GET only). ``run`` performs M3 supervised execution against a
gateway that advertises the ``mock`` capability; it refuses anything else.
``run`` submits motion and requires an explicit operator ``Authorization``
record. ``verify`` audits one persisted run record: it is read-only, makes no
network call and does not create the store directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .gateway import GatewayError
from .model import (
    RecipeError,
    authorization_from_mapping,
    build_plan,
    load_recipe,
)
from .runstore import RunStore
from .runner import RunState
from .supervised import run_supervised
from .transport import GatewayEndpoint, ReadOnlyGateway
from .write_transport import HttpGateway


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jcheng-applications",
        description=(
            "Validate recipes and build deterministic plans offline; "
            "check performs a read-only gateway admission."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="validate a recipe against the v0.1.0 recipe contract"
    )
    validate.add_argument("recipe", type=Path)

    plan = subparsers.add_parser(
        "plan", help="print the deterministic plan; no network and no robot"
    )
    plan.add_argument("recipe", type=Path)
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--json", action="store_true", dest="as_json")

    check = subparsers.add_parser(
        "check",
        help="read-only admission against a gateway; GET only, no motion",
    )
    check.add_argument(
        "--url",
        default=os.environ.get("JCHENG_GATEWAY_URL", ""),
        help="http(s)://host[:port]; defaults to JCHENG_GATEWAY_URL",
    )
    check.add_argument("--timeout", type=float, default=2.0)

    run = subparsers.add_parser(
        "run",
        help="supervised execution against a mock gateway; submits motion",
    )
    run.add_argument("recipe", type=Path)
    run.add_argument(
        "--authorization",
        type=Path,
        required=True,
        help="JSON Authorization record supplied by the operator",
    )
    run.add_argument("--run-id", required=True)
    run.add_argument(
        "--url",
        default=os.environ.get("JCHENG_GATEWAY_URL", ""),
        help="http(s)://host[:port]; defaults to JCHENG_GATEWAY_URL",
    )
    run.add_argument("--timeout", type=float, default=2.0)
    run.add_argument("--ttl-ms", type=int, default=30_000)
    run.add_argument(
        "--store-dir",
        default="~/.local/state/jcheng-applications/runs",
        help="append-only run records; must be outside the repository",
    )
    run.add_argument(
        "--evidence-class", choices=["L2", "L3"], default="L2"
    )
    run.add_argument(
        "--derived-from",
        default=None,
        help=(
            "predecessor run id; requires a new recipe version, run id and "
            "authorization session"
        ),
    )
    run.add_argument(
        "--engine",
        choices=["linear", "tree"],
        default="linear",
        help="tree requires the optional 'tree' extra (py_trees)",
    )
    run.add_argument(
        "--deadline-s",
        type=float,
        default=None,
        help="run deadline; tree engine only",
    )

    verify = subparsers.add_parser(
        "verify",
        help="verify a run record's hash chain and print its terminal summary",
    )
    verify.add_argument("run_id")
    verify.add_argument(
        "--store-dir",
        default="~/.local/state/jcheng-applications/runs",
        help="read-only run record directory; it is not created",
    )
    return parser


def _check(args: argparse.Namespace) -> int:
    token = os.environ.get("JCHENG_GATEWAY_TOKEN", "")
    if not args.url or not token:
        print(
            "error: --url/JCHENG_GATEWAY_URL and JCHENG_GATEWAY_TOKEN are required",
            file=sys.stderr,
        )
        return 2
    endpoint = GatewayEndpoint(
        base_url=args.url, token=token, timeout_s=args.timeout
    )
    with ReadOnlyGateway(endpoint) as gateway:
        report = gateway.check_admission()
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    if not report.ready:
        print(
            f"not ready: safety {report.safety.state}"
            + (" (stop latched)" if report.safety.stop_latched else ""),
            file=sys.stderr,
        )
        return 1
    return 0


def _run(args: argparse.Namespace) -> int:
    token = os.environ.get("JCHENG_GATEWAY_TOKEN", "")
    if not args.url or not token:
        print(
            "error: --url/JCHENG_GATEWAY_URL and JCHENG_GATEWAY_TOKEN are required",
            file=sys.stderr,
        )
        return 2
    store_dir = Path(args.store_dir).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    if store_dir == repo_root or repo_root in store_dir.parents:
        print("error: --store-dir must be outside the repository", file=sys.stderr)
        return 2

    recipe = load_recipe(args.recipe)
    authorization = authorization_from_mapping(
        json.loads(args.authorization.read_text(encoding="utf-8"))
    )
    endpoint = GatewayEndpoint(
        base_url=args.url, token=token, timeout_s=args.timeout
    )
    gateway = HttpGateway(endpoint, operator_id=authorization.operator)
    result = run_supervised(
        gateway,
        recipe,
        authorization,
        run_id=args.run_id,
        store=RunStore(store_dir),
        recipe_hash=hashlib.sha256(args.recipe.read_bytes()).hexdigest(),
        ttl_ms=args.ttl_ms,
        evidence_class=args.evidence_class,
        engine=args.engine,
        deadline_s=args.deadline_s,
        derived_from=args.derived_from,
    )
    print(
        json.dumps(
            {
                "run_id": result.report.run_id,
                "derived_from": result.derived_from,
                "state": result.report.state.value,
                "operator_required": result.report.operator_required,
                "operator_reason": result.report.operator_reason,
                "stop_confirmation": result.report.stop_confirmation,
                "physical_stop": result.report.physical_stop,
                "steps": [
                    {
                        "index": step.index,
                        "name": step.name,
                        "outcome": step.outcome,
                        "reason_code": step.reason_code,
                    }
                    for step in result.report.steps
                ],
                "record": str(result.record_path) if result.record_path else None,
                "record_error": result.record_error,
                "lease_released": result.lease_released,
                "renewals": result.renewals,
                "lease_error": result.lease_error,
                "release_error": result.release_error,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if result.record_error is not None:
        # The motion may have happened; the evidence is missing. Never exit 0.
        print(
            f"error: run record was not written: {result.record_error}",
            file=sys.stderr,
        )
        return 2
    if result.report.operator_required:
        # No motion authority in this state. The operator must inspect the
        # system and, if needed, start a new authorized run.
        print(
            "WAITING_FOR_OPERATOR: "
            + (result.report.operator_reason or "operator inspection required"),
            file=sys.stderr,
        )
        return 3
    return 0 if result.report.state is RunState.SUCCEEDED else 1


def _verify(args: argparse.Namespace) -> int:
    store = RunStore(Path(args.store_dir).expanduser(), create=False)
    try:
        path = store.path_for(args.run_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not path.exists():
        print(
            json.dumps(
                {
                    "run_id": args.run_id,
                    "record": str(path),
                    "verified": False,
                    "complete": False,
                    "error": "record not found",
                },
                indent=2,
                sort_keys=True,
            )
        )
        print(f"error: no record for run {args.run_id!r}", file=sys.stderr)
        return 1

    try:
        chain_ok = store.verify(args.run_id)
        finished = store.load_finished(args.run_id) if chain_ok else None
        started = store.load_started(args.run_id) if chain_ok else None
    except (OSError, UnicodeError, ValueError):
        # A record that cannot be parsed is a chain failure, not a usage error.
        chain_ok = False
        finished = None
        started = None
    steps = finished.get("steps") if finished else None
    if not isinstance(steps, list):
        steps = []
    result: dict[str, object] = {
        "run_id": args.run_id,
        "record": str(path),
        "verified": chain_ok,
        "complete": finished is not None,
        "recipe_name": (started or {}).get("recipe_name"),
        "recipe_version": (started or {}).get("recipe_version"),
        "evidence_class": (started or {}).get("evidence_class"),
        "derived_from": (started or {}).get("derived_from"),
        "state": finished.get("state") if finished else None,
        "abort_reason": finished.get("abort_reason") if finished else None,
        "stop_error": finished.get("stop_error") if finished else None,
        "stop_confirmation": finished.get("stop_confirmation") if finished else None,
        "physical_stop": finished.get("physical_stop") if finished else None,
        "operator_required": finished.get("operator_required") if finished else None,
        "operator_reason": finished.get("operator_reason") if finished else None,
        "report_hash": finished.get("report_hash") if finished else None,
        "steps": [
            {
                "index": step.get("index"),
                "name": step.get("name"),
                "outcome": step.get("outcome"),
                "reason_code": step.get("reason_code"),
                "bus_verified": step.get("bus_verified"),
            }
            for step in steps
            if isinstance(step, dict)
        ],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if not chain_ok:
        print("error: record hash chain does not verify", file=sys.stderr)
        return 1
    if finished is None:
        print("error: record has no terminal run_finished entry", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            return _check(args)
        if args.command == "run":
            return _run(args)
        if args.command == "verify":
            return _verify(args)
        recipe = load_recipe(args.recipe)
        if args.command == "validate":
            print(
                f"OK {recipe.name} {recipe.version}: "
                f"{len(recipe.steps)} step(s), scopes {','.join(recipe.scopes)}"
            )
            return 0

        plan = build_plan(recipe, args.run_id)
        if args.as_json:
            print(
                json.dumps(
                    {
                        "run_id": plan.run_id,
                        "recipe": {
                            "name": plan.recipe_name,
                            "version": plan.recipe_version,
                        },
                        "contract_hash": plan.contract_hash,
                        "required_scopes": list(plan.required_scopes),
                        "steps": [
                            {
                                "index": step.index,
                                "name": step.name,
                                "kind": step.kind,
                                "profile": step.profile,
                                "scopes": list(step.scopes),
                                "idempotency_key": step.idempotency_key,
                            }
                            for step in plan.steps
                        ],
                        "facts": {
                            name: {
                                "from_step": fact.from_step,
                                "index": fact.index,
                                "at_least_rad": fact.at_least_rad,
                                "at_most_rad": fact.at_most_rad,
                            }
                            for name, fact in plan.facts.items()
                        },
                        "branches": [
                            {
                                "id": branch.id,
                                "after": branch.after,
                                "required": branch.required,
                                "priority": branch.priority,
                                "conditions": [list(item) for item in branch.conditions],
                                "steps": [
                                    {
                                        "index": step.index,
                                        "name": step.name,
                                        "kind": step.kind,
                                        "profile": step.profile,
                                        "scopes": list(step.scopes),
                                        "idempotency_key": step.idempotency_key,
                                    }
                                    for step in branch.steps
                                ],
                            }
                            for branch in plan.branches
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        print(
            f"plan {plan.recipe_name} {plan.recipe_version} "
            f"run={plan.run_id} steps={len(plan.steps)} "
            f"branches={len(plan.branches)}"
        )
        for step in plan.steps:
            print(
                f"  {step.index:02d} {step.name}: {step.kind}/{step.profile} "
                f"scopes={','.join(step.scopes)} "
                f"idem={step.idempotency_key[:16]}..."
            )
        return 0
    except (GatewayError, RecipeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
