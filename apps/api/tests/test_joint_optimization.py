"""Regression tests for FASE 2F.8: feature-flagged, plan-aware marker
refinement (PlanningCoordinator._refine_plan_with_geometry /
_refine_marker_geometry).

Orchestration tests mock `_refine_marker_geometry` -- the only genuinely
expensive step (real ALNS search) -- so they run fast and deterministically;
one end-to-end test exercises the real GlobalNestingSearch path on the
smallest possible real composition (M x1) to prove the wiring works against
real geometry, not just against the mock.
"""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from sqlalchemy import select

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator, RunCancelled
from costura_optima.domain.operational_heuristic_solver import OperationalHeuristicSolver
from costura_optima.domain.production_models import PlanningConfig, ValidatedMarkerCandidate
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.production_solver import CpSatRefinementSolver
from costura_optima.infrastructure.db_models import MarkerArtifactORM, OptimizationRunORM, PatternSetVersionORM, ProductionOrderORM
from costura_optima.patterns.persistence import generate_and_persist


def marker(name, composition, length=100_000, layers=()):
    pieces = sum(composition.values()) * 5
    return ValidatedMarkerCandidate(
        marker_hash=name, content_key=name, composition=tuple(composition.items()),
        marker_length_units=length, usable_width_units=176_000,
        piece_area_units2=max(1, pieces * 100), marker_area_units2=max(1, pieces * 120),
        waste_area_units2=max(1, pieces * 20), efficiency_percentage=83.333333,
        placements=tuple({"piece": index} for index in range(pieces)),
        validation_certificate={"status": "VALIDATED"}, geometry_engine_version="fixture-local",
        marker_search_status="VALIDATED_FEASIBLE", input_hash=name, lower_bound_length_units=1,
        origin="SINGLE_SIZE_FALLBACK" if len(composition) == 1 else "PROPORTIONAL_DEMAND_RATIO",
        candidate_layers=layers,
    )


def _create_order(client, database, catalog_ids, demand):
    with database() as session:
        generate_and_persist(session)
    return client.post("/api/v1/production-orders", json={
        "garment_model_version_id": catalog_ids["version"],
        "fabric_configuration_id": catalog_ids["fabric"],
        "cutting_table_configuration_id": catalog_ids["table"],
        "demand": [{"size_code": size, "quantity": quantity} for size, quantity in demand.items()],
    }).json()


def _pair_with_fallback(size, quantity, length):
    """OperationalHeuristicSolver always requires a quantity-1 single-size
    fallback marker per demanded size (see _fallback_choices) -- the
    fallback's length is set equal to the pair's so using it twice (for
    quantity=2) is clearly worse (2x fabric), keeping the pair marker
    reliably the winning plan's choice.
    """
    pair = marker(f"pair-{size}{quantity}", {size: quantity}, length=length)
    fallback = marker(f"fallback-{size}", {size: 1}, length=length)
    return pair, fallback


def _best_valid_from_markers(demand, maximum_overproduction, markers, max_layers=30):
    heuristic = OperationalHeuristicSolver(max_layers=max_layers, beam_width=10)
    solution = heuristic.solve(demand, maximum_overproduction, markers)
    assert solution.spreads
    evidence = {
        "order_hash": "o", "input_hash": "i", "pattern_hash": "p", "fabric_snapshot": {}, "table_snapshot": {},
        "policy": {}, "candidate_generation_config": {}, "geometry_config": {}, "geometry_version": "fixture-local",
        "planner_config": {}, "planner_version": "heuristic", "seed": 1,
        "marker_hashes": sorted(item.marker_hash for item in markers), "solver_status": solution.planning_status,
        "objective_stages": list(solution.objective_stages),
        "validation_certificate": [item.validation_certificate for item in markers],
    }
    report = IndependentProductionPlanValidator().validate(
        solution, markers, demand, maximum_overproduction, max_layers, 700_000, evidence,
    )
    assert report.status == "VALIDATED_PLAN"
    return [(solution, report)]


