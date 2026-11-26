"""Offline CLI: validate a recipe or print its deterministic plan.

There is deliberately no ``run`` command in v0.1.0. The gateway transport is
M1; pretending to run without a contract-checked gateway would invite a
hardware claim the repository cannot support.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from . import __version__
from .model import RecipeError, build_plan, load_recipe


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jcheng-applications",
        description=(
            "Validate recipes and build deterministic plans offline. "
            "No gateway, robot or network is used."
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
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
    except (RecipeError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
