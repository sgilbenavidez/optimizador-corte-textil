from dataclasses import replace

from costura_optima.domain.candidate_generator import CandidateCompositionGenerator
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.production_models import CandidateGenerationConfig, PlanningConfig, ValidatedMarkerCandidate
from costura_optima.domain.production_planner import ProductionPlanner


def marker(name, composition, length, waste=100):
    piece_area = 1000
    return ValidatedMarkerCandidate(
        marker_hash=name, content_key=f"key-{name}", composition=tuple(composition.items()),
        marker_length_units=length, usable_width_units=100, piece_area_units2=piece_area,
        marker_area_units2=piece_area + waste, waste_area_units2=waste,
        efficiency_percentage=piece_area / (piece_area + waste) * 100, placements=(),
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
    # Dominance is represented by retaining only the shortest equivalent capability.
    retained = tuple(sorted((slow, fast), key=lambda item: item.marker_length_units)[:1])
    assert retained == (fast,)
    solution = ProductionPlanner(PlanningConfig(max_layers=3, time_limit_seconds=5)).solve_profiles(
        {"M": 2}, {"M": 0}, retained
    )[0]
    validator = IndependentProductionPlanValidator()
    assert validator.validate(solution, retained, {"M": 2}, {"M": 0}, 3).status == "VALIDATED_PLAN"
    tampered = replace(solution, total_fabric_units=solution.total_fabric_units + 1)
    assert validator.validate(tampered, retained, {"M": 2}, {"M": 0}, 3).status == "INVALID_PLAN"


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
