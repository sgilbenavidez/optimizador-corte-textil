from dataclasses import replace

from costura_optima.application.planning_coordinator import remove_dominated
from costura_optima.domain.candidate_generator import CandidateCompositionGenerator, select_balanced_budget
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.production_models import CandidateGenerationConfig, PlanningConfig, ValidatedMarkerCandidate
from costura_optima.domain.production_planner import ProductionPlanner


def marker(name, composition, length, waste=100):
    piece_area = 1000
    return ValidatedMarkerCandidate(
        marker_hash=name, content_key=f"key-{name}", composition=tuple(composition.items()),
        marker_length_units=length, usable_width_units=100, piece_area_units2=piece_area,
        marker_area_units2=piece_area + waste, waste_area_units2=waste,
        efficiency_percentage=piece_area / (piece_area + waste) * 100,
        placements=tuple({"piece": index} for index in range(sum(composition.values()) * 5)),
        validation_certificate={"status": "VALIDATED", "checks": {}}, geometry_engine_version="test",
        marker_search_status="FEASIBLE_NOT_PROVEN_BEST", input_hash=f"input-{name}", lower_bound_length_units=1,
    )


def test_lexicographic_profiles_choose_expected_small_optima():
    markers = (
        marker("single-s", {"S": 1}, 100),
        marker("single-m", {"M": 1}, 100),
        marker("pair", {"S": 1, "M": 1}, 150),
    )
    planner = ProductionPlanner(PlanningConfig(max_layers=2, time_limit_seconds=5))
    solutions = planner.solve_profiles({"S": 2, "M": 2}, {"S": 0, "M": 0}, markers)
    assert solutions
    for solution in solutions:
        assert solution.planning_optimality == "SOLVER_OPTIMAL"
        assert solution.produced_by_size == {"M": 2, "S": 2}
        assert solution.total_overproduction == 0
    min_fabric = next(solution for solution in solutions if "MIN_FABRIC" in solution.profiles)
    assert min_fabric.total_fabric_units == 300
    assert min_fabric.spread_count == 1


def test_profile_priority_differs_between_fabric_and_spreads():
    markers = (
        marker("single", {"M": 1}, 50),
        marker("double", {"M": 2}, 90),
    )
    planner = ProductionPlanner(PlanningConfig(max_layers=2, time_limit_seconds=5))
    solutions = planner.solve_profiles({"M": 3}, {"M": 1}, markers)
    assert all(solution.produced_by_size["M"] >= 3 for solution in solutions)
    assert all(solution.overproduction_by_size["M"] <= 1 for solution in solutions)


def test_solution_hash_excludes_solver_timing_and_is_reproducible():
    catalog = (marker("single", {"M": 1}, 50), marker("double", {"M": 2}, 90))
    planner = ProductionPlanner(PlanningConfig(max_layers=2, time_limit_seconds=5, deterministic=True, seed=7))
    first = planner.solve_profiles({"M": 3}, {"M": 1}, catalog)
    second = planner.solve_profiles({"M": 3}, {"M": 1}, catalog)
    assert [item.fingerprint for item in first] == [item.fingerprint for item in second]
    assert [item.solution_hash for item in first] == [item.solution_hash for item in second]


def test_allow_overproduction_false_can_be_infeasible():
    planner = ProductionPlanner(PlanningConfig(max_layers=1, time_limit_seconds=5))
    result = planner.solve_profiles({"M": 1}, {"M": 0}, (marker("double", {"M": 2}, 90),))[0]
    assert result.planning_status == "INFEASIBLE"


def test_dominance_fixture_and_global_validator_reject_tampering():
    slow = marker("slow", {"M": 1}, 120)
    fast = marker("fast", {"M": 1}, 100)
    retained = remove_dominated((slow, fast))
    assert retained == (fast,)
    solution = ProductionPlanner(PlanningConfig(max_layers=3, time_limit_seconds=5)).solve_profiles(
        {"M": 2}, {"M": 0}, retained
    )[0]
    validator = IndependentProductionPlanValidator()
    evidence = complete_audit(retained)
    assert validator.validate(solution, retained, {"M": 2}, {"M": 0}, 3, 100, evidence).status == "VALIDATED_PLAN"
    tampered = replace(solution, total_fabric_units=solution.total_fabric_units + 1)
    assert validator.validate(tampered, retained, {"M": 2}, {"M": 0}, 3, 100, evidence).status == "INVALID_PLAN"


def complete_audit(markers):
    return {
        "order_hash": "order", "input_hash": "input", "pattern_hash": "pattern",
        "fabric_snapshot": {}, "table_snapshot": {}, "policy": {}, "candidate_generation_config": {},
        "geometry_config": {}, "geometry_version": "geometry", "planner_config": {},
        "planner_version": "planner", "seed": 1, "solver_status": "OPTIMAL", "objective_stages": [],
        "validation_certificate": [], "marker_hashes": sorted(item.marker_hash for item in markers),
    }


def test_remove_dominated_uses_real_pareto_capability_and_keeps_tradeoff():
    longer = marker("longer", {"M": 1, "L": 1}, 200, 200)
    winner = marker("winner", {"M": 1, "L": 1}, 180, 180)
    tradeoff = marker("tradeoff", {"M": 1, "L": 1}, 170, 300)
    retained = remove_dominated((longer, winner, tradeoff))
    assert longer not in retained
    assert winner in retained and tradeoff in retained


