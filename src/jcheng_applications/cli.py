"""CLI: validate a recipe, print its plan, or check gateway admission.

``validate`` and ``plan`` are offline. ``check`` performs the M2 read-only
admission (GET only) and has no motion authority. There is deliberately no
``run`` command: pretending to run without the M2/M3 gateway path would invite
a hardware claim this repository cannot support.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .gateway import GatewayError
from .model import RecipeError, build_plan, load_recipe
from .transport import GatewayEndpoint, ReadOnlyGateway


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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            return _check(args)
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
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        print(
            f"plan {plan.recipe_name} {plan.recipe_version} "
            f"run={plan.run_id} steps={len(plan.steps)}"
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
