import inspect

import pytest
from sqlalchemy import select

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator, RunTimedOut
from costura_optima.domain.candidate_funnel import select_geometry_top_k
from costura_optima.domain.candidate_generator import CandidateCompositionGenerator
from costura_optima.domain.operational_heuristic_solver import OperationalCoveragePlanner, OperationalHeuristicSolver
from costura_optima.domain.production_models import CandidateGenerationConfig, ValidatedMarkerCandidate
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.production_solver import ProductionSolver
from costura_optima.infrastructure.db_models import OptimizationRunORM, OptimizationSolutionORM
from costura_optima.patterns.persistence import generate_and_persist


LARGE_ORDER = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
REGRESSION_CASES = (
    {"S": 3},
    {"S": 3, "M": 20, "L": 10, "XL": 12, "XXL": 30},
    {"XS": 1, "S": 1, "M": 1, "L": 1, "XL": 1, "XXL": 1, "XXXL": 1},
    {"M": 100},
    {"XXXL": 31},
)


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


def evidence(solution, markers):
    return {"order_hash": "o", "input_hash": "i", "pattern_hash": "p", "fabric_snapshot": {},
            "table_snapshot": {}, "policy": {}, "candidate_generation_config": {}, "geometry_config": {},
            "geometry_version": "fixture-local", "planner_config": {}, "planner_version": "heuristic",
            "seed": 1, "marker_hashes": sorted(item.marker_hash for item in markers),
            "solver_status": solution.planning_status, "objective_stages": list(solution.objective_stages),
            "validation_certificate": [item.validation_certificate for item in markers]}


def test_heuristic_only_works_and_implements_solver_abstraction():
    catalog = (marker("pair", {"S": 1, "M": 1}, layers=(10,)), marker("s", {"S": 1}), marker("m", {"M": 1}))
    solver: ProductionSolver = OperationalHeuristicSolver(max_layers=30, beam_width=5)
    solution = solver.solve({"S": 10, "M": 10}, {"S": 0, "M": 0}, catalog)
    assert solution.planning_optimality == "HEURISTIC_FEASIBLE"
    assert solution.spread_count == 1
    assert solution.produced_by_size == {"M": 10, "S": 10}


def test_heuristic_implementation_has_no_ortools_import():
    source = inspect.getsource(inspect.getmodule(OperationalHeuristicSolver))
    assert "ortools" not in source.lower()
    assert "production_planner" not in source


def test_fallback_plan_is_guaranteed_and_compacted():
    solution = OperationalCoveragePlanner(max_layers=30).solve({"M": 42}, {"M": 0}, (marker("m", {"M": 1}),))
    assert [spread.layers for spread in solution.spreads] == [30, 12]
    assert solution.produced_by_size["M"] == 42


def test_greedy_incumbent_prefers_large_validated_marker():
    catalog = (marker("pair", {"S": 1, "M": 1}, 150_000, (10,)), marker("s", {"S": 1}), marker("m", {"M": 1}))
    solver = OperationalHeuristicSolver(max_layers=30, beam_width=1)
    choices = solver._greedy_choices({"S": 10, "M": 10}, {"S": 0, "M": 0}, catalog)
    assert choices == ((0, 10),)


def test_beam_never_regresses_fallback_and_can_consolidate():
    catalog = (marker("all", {"S": 1, "M": 1, "L": 1}, 200_000, (10,)),
               marker("s", {"S": 1}), marker("m", {"M": 1}), marker("l", {"L": 1}))
    solution = OperationalHeuristicSolver(max_layers=30, beam_width=10).solve(
        {"S": 10, "M": 10, "L": 10}, {"S": 0, "M": 0, "L": 0}, catalog)
    assert solution.spread_count == 1 and solution.marker_design_count == 1


def test_candidate_funnel_limits_geometry_and_starts_with_large_marker():
    demand = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55}
    generated = CandidateCompositionGenerator(CandidateGenerationConfig(max_candidate_compositions=48)).generate(
        demand, {size: 2 for size in demand}, {size: 100 for size in demand}, {size: True for size in demand},
        176_000, 700_000, max_layers=30,
    )
    funnel = select_geometry_top_k(generated.candidates, 10)
    assert len(funnel.selected) == 10 < len(generated.candidates)
    assert len(funnel.selected[0].composition) > 1
    assert funnel.counts["geometry_top_k"] == 10
    assert all(item.estimated_length_units >= item.area_lower_bound_units for item in funnel.selected)


def test_candidate_funnel_never_sacrifices_feasibility_fallbacks_to_small_top_k():
    catalog = (
        CandidateCompositionGenerator(CandidateGenerationConfig(max_candidate_compositions=48)).generate(
            {"S": 10, "M": 10}, {"S": 0, "M": 0}, {"S": 100, "M": 100},
            {"S": True, "M": True}, 1_000, 100_000, max_layers=30,
        ).candidates
    )
    funnel = select_geometry_top_k(catalog, 1)
    fallbacks = [item for item in funnel.selected if item.origin == "SINGLE_SIZE_FALLBACK"]
    assert {item.composition[0][0] for item in fallbacks} == {"S", "M"}
    assert len(funnel.selected[0].composition) > 1
    assert funnel.counts["geometry_top_k_requested"] == 1


def test_area_lower_bound_pruning_includes_longitudinal_margins():
    generated = CandidateCompositionGenerator(CandidateGenerationConfig()).generate(
        {"M": 1}, {"M": 0}, {"M": 9_000}, {"M": True},
        usable_width_units=100, max_marker_length_units=100,
        length_margins_units=20,
    )
    assert not generated.candidates
    assert {item["reason"] for item in generated.pruned} == {"area_lower_bound"}


