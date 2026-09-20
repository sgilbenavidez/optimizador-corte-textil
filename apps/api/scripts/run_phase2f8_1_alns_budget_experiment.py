"""Phase 2F.8.1 Sections 14-16: controlled ALNS budget experiment, standalone
(never wired into the worker -- Section 17 explicitly forbids automatic
deep-search inside the worker pending evidence).

Takes the 3 markers with the highest fabric contribution in the live cold
216-benchmark's actual winning plan (XXL x1, XL x1, M x1 -- derived by
get_cold_run_spread_compositions.py, not assumed), and runs each through
GlobalNestingSearch (reused unchanged) at 3 budget tiers, measuring
time-to-first-improvement and iterations-to-first-improvement -- answering
"why didn't 2F.8's runtime refinement improve anything" with measurement.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from costura_optima.application.services import MARKER_ENGINE, build_marker_request
from costura_optima.domain.global_nesting_search import GlobalNestingSearch, make_state
from costura_optima.domain.marker_validator import IndependentMarkerValidator

TARGET_COMPOSITIONS = {
    "XXL1": [("XXL", 1)],
    "XL1": [("XL", 1)],
    "M1": [("M", 1)],
}

TIERS = {
    "runtime": {"seeds": (1,), "max_iterations": 3, "candidate_budget": 1000, "piece_time_budget_ms": 3_000, "max_runtime_s": 60.0},
    "medium": {"seeds": (1, 2), "max_iterations": 6, "candidate_budget": 2000, "piece_time_budget_ms": 5_000, "max_runtime_s": 120.0},
    "deep": {"seeds": (1, 2, 3), "max_iterations": 6, "candidate_budget": 3000, "piece_time_budget_ms": 5_000, "max_runtime_s": 120.0},
}


def run_tier(request, by_id, initial_state, tier_name, tier_config) -> dict:
    validator = IndependentMarkerValidator()
    seed_results = []
    for seed in tier_config["seeds"]:
        search = GlobalNestingSearch(
            request, by_id, seed=seed, candidate_budget=tier_config["candidate_budget"], top_k=1, beam_width=1,
            piece_time_budget_ms=tier_config["piece_time_budget_ms"],
        )
        started = perf_counter()
        result = search.run(initial_state, max_iterations=tier_config["max_iterations"], max_runtime_s=tier_config["max_runtime_s"])
        elapsed_s = perf_counter() - started
        best = result["best_state"]
        report = validator.validate(request, best.placements, request.max_length)
        first_improvement = next((row for row in search.incumbent_history if row["iteration"] > 0), None)
        seed_results.append({
            "seed": seed, "iterations_run": result["iterations"], "runtime_s": round(elapsed_s, 3),
            "best_length_cm": best.marker_length_units / 1000, "validated": report.status == "VALIDATED",
            "improved_from_initial": best.marker_length_units < initial_state.marker_length_units,
            "time_to_first_improvement_s": (
                round(first_improvement["runtime_s"], 3) if first_improvement else None
            ),
            "iterations_to_first_improvement": (
                first_improvement["iteration"] if first_improvement else None
            ),
        })
    best_seed_result = min(seed_results, key=lambda item: item["best_length_cm"])
    return {"tier": tier_name, "config": tier_config, "seed_results": seed_results, "best": best_seed_result}


def experiment_for_composition(tag: str, composition: list[tuple[str, int]]) -> dict:
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
        request = build_marker_request(pattern_set, fabric, table, composition, deterministic=True, seed=1, evaluation_budget=100_000)

    by_id = {item.instance_id: item for item in request.piece_instances}
    cold_result = MARKER_ENGINE.nest(request)
    if cold_result.status != "VALIDATED_FEASIBLE":
        return {"composition_tag": tag, "status": "COLD_START_FAILED"}
    initial_state = make_state(cold_result.placements, 0, None, None)

    tiers_output = {tier_name: run_tier(request, by_id, initial_state, tier_name, tier_config)
                     for tier_name, tier_config in TIERS.items()}
    return {
        "composition_tag": tag, "composition": composition, "status": "OK",
        "initial_length_cm": initial_state.marker_length_units / 1000,
        "tiers": tiers_output,
    }


def main():
    results = {tag: experiment_for_composition(tag, composition) for tag, composition in TARGET_COMPOSITIONS.items()}
    output_dir = Path("artifacts/phase2f8_1")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "alns-budget-experiment.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
