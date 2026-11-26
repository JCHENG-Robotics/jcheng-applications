"""Derived Plan identity rules.

A Derived Plan is a new run of a new recipe revision under a new authorization.
This module refuses a request that reuses the predecessor's run id, recipe
version or authorization session, or whose predecessor record is missing or
does not verify. It performs no I/O beyond reading the run store and grants no
motion authority.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Authorization, Recipe
from .runstore import RunStore


class DerivedPlanError(ValueError):
    """The requested Derived Plan does not satisfy the identity rules."""


@dataclass(frozen=True)
class DerivedPlanEvidence:
    predecessor_run_id: str
    predecessor_recipe_version: str
    predecessor_session_id: str


def validate_derived_plan(
    *,
    recipe: Recipe,
    authorization: Authorization,
    run_id: str,
    derived_from: str,
    store: RunStore,
) -> DerivedPlanEvidence:
    """Validate a Derived Plan before any lease, submission or network call."""

    if not isinstance(derived_from, str) or not derived_from.strip():
        raise DerivedPlanError("derived_from must be a non-empty run id")
    if derived_from == run_id:
        raise DerivedPlanError(
            "a Derived Plan requires a new run id, not the predecessor's"
        )
    predecessor = store.load_started(derived_from)
    if predecessor is None:
        raise DerivedPlanError(
            f"predecessor run {derived_from!r} is missing or its record does "
            "not verify"
        )
    if predecessor.get("recipe_name") != recipe.name:
        raise DerivedPlanError(
            "a Derived Plan must revise the predecessor's recipe, not a "
            "different recipe"
        )
    predecessor_version = predecessor.get("recipe_version")
    if not isinstance(predecessor_version, str) or not predecessor_version.strip():
        raise DerivedPlanError("predecessor record has no recipe version")
    if predecessor_version == recipe.version:
        raise DerivedPlanError(
            "a Derived Plan requires a new recipe version"
        )
    predecessor_session = predecessor.get("session_id")
    if not isinstance(predecessor_session, str) or not predecessor_session.strip():
        raise DerivedPlanError("predecessor record has no authorization session")
    if predecessor_session == authorization.session_id:
        raise DerivedPlanError(
            "a Derived Plan requires a new authorization session"
        )
    return DerivedPlanEvidence(
        predecessor_run_id=derived_from,
        predecessor_recipe_version=predecessor_version,
        predecessor_session_id=predecessor_session,
    )