def _setup(client, database, catalog_ids, demand, *, joint_optimization_enabled):
    services_enqueue_patch = None
    order = _create_order(client, database, catalog_ids, demand)
    run_response = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": f"2f8-{joint_optimization_enabled}-{'-'.join(demand)}"},
        json={"planner_refinement_engine": "heuristic", "joint_optimization_enabled": joint_optimization_enabled},
    ).json()
    session = database()
    run = session.get(OptimizationRunORM, run_response["id"])
    order_row = session.get(ProductionOrderORM, order["id"])
    pattern_set = session.get(PatternSetVersionORM, order_row.pattern_set_version_id)
    coordinator = PlanningCoordinator(session)
    heuristic = OperationalHeuristicSolver(max_layers=30, beam_width=10)
    refinement = CpSatRefinementSolver(PlanningConfig(max_layers=30, time_limit_seconds=5, deterministic=True, seed=1))
    validator = IndependentProductionPlanValidator()
    config = dict(run.configuration)
    return session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config


@pytest.fixture(autouse=True)
def _no_real_enqueue(monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)


def test_flag_off_never_calls_refinement(client, catalog_ids, database):
    demand = {"M": 2}
    maximum_overproduction = {"M": 0}
    session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config = _setup(
        client, database, catalog_ids, demand, joint_optimization_enabled=False,
    )
    pair, fallback = _pair_with_fallback("M", 2, 200_000)
    markers = [pair, fallback]
    best_valid = _best_valid_from_markers(demand, maximum_overproduction, markers)
    with patch.object(PlanningCoordinator, "_refine_marker_geometry") as mocked:
        result_valid, result_markers = coordinator._refine_plan_with_geometry(
            run, order_row, pattern_set, demand, maximum_overproduction, 30, 700_000,
            markers, best_valid, heuristic, refinement, validator, config, None,
        )
    mocked.assert_not_called()
    assert result_valid == best_valid
    assert result_markers == markers


def test_flag_on_substitutes_an_improved_marker_and_never_worsens_the_plan(client, catalog_ids, database):
    demand = {"M": 2}
    maximum_overproduction = {"M": 0}
    session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config = _setup(
        client, database, catalog_ids, demand, joint_optimization_enabled=True,
    )
    original, fallback = _pair_with_fallback("M", 2, 200_000)
    improved = replace(original, marker_hash="m2-refined", content_key="m2-refined", marker_length_units=150_000)
    markers = [original, fallback]
    best_valid = _best_valid_from_markers(demand, maximum_overproduction, markers)
    key_before = coordinator._best_key(best_valid)

    with patch.object(PlanningCoordinator, "_refine_marker_geometry", return_value=improved) as mocked:
        result_valid, result_markers = coordinator._refine_plan_with_geometry(
            run, order_row, pattern_set, demand, maximum_overproduction, 30, 700_000,
            markers, best_valid, heuristic, refinement, validator, config, None,
        )
    mocked.assert_called_once()
    assert any(item.marker_hash == "m2-refined" for item in result_markers)
    key_after = coordinator._best_key(result_valid)
    assert key_after <= key_before
    assert key_after < key_before  # the shorter marker strictly reduces total fabric here
    assert run.audit["joint_optimization"]["markers_refined"] == 1
    assert run.audit["joint_optimization"]["plan_improved"] is True


def test_no_improvement_never_replaces_the_plan(client, catalog_ids, database):
    demand = {"M": 2}
    maximum_overproduction = {"M": 0}
    session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config = _setup(
        client, database, catalog_ids, demand, joint_optimization_enabled=True,
    )
    original, fallback = _pair_with_fallback("M", 2, 200_000)
    markers = [original, fallback]
    best_valid = _best_valid_from_markers(demand, maximum_overproduction, markers)

    with patch.object(PlanningCoordinator, "_refine_marker_geometry", return_value=None) as mocked:
        result_valid, result_markers = coordinator._refine_plan_with_geometry(
            run, order_row, pattern_set, demand, maximum_overproduction, 30, 700_000,
            markers, best_valid, heuristic, refinement, validator, config, None,
        )
    mocked.assert_called_once()
    assert result_valid == best_valid
    assert result_markers == markers
    assert run.audit["joint_optimization"]["markers_refined"] == 0
    assert run.audit["joint_optimization"]["plan_improved"] is False


