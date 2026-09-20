from dataclasses import replace

from costura_optima.domain.candidate_generator import (
    CandidateCompositionGenerator, candidate_category, select_balanced_budget, useful_coverage,
)
from costura_optima.domain.production_models import (
    CandidateGenerationConfig, PlannedSpread, PlanningConfig, ValidatedMarkerCandidate,
)
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.production_planner import PROFILES, ProductionPlanner


ORDER_97 = {"XS": 20, "S": 10, "M": 30, "L": 20, "XL": 14, "XXL": 3, "XXXL": 0}


def marker(name, composition, length=100, origin="CATALOG", candidate_layers=()):
    piece_count = sum(composition.values()) * 5
    return ValidatedMarkerCandidate(
        marker_hash=name, content_key=name, composition=tuple(composition.items()),
        marker_length_units=length, usable_width_units=176_000, piece_area_units2=1000,
        marker_area_units2=1100, waste_area_units2=100, efficiency_percentage=90.909091,
        placements=tuple({"piece": i} for i in range(piece_count)),
        validation_certificate={"status": "VALIDATED"}, geometry_engine_version="fixture",
        marker_search_status="VALIDATED_FEASIBLE", input_hash=name, lower_bound_length_units=1,
        origin=origin, candidate_layers=candidate_layers,
    )


def generation(demand=ORDER_97, budget=48):
    return CandidateCompositionGenerator(CandidateGenerationConfig(
        max_garments_per_marker=15, max_distinct_sizes_per_marker=5,
        max_candidate_compositions=budget,
    )).generate(
        demand, {size: 2 for size in demand}, {size: 100 for size in demand},
        {size: True for size in demand}, 176_000, 700_000, max_layers=30,
    )


def test_coverage_does_not_count_overproduction():
    assert useful_coverage({"M": 3}, (("M", 2),), 3) == 3


def test_demand_ratio_and_layer_aware_candidates_derive_from_order():
    result = generation()
    ratio = [item for item in result.candidates if "DEMAND_RATIO" in item.origin]
    assert ratio and any(len(item.composition) >= 4 for item in ratio)
    assert all(item.candidate_layers and max(item.candidate_layers) <= 30 for item in ratio)
    assert any({9, 10, 11} & set(item.candidate_layers) for item in ratio)
    assert {3, 4, 5, 6, 8, 10, 12, 15} <= {
        sum(quantity for _, quantity in item.composition)
        for item in result.candidates if "DEMAND_RATIO" in item.origin
    }


def test_multi_size_budget_is_reserved_and_single_size_is_fallback():
    result = generation(budget=24)
    selected, _ = select_balanced_budget(result.candidates, 12)
    categories = {candidate_category(item.origin, item.composition) for item in selected}
    assert "DEMAND_RATIO" in categories
    assert any(len(item.composition) > 1 for item in selected)
    assert {size for item in selected if item.origin == "SINGLE_SIZE_FALLBACK" for size, _ in item.composition} == {
        size for size, quantity in ORDER_97.items() if quantity
    }


def test_primary_spread_maximizes_known_fixture_and_residual_is_non_negative():
    catalog = (
        marker("pair", {"S": 1, "M": 1}, candidate_layers=(1, 2)),
        marker("single-s", {"S": 1}), marker("single-m", {"M": 1}),
    )
    solution = next(item for item in ProductionPlanner(PlanningConfig(max_layers=30, time_limit_seconds=5)).solve_profiles(
        {"S": 2, "M": 2}, {"S": 0, "M": 0}, catalog,
    ) if "MAX_ORDER_PER_CUT" in item.profiles)
    assert solution.spreads[0].marker_hash == "pair"
    assert solution.primary_spread_covered_garments == 4
    assert solution.primary_spread_coverage_percentage == 100
    assert solution.spreads[0].remaining_demand_after == {"S": 0, "M": 0}


