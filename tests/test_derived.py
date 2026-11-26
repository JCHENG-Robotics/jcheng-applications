"""Derived Plan identity rules: new run, recipe version and authorization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jcheng_applications.derived import DerivedPlanError, validate_derived_plan
from jcheng_applications.model import (
    Authorization,
    load_recipe,
    recipe_from_mapping,
)
from jcheng_applications.runner import RunState, make_report
from jcheng_applications.runstore import RunStore
from jcheng_applications.supervised import run_supervised
from jcheng_applications.transport import GatewayEndpoint
from jcheng_applications.write_transport import HttpGateway

from mock_gateway import NOW, TOKEN, MockGatewayServer, completed

ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATH = ROOT / "recipes" / "example-supervised-transfer.yaml"
RECIPE = load_recipe(RECIPE_PATH)
PREDECESSOR = "pred-1"


def _raw() -> dict:
    return yaml.safe_load(RECIPE_PATH.read_text(encoding="utf-8"))


def _derived_recipe(version: str = "0.1.1"):
    raw = _raw()
    raw["version"] = version
    return recipe_from_mapping(raw)


def _auth(session: str = "derived-session") -> Authorization:
    return Authorization(
        operator="operator",
        session_id=session,
        scopes=frozenset({"arm", "gripper"}),
        approved=True,
        approved_at=NOW,
    )


def _store_with_predecessor(tmp_path: Path) -> RunStore:
    store = RunStore(tmp_path / "runs")
    report = make_report(
        recipe=RECIPE,
        run_id=PREDECESSOR,
        operator="operator",
        session_id="pred-session",
        state=RunState.INDETERMINATE,
        started_at=NOW,
        finished_at=NOW,
        steps=(),
        abort_reason="indeterminate",
        stop_error=None,
    )
    store.write_report(
        report,
        run_id=PREDECESSOR,
        recipe_hash="recipe-hash",
        plan_hash="plan-hash",
        evidence_class="L2",
    )
    return store


def test_valid_derived_plan_returns_evidence(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    evidence = validate_derived_plan(
        recipe=_derived_recipe(),
        authorization=_auth(),
        run_id="derived-1",
        derived_from=PREDECESSOR,
        store=store,
    )
    assert evidence.predecessor_run_id == PREDECESSOR
    assert evidence.predecessor_recipe_version == "0.1.0"
    assert evidence.predecessor_session_id == "pred-session"


def test_same_recipe_version_is_refused(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    with pytest.raises(DerivedPlanError, match="new recipe version"):
        validate_derived_plan(
            recipe=RECIPE,
            authorization=_auth(),
            run_id="derived-1",
            derived_from=PREDECESSOR,
            store=store,
        )


def test_same_run_id_is_refused(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    with pytest.raises(DerivedPlanError, match="new run id"):
        validate_derived_plan(
            recipe=_derived_recipe(),
            authorization=_auth(),
            run_id=PREDECESSOR,
            derived_from=PREDECESSOR,
            store=store,
        )


def test_same_authorization_session_is_refused(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    with pytest.raises(DerivedPlanError, match="new authorization session"):
        validate_derived_plan(
            recipe=_derived_recipe(),
            authorization=_auth("pred-session"),
            run_id="derived-1",
            derived_from=PREDECESSOR,
            store=store,
        )


def test_missing_predecessor_is_refused(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with pytest.raises(DerivedPlanError, match="missing"):
        validate_derived_plan(
            recipe=_derived_recipe(),
            authorization=_auth(),
            run_id="derived-1",
            derived_from=PREDECESSOR,
            store=store,
        )


def test_different_recipe_is_refused(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    other = load_recipe(ROOT / "recipes" / "example-declared-branch.yaml")
    with pytest.raises(DerivedPlanError, match="different recipe"):
        validate_derived_plan(
            recipe=other,
            authorization=_auth(),
            run_id="derived-1",
            derived_from=PREDECESSOR,
            store=store,
        )


def test_tampered_predecessor_is_treated_as_missing(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    path = store.path_for(PREDECESSOR)
    path.write_text(
        path.read_text(encoding="utf-8").replace("INDETERMINATE", "SUCCEEDED"),
        encoding="utf-8",
    )
    assert store.load_started(PREDECESSOR) is None
    with pytest.raises(DerivedPlanError, match="missing"):
        validate_derived_plan(
            recipe=_derived_recipe(),
            authorization=_auth(),
            run_id="derived-1",
            derived_from=PREDECESSOR,
            store=store,
        )


def test_supervised_derived_run_records_the_predecessor(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    with MockGatewayServer() as server:
        server.probe.results.extend([completed(), completed()])
        endpoint = GatewayEndpoint(
            base_url=f"http://127.0.0.1:{server.port}", token=TOKEN, timeout_s=1.0
        )
        result = run_supervised(
            HttpGateway(endpoint, operator_id="operator"),
            _derived_recipe(),
            _auth(),
            run_id="derived-1",
            store=store,
            recipe_hash="hash",
            evidence_class="L2",
            derived_from=PREDECESSOR,
        )
    assert result.report.state.value == "SUCCEEDED"
    assert result.derived_from == PREDECESSOR
    started = store.load_started("derived-1")
    assert started is not None and started["derived_from"] == PREDECESSOR


def test_derived_refusal_happens_before_any_gateway_call(tmp_path: Path) -> None:
    store = _store_with_predecessor(tmp_path)
    with MockGatewayServer() as server:
        endpoint = GatewayEndpoint(
            base_url=f"http://127.0.0.1:{server.port}", token=TOKEN, timeout_s=1.0
        )
        with pytest.raises(DerivedPlanError):
            run_supervised(
                HttpGateway(endpoint, operator_id="operator"),
                RECIPE,
                _auth(),
                run_id="derived-1",
                store=store,
                recipe_hash="hash",
                evidence_class="L2",
                derived_from=PREDECESSOR,
            )
        assert server.probe.paths() == []
