"""Phase 2F.7H-2: exact-production feasibility for the 216-garment benchmark.

Assembles the VALIDATED CompositionResults from
run_phase2f7h2_composition_search.py's compositions-summary.json into a
ValidatedMarkerCandidate catalog and asks the existing, already-tested
ProductionPlanner (production_planner.py) to solve for EXACT production
(maximum_overproduction = 0 for every size) against the 216-garment
benchmark demand -- the same exact-mode code path
test_production_planner.py::test_allow_overproduction_false_can_be_infeasible
already exercises. No new CP-SAT code: this only builds the inputs and
audits the output with the existing IndependentProductionPlanValidator.
"""
from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from costura_optima.domain.production_models import PlanningConfig, ValidatedMarkerCandidate
from costura_optima.domain.production_planner import ProductionPlanner
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator

DEMAND_216 = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
MAX_LAYERS = 55  # must cover the largest single-size layer count in the benchmark (S3xXXL5 uses 5, XXL alone could need up to 55)
USABLE_TABLE_LENGTH_UNITS = 700_000


def _catalog_from_summary(rows: list[dict]) -> tuple[ValidatedMarkerCandidate, ...]:
    catalog = []
    for row in rows:
        if not row.get("validated") or row.get("status") != "VALIDATED":
            continue
        marker_length_units = round(row["best_marker_length_cm"] * 1000)
        usable_width_units = row["usable_width_units"]
        piece_area_units2 = row["piece_area_units2"]
        marker_area_units2 = usable_width_units * marker_length_units
        waste_area_units2 = marker_area_units2 - piece_area_units2
        best_path = Path(row["_artifact_dir"]) / "best-marker.json"
        placements = ()
        if best_path.exists():
            placements = tuple(json.loads(best_path.read_text(encoding="utf-8"))["placements"])
        catalog.append(ValidatedMarkerCandidate(
            marker_hash=row["marker_hash"], content_key=f"composition-{row['composition_id']}-{row['marker_hash']}",
            composition=tuple(sorted(row["size_counts"].items())), marker_length_units=marker_length_units,
            usable_width_units=usable_width_units, piece_area_units2=piece_area_units2,
            marker_area_units2=marker_area_units2, waste_area_units2=waste_area_units2,
            efficiency_percentage=row["best_efficiency_pct"], placements=placements,
            validation_certificate={"status": "VALIDATED", "source": "GlobalNestingSearch+IndependentMarkerValidator"},
            geometry_engine_version="2F.7H-2-composition-explorer", marker_search_status="FEASIBLE_NOT_PROVEN_BEST",
            input_hash=row["marker_hash"], lower_bound_length_units=row["theoretical_length_100_units"],
            origin="COMPOSITION_EXPLORER_2F7H2",
        ))
    return tuple(catalog)


def main():
    parser = ArgumentParser()
    parser.add_argument("--summary", type=Path, default=Path("artifacts/phase2f7h2/compositions-summary.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/phase2f7h2/exact-production-216.json"))
    parser.add_argument("--time-limit", type=float, default=60.0)
    args = parser.parse_args()

    rows = json.loads(args.summary.read_text(encoding="utf-8"))
    for row in rows:
        row.setdefault("_artifact_dir", str(args.summary.parent / "+".join(
            f"{size}{quantity}" for size, quantity in sorted(row["size_counts"].items(),
                                                              key=lambda item: ("XS", "S", "M", "L", "XL", "XXL", "XXXL").index(item[0])))))
    catalog = _catalog_from_summary(rows)

    zero_overproduction = {size: 0 for size in DEMAND_216}
    planner = ProductionPlanner(PlanningConfig(max_layers=MAX_LAYERS, time_limit_seconds=args.time_limit, deterministic=True, seed=1))
    solutions = planner.solve_profiles(DEMAND_216, zero_overproduction, catalog)

    feasible = [solution for solution in solutions if solution.planning_status != "INFEASIBLE" and solution.spreads]
    chosen = min(feasible, key=lambda solution: (
        solution.spread_count, solution.marker_design_count, solution.marker_change_count,
        solution.total_fabric_units, solution.total_overproduction,
    )) if feasible else None

    audit_evidence = {
        "order_hash": "phase2f7h2-216-benchmark", "input_hash": "phase2f7h2-216-benchmark-input",
        "pattern_hash": "phase2f7h2-pattern", "fabric_snapshot": {"usable_width_units": catalog[0].usable_width_units if catalog else 0},
        "table_snapshot": {"usable_length_units": USABLE_TABLE_LENGTH_UNITS}, "policy": {"maximum_overproduction": zero_overproduction},
        "candidate_generation_config": {"source": "composition_explorer"}, "geometry_config": {"engine": "GlobalNestingSearch+legacy_cold_start"},
        "geometry_version": "2F.7H-2-composition-explorer", "planner_config": {"max_layers": MAX_LAYERS, "time_limit_seconds": args.time_limit},
        "planner_version": "production_planner-2F.7H-2", "seed": 1,
        "solver_status": chosen.planning_optimality if chosen else "NO_FEASIBLE_SOLUTION",
        "objective_stages": list(chosen.objective_stages) if chosen else [],
        "validation_certificate": [marker.validation_certificate for marker in catalog],
        "marker_hashes": sorted(marker.marker_hash for marker in catalog),
    }
    validator = IndependentProductionPlanValidator()
    validation = validator.validate(chosen, catalog, DEMAND_216, zero_overproduction, MAX_LAYERS,
                                     USABLE_TABLE_LENGTH_UNITS, audit_evidence) if chosen else None

    output = {
        "catalog_marker_hashes": sorted(marker.marker_hash for marker in catalog),
        "catalog_size": len(catalog),
        "exact_solution_found": chosen is not None,
        "planning_status": chosen.planning_status if chosen else "INFEASIBLE",
        "spread_count": chosen.spread_count if chosen else None,
        "marker_design_count": chosen.marker_design_count if chosen else None,
        "produced_by_size": chosen.produced_by_size if chosen else None,
        "overproduction_by_size": chosen.overproduction_by_size if chosen else None,
        "total_overproduction": chosen.total_overproduction if chosen else None,
        "total_fabric_units": chosen.total_fabric_units if chosen else None,
        "global_efficiency_percentage": chosen.global_efficiency_percentage if chosen else None,
        "shortage": {size: max(0, DEMAND_216[size] - (chosen.produced_by_size.get(size, 0) if chosen else 0)) for size in DEMAND_216},
        "independent_validation_status": validation.status if validation else "NOT_RUN",
        "independent_validation_checks": validation.checks if validation else None,
        "independent_validation_errors": list(validation.errors) if validation else None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