def test_cancellation_mid_refinement_propagates_and_stops_further_markers(client, catalog_ids, database):
    demand = {"M": 1, "L": 1}
    maximum_overproduction = {"M": 0, "L": 0}
    session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config = _setup(
        client, database, catalog_ids, demand, joint_optimization_enabled=True,
    )
    markers = [marker("m1", {"M": 1}, length=100_000), marker("l1", {"L": 1}, length=110_000)]
    best_valid = _best_valid_from_markers(demand, maximum_overproduction, markers)

    calls = {"n": 0}

    def fake_check_cancel(run_arg, deadline=None):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RunCancelled()

    coordinator._check_cancel = fake_check_cancel
    with patch.object(PlanningCoordinator, "_refine_marker_geometry") as mocked, pytest.raises(RunCancelled):
        coordinator._refine_plan_with_geometry(
            run, order_row, pattern_set, demand, maximum_overproduction, 30, 700_000,
            markers, best_valid, heuristic, refinement, validator, config, None,
        )
    assert mocked.call_count <= 1


def test_orchestration_is_deterministic_given_identical_inputs(client, catalog_ids, database):
    demand = {"M": 2}
    maximum_overproduction = {"M": 0}
    session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config = _setup(
        client, database, catalog_ids, demand, joint_optimization_enabled=True,
    )
    original, fallback = _pair_with_fallback("M", 2, 200_000)
    markers = [original, fallback]
    improved = replace(original, marker_hash="m2-refined", content_key="m2-refined", marker_length_units=150_000)
    best_valid = _best_valid_from_markers(demand, maximum_overproduction, markers)

    results = []
    for _ in range(2):
        run.audit = {}
        with patch.object(PlanningCoordinator, "_refine_marker_geometry", return_value=improved):
            result_valid, result_markers = coordinator._refine_plan_with_geometry(
                run, order_row, pattern_set, demand, maximum_overproduction, 30, 700_000,
                markers, best_valid, heuristic, refinement, validator, config, None,
            )
        results.append((coordinator._best_key(result_valid), tuple(sorted(m.marker_hash for m in result_markers))))
    assert results[0] == results[1]


def test_refined_marker_gets_a_distinct_cache_entry_from_the_original(client, catalog_ids, database):
    """End-to-end: real GlobalNestingSearch, real DB geometry, real cache.
    Uses the smallest possible real composition (M x1, 5 pieces) to bound
    runtime while still exercising the full, unmocked refinement path.
    """
    demand = {"M": 1}
    maximum_overproduction = {"M": 0}
    session, run, order_row, pattern_set, coordinator, heuristic, refinement, validator, config = _setup(
        client, database, catalog_ids, demand, joint_optimization_enabled=True,
    )
    config["joint_optimization_refinement_budget_seconds"] = 60.0
    config["joint_optimization_max_markers_to_refine"] = 1

    marker_data, cache_hit, _elapsed, diagnostics = coordinator._evaluate_candidate(run, order_row, pattern_set, {"M": 1})
    assert marker_data is not None, diagnostics
    best_valid = _best_valid_from_markers(demand, maximum_overproduction, [marker_data])

    original_rows_before = session.scalars(select(MarkerArtifactORM)).all()
    result_valid, result_markers = coordinator._refine_plan_with_geometry(
        run, order_row, pattern_set, demand, maximum_overproduction, 30, 700_000,
        [marker_data], best_valid, heuristic, refinement, validator, config, None,
    )
    rows_after = session.scalars(select(MarkerArtifactORM)).all()

    # The original cached marker must still be present and byte-identical
    # (never overwritten), regardless of whether refinement found an
    # improvement for this particular composition.
    original_row_after = session.get(MarkerArtifactORM, next(
        row.marker_hash for row in original_rows_before if row.content_key == marker_data.content_key
    ))
    assert original_row_after is not None
    assert original_row_after.marker_payload["result_hash"] == marker_data.marker_hash

    if len(rows_after) > len(original_rows_before):
        refined_hashes = {m.marker_hash for m in result_markers} - {m.marker_hash for m in [marker_data]}
        assert refined_hashes, "a new artifact row was persisted but no refined marker hash is in the returned catalog"