def test_large_order_heuristic_has_no_shortage_and_uses_only_validated_markers():
    catalog = tuple(marker(size.lower(), {size: 1}) for size, quantity in LARGE_ORDER.items() if quantity)
    solution = OperationalHeuristicSolver(max_layers=30, beam_width=10).solve(
        LARGE_ORDER, {size: 0 for size in LARGE_ORDER}, catalog)
    assert all(solution.produced_by_size[size] >= quantity for size, quantity in LARGE_ORDER.items())
    assert all(spread.layers <= 30 and spread.marker_hash in {item.marker_hash for item in catalog} for spread in solution.spreads)
    report = IndependentProductionPlanValidator().validate(
        solution, catalog, LARGE_ORDER, {size: 0 for size in LARGE_ORDER}, 30, 700_000,
        evidence(solution, catalog),
    )
    assert report.status == "VALIDATED_PLAN"


@pytest.mark.parametrize("demand", REGRESSION_CASES, ids=("A", "B", "C", "D", "E"))
def test_a_to_e_regression_is_feasible_in_heuristic_only_mode(demand):
    catalog = tuple(marker(size.lower(), {size: 1}) for size, quantity in demand.items() if quantity)
    maximum = {size: 0 for size in demand}
    solution = OperationalHeuristicSolver(max_layers=30, beam_width=10).solve(demand, maximum, catalog)
    assert solution.planning_status == "FEASIBLE"
    assert solution.produced_by_size == demand
    report = IndependentProductionPlanValidator().validate(
        solution, catalog, demand, maximum, 30, 700_000, evidence(solution, catalog),
    )
    assert report.status == "VALIDATED_PLAN"


def test_missing_fallback_is_reported_without_cloud_or_exact_solver():
    solution = OperationalHeuristicSolver().solve({"S": 1, "M": 1}, {"S": 0, "M": 0}, (marker("s", {"S": 1}),))
    assert not solution.spreads
    assert solution.planning_optimality == "HEURISTIC_INFEASIBLE"


def test_timeout_preserves_a_published_incumbent(client, catalog_ids, database, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    with database() as session:
        generate_and_persist(session)
    order = client.post("/api/v1/production-orders", json={
        "garment_model_version_id": catalog_ids["version"],
        "fabric_configuration_id": catalog_ids["fabric"],
        "cutting_table_configuration_id": catalog_ids["table"],
        "demand": [{"size_code": "M", "quantity": 3}],
    }).json()
    run = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "timeout-with-incumbent"},
        json={"planner_refinement_engine": "heuristic"},
    ).json()
    with database() as session:
        session.add(OptimizationSolutionORM(
            id="timeout-incumbent", optimization_run_id=run["id"], solution_hash="solution",
            fingerprint="fingerprint", rank=999, planning_status="FEASIBLE",
            planning_optimality="HEURISTIC_FEASIBLE", solution_origin="OPERATIONAL_HEURISTIC",
            metrics={}, validation_certificate={"status": "VALIDATED_PLAN"},
            explanation="BEST_VALIDATED_SO_FAR",
        ))
        session.commit()
        coordinator = PlanningCoordinator(session)
        coordinator._check_cancel = lambda current, deadline=None: (_ for _ in ()).throw(RunTimedOut())
        coordinator.execute(run["id"])
    response = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
    assert response["status"] == "TIMED_OUT"
    assert response["best_solution_available"] is True
    assert response["error_detail"] == "global_run_time_limit_exceeded; validated_solution_preserved"


def test_use_current_plan_finishes_as_succeeded_early(client, catalog_ids, database, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    with database() as session:
        generate_and_persist(session)
    order = client.post("/api/v1/production-orders", json={
        "garment_model_version_id": catalog_ids["version"],
        "fabric_configuration_id": catalog_ids["fabric"],
        "cutting_table_configuration_id": catalog_ids["table"],
        "demand": [{"size_code": "M", "quantity": 3}],
    }).json()
    run = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "finish-current-incumbent"},
        json={"planner_refinement_engine": "heuristic"},
    ).json()
    with database() as session:
        row = session.scalar(select(OptimizationRunORM).where(OptimizationRunORM.id == run["id"]))
        row.use_current_plan_requested = True
        session.add(OptimizationSolutionORM(
            id="early-incumbent", optimization_run_id=run["id"], solution_hash="solution",
            fingerprint="fingerprint", rank=999, planning_status="FEASIBLE",
            planning_optimality="HEURISTIC_FEASIBLE", solution_origin="OPERATIONAL_HEURISTIC",
            metrics={}, validation_certificate={"status": "VALIDATED_PLAN"},
            explanation="BEST_VALIDATED_SO_FAR",
        ))
        session.commit()
        PlanningCoordinator(session).execute(run["id"])
    response = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
    assert response["status"] == "SUCCEEDED_EARLY"
    assert response["best_solution_available"] is True


@pytest.mark.parametrize("beam_width", [5, 10, 20, 30])
def test_documented_beam_width_benchmark_values_are_supported(beam_width):
    assert OperationalHeuristicSolver(beam_width=beam_width).beam_width == beam_width


@pytest.mark.parametrize("top_k", [10, 20, 30, 50])
def test_documented_top_k_benchmark_values_are_supported(top_k):
    assert select_geometry_top_k((), top_k).counts["geometry_top_k"] == 0