def test_validator_rejects_marker_beyond_table_and_incomplete_audit():
    catalog = (marker("too-long", {"M": 1}, 120),)
    solution = ProductionPlanner(PlanningConfig(max_layers=3, time_limit_seconds=5)).solve_profiles(
        {"M": 2}, {"M": 0}, catalog
    )[0]
    validator = IndependentProductionPlanValidator()
    table_report = validator.validate(solution, catalog, {"M": 2}, {"M": 0}, 3, 119, complete_audit(catalog))
    assert table_report.status == "INVALID_PLAN"
    assert table_report.checks["marker_length_within_table"] is False
    audit_report = validator.validate(solution, catalog, {"M": 2}, {"M": 0}, 3, 120, {})
    assert audit_report.status == "INVALID_PLAN"
    assert audit_report.checks["audit_complete"] is False


def test_balanced_budget_reserves_multi_size_candidates():
    generator = CandidateCompositionGenerator(CandidateGenerationConfig(max_candidate_compositions=24))
    result = generator.generate(
        demand={"S": 3, "M": 20, "L": 10, "XL": 12, "XXL": 30},
        maximum_overproduction={"S": 2, "M": 2, "L": 2, "XL": 2, "XXL": 2},
        size_area_units2={size: 100 for size in ("S", "M", "L", "XL", "XXL")},
        size_piece_fits={size: True for size in ("S", "M", "L", "XL", "XXL")},
        usable_width_units=100, max_marker_length_units=100,
    )
    selected, outside = select_balanced_budget(result.candidates, 10)
    assert any(len(item.composition) > 1 for item in selected)
    assert outside


def test_material_fingerprint_groups_layer_allocations_and_fallback_origin():
    catalog = (replace(marker("single", {"M": 1}, 50), origin="SINGLE_SIZE_FALLBACK"),)
    solutions = ProductionPlanner(PlanningConfig(max_layers=30, time_limit_seconds=5)).solve_profiles(
        {"M": 100}, {"M": 3}, catalog
    )
    assert len(solutions) == 1
    assert {"MAX_ORDER_PER_CUT", "MIN_FABRIC", "MIN_SPREADS", "BALANCED", "CONSOLIDATED_PRODUCTION"} <= set(solutions[0].profiles)
    assert solutions[0].solution_origin == "FALLBACK_SINGLE_SIZE"
    assert all({"stage", "objective", "status", "elapsed_ms", "fixed_from_previous_stage"} <= set(stage)
               for stage in solutions[0].objective_stages)


def test_candidate_generator_prunes_area_width_and_demand():
    generator = CandidateCompositionGenerator(CandidateGenerationConfig(max_candidate_compositions=20))
    result = generator.generate(
        demand={"S": 1, "M": 3}, maximum_overproduction={"S": 0, "M": 1},
        size_area_units2={"S": 1000, "M": 1000}, size_piece_fits={"S": False, "M": True},
        usable_width_units=100, max_marker_length_units=25,
    )
    assert all("S" not in dict(item.composition) for item in result.candidates)
    assert any(item["reason"] == "individual_piece_width" for item in result.pruned)
    assert all(item.area_lower_bound_units <= 25 for item in result.candidates)


def test_ratio_directed_generation_reaches_large_multi_size_markers_without_enumeration():
    sizes = ("XS", "S", "M", "L", "XL", "XXL")
    generator = CandidateCompositionGenerator(CandidateGenerationConfig(
        max_garments_per_marker=12, max_distinct_sizes_per_marker=5, max_candidate_compositions=48,
    ))
    result = generator.generate(
        demand={"XS": 20, "S": 10, "M": 30, "L": 20, "XL": 14, "XXL": 3},
        maximum_overproduction={size: 2 for size in sizes},
        size_area_units2={size: 100 for size in sizes},
        size_piece_fits={size: True for size in sizes},
        usable_width_units=100, max_marker_length_units=10_000,
    )
    consolidated = [item for item in result.candidates if item.origin.startswith(("PROPORTIONAL", "AREA_DENSITY"))]
    assert consolidated
    assert any(sum(quantity for _, quantity in item.composition) == 12 for item in consolidated)
    assert any(len(item.composition) >= 4 for item in consolidated)
    assert len(result.candidates) <= 48


def test_consolidated_profile_exposes_fabric_tradeoff_and_fewer_marker_designs():
    catalog = (
        marker("single-s", {"S": 1}, 50), marker("single-m", {"M": 1}, 50),
        marker("pair", {"S": 1, "M": 1}, 120),
    )
    solutions = ProductionPlanner(PlanningConfig(max_layers=2, time_limit_seconds=5)).solve_profiles(
        {"S": 2, "M": 2}, {"S": 0, "M": 0}, catalog,
    )
    fabric = next(item for item in solutions if "MIN_FABRIC" in item.profiles)
    consolidated = next(item for item in solutions if "CONSOLIDATED_MARKERS" in item.profiles)
    assert fabric.total_fabric_units < consolidated.total_fabric_units
    assert fabric.marker_design_count == 2
    assert consolidated.marker_design_count == 1
    assert consolidated.max_pieces_per_marker == 10
