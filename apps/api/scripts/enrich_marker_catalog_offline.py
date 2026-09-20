"""Phase 2F.8.1 Section 12: offline/deep catalog enrichment.

Separate from runtime search (Section 12's explicit "Separate two
concepts"): this script is never called from the worker. Given a
composition, it runs a deeper, multi-seed `GlobalNestingSearch` (reused
unchanged -- no second geometry engine, per Section 12's explicit
instruction), validates the winning seed's result, and persists it into the
real catalog via the same `PlanningCoordinator._persist_marker_artifact`
path the import script and the 2F.8 runtime refinement path both already
use, so the planner remains unaware of a marker's origin (Section 18).
"""
from __future__ import annotations

import json
from argparse import ArgumentParser
from math import ceil
from pathlib import Path

from costura_optima.application.planning_coordinator import (
    PlanningCoordinator, _marker_from_payload, _serialize_placement_for_payload,
)
from costura_optima.application.services import MARKER_ENGINE, build_marker_request
from costura_optima.domain.global_nesting_search import GlobalNestingSearch, make_state
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash
from costura_optima.domain.marker_validator import IndependentMarkerValidator


def enrich(session, pattern_set, fabric, table, composition: list[tuple[str, int]], *,
           seeds: tuple[int, ...], max_iterations: int, candidate_budget: int, max_runtime_s: float,
           piece_time_budget_ms: int) -> dict:
    request = build_marker_request(pattern_set, fabric, table, composition, deterministic=True, seed=seeds[0],
                                    evaluation_budget=100_000)
    by_id = {item.instance_id: item for item in request.piece_instances}
    cold_result = MARKER_ENGINE.nest(request)
    if cold_result.status != "VALIDATED_FEASIBLE" or not cold_result.placements:
        return {"composition": composition, "status": "COLD_START_FAILED"}

    full_validator = IndependentMarkerValidator()
    seed_outcomes = []
    for seed in seeds:
        initial_state = make_state(cold_result.placements, 0, None, None)
        search = GlobalNestingSearch(request, by_id, seed=seed, candidate_budget=candidate_budget, top_k=1, beam_width=1,
                                      piece_time_budget_ms=piece_time_budget_ms)
        result = search.run(initial_state, max_iterations=max_iterations, max_runtime_s=max_runtime_s)
        best = result["best_state"]
        report = full_validator.validate(request, best.placements, request.max_length)
        if report.status == "VALIDATED":
            seed_outcomes.append({"seed": seed, "length_units": best.marker_length_units, "placements": best.placements})

    if not seed_outcomes:
        return {"composition": composition, "status": "SEARCH_FAILED"}

    best_outcome = min(seed_outcomes, key=lambda item: item["length_units"])
    units = pattern_set.geometry_units_per_cm
    kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())
    piece_area_units2 = sum(kernel.area_units2(item.piece.cut_polygon) for item in request.piece_instances)
    marker_area_units2 = request.usable_width * best_outcome["length_units"]
    waste_area_units2 = marker_area_units2 - piece_area_units2
    efficiency_pct = round(piece_area_units2 / marker_area_units2 * 100, 6) if marker_area_units2 else 0.0
    signature = {"pattern": pattern_set.content_hash, "fabric": fabric.content_hash, "table": table.content_hash,
                 "composition": composition, "engine": "offline-deep-enrichment-v1", "seeds": list(seeds),
                 "max_iterations": max_iterations}
    content_key = canonical_json_hash(signature)
    payload = {
        "geometry_units_per_cm": units, "marker_length_cm": best_outcome["length_units"] / units,
        "usable_width_cm": request.usable_width / units, "piece_area_total_cm2": piece_area_units2 / (units * units),
        "marker_area_cm2": marker_area_units2 / (units * units), "waste_area_cm2": waste_area_units2 / (units * units),
        "efficiency_percentage": efficiency_pct,
        "placements": [_serialize_placement_for_payload(item, units) for item in best_outcome["placements"]],
        "validation": {"status": "VALIDATED", "checks": {}, "errors": [], "pair_checks": 0},
        "algorithm_version": "offline-deep-enrichment-v1", "search_status": "DEEP_ENRICHED_FEASIBLE",
        "input_hash": content_key, "result_hash": make_state(best_outcome["placements"], 0, None, None).layout_hash,
        "lower_bound_length_cm": ceil(piece_area_units2 / request.usable_width) / units,
    }
    from types import SimpleNamespace
    fake_order = SimpleNamespace(catalog_snapshot={"fabric_configuration": {"content_hash": fabric.content_hash},
                                                    "cutting_table_configuration": {"content_hash": table.content_hash}})
    coordinator = PlanningCoordinator(session)
    artifact, cache_hit = coordinator._persist_marker_artifact(content_key, pattern_set, fake_order, dict(composition), payload)
    candidate = _marker_from_payload(artifact)
    return {
        "composition": composition, "status": "PERSISTED" if not cache_hit else "ALREADY_PRESENT",
        "marker_hash": candidate.marker_hash, "best_seed": best_outcome["seed"],
        "seed_lengths_cm": sorted(o["length_units"] / units for o in seed_outcomes),
        "marker_length_cm": candidate.marker_length_units / units, "efficiency_percentage": candidate.efficiency_percentage,
    }


def main():
    parser = ArgumentParser()
    parser.add_argument("--composition", required=True, help='e.g. "M:2,L:1"')
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2])
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--candidate-budget", type=int, default=2000)
    parser.add_argument("--max-runtime-s", type=float, default=300.0)
    parser.add_argument("--piece-time-budget-ms", type=int, default=10_000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/phase2f8_1/enrichment-result.json"))
    args = parser.parse_args()

    composition = [(part.split(":")[0], int(part.split(":")[1])) for part in args.composition.split(",")]

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from costura_optima.infrastructure.database import Base
    from costura_optima.infrastructure.repositories import CatalogRepository
    from costura_optima.infrastructure.seed_data import seed_catalog
    from costura_optima.patterns.persistence import generate_and_persist

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed_catalog(session)
        pattern_set, _ = generate_and_persist(session)
        catalog = CatalogRepository(session)
        fabric, table = catalog.list_fabrics()[0], catalog.list_tables()[0]
        result = enrich(session, pattern_set, fabric, table, composition, seeds=tuple(args.seeds),
                         max_iterations=args.max_iterations, candidate_budget=args.candidate_budget,
                         max_runtime_s=args.max_runtime_s, piece_time_budget_ms=args.piece_time_budget_ms)
        session.commit()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