def test_distinct_marker_boolean_and_changeovers_are_lexicographic_stages():
    assert PROFILES["MAX_ORDER_PER_CUT"] == (
        "spreads", "primary_coverage", "marker_designs", "marker_changeovers",
        "fabric", "total_overproduction", "waste",
    )
    solution = next(item for item in ProductionPlanner(PlanningConfig(max_layers=2, time_limit_seconds=5)).solve_profiles(
        {"S": 2, "M": 2}, {"S": 0, "M": 0},
        (marker("pair", {"S": 1, "M": 1}), marker("s", {"S": 1}), marker("m", {"M": 1})),
    ) if "MAX_ORDER_PER_CUT" in item.profiles)
    assert solution.marker_design_count == 1 and solution.marker_change_count == 0


def test_recommended_profile_never_regresses_known_operational_incumbent():
    solutions = ProductionPlanner(PlanningConfig(max_layers=30, time_limit_seconds=.2)).solve_profiles(
        {"S": 10, "M": 30}, {"S": 0, "M": 0},
        (marker("pair", {"S": 1, "M": 3}, candidate_layers=(10,)),
         marker("s", {"S": 1}), marker("m", {"M": 1})),
    )
    recommended = next(item for item in solutions if "MAX_ORDER_PER_CUT" in item.profiles)
    assert recommended.spread_count <= min(item.spread_count for item in solutions if item.spreads)


def test_same_marker_layers_compact_and_physical_spreads_count_operations():
    planner = ProductionPlanner(PlanningConfig(max_layers=30))
    catalog = (marker("m", {"M": 1}),)
    rows = tuple(PlannedSpread(
        marker_hash="m", composition=(("M", 1),), layers=layers, repeats=1,
        marker_length_units=100, fabric_consumption_units=100 * layers,
        production_by_size={"M": layers}, marker_efficiency_percentage=90,
        marker_search_status="VALIDATED_FEASIBLE", spread_hash=str(layers),
    ) for layers in (2, 4, 12, 15))
    compact = planner._compact_and_sequence(rows, catalog, {"M": 33})
    assert [item.layers for item in compact] == [30, 3]
    assert sum(item.repeats for item in compact) == 2


def test_order_97_fixture_has_no_shortage_max_layers_30_and_large_marker():
    catalog = (
        marker("primary", {"XS": 2, "S": 1, "M": 3, "L": 2, "XL": 1}, 600_000, candidate_layers=(9, 10, 11)),
        marker("m-xl", {"M": 2, "XL": 1}, 250_000), marker("xl", {"XL": 1}, 80_000),
        marker("xxl", {"XXL": 1}, 80_000),
    )
    solution = next(item for item in ProductionPlanner(PlanningConfig(max_layers=30, time_limit_seconds=8)).solve_profiles(
        ORDER_97, {size: 2 for size in ORDER_97}, catalog,
    ) if "MAX_ORDER_PER_CUT" in item.profiles)
    assert all(solution.produced_by_size[size] >= demand for size, demand in ORDER_97.items())
    assert all(spread.layers <= 30 for spread in solution.spreads)
    assert all(spread.marker_length_units <= 700_000 for spread in solution.spreads)
    assert solution.max_pieces_per_marker > 15


def test_validator_recalculates_new_coverage_metrics():
    catalog = (marker("m", {"M": 1}, 100),)
    solution = ProductionPlanner(PlanningConfig(max_layers=30, time_limit_seconds=5)).solve_profiles(
        {"M": 3}, {"M": 0}, catalog,
    )[0]
    evidence = {"order_hash": "o", "input_hash": "i", "pattern_hash": "p", "fabric_snapshot": {},
                "table_snapshot": {}, "policy": {}, "candidate_generation_config": {}, "geometry_config": {},
                "geometry_version": "g", "planner_config": {}, "planner_version": "p", "seed": 1,
                "marker_hashes": ["m"], "solver_status": "OPTIMAL", "objective_stages": [],
                "validation_certificate": []}
    report = IndependentProductionPlanValidator().validate(
        solution, catalog, {"M": 3}, {"M": 0}, 30, 700_000, evidence,
    )
    assert report.status == "VALIDATED_PLAN"
    assert report.recomputed["primary_spread_coverage_percentage"] == 100
    tampered = replace(solution, primary_spread_covered_garments=2)
    assert IndependentProductionPlanValidator().validate(
        tampered, catalog, {"M": 3}, {"M": 0}, 30, 700_000, evidence,
    ).status == "INVALID_PLAN"
