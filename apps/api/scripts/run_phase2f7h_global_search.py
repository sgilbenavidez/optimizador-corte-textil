"""Phase 2F.7H: bounded ALNS-style global nesting search over M2+L1.

Starts from the unified-two-way greedy result (235.064 cm) as the initial
search state (Section 6 seed), and explores destroy/repair/acceptance moves
under a fixed seed and iteration/time budget. The legacy 229.5 cm result is
tracked as a protected external incumbent (BEST_KNOWN never regresses past
it) even though its raw placement geometry is not loaded into this run.
"""
from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.global_nesting_search import GlobalNestingSearch, make_state
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist
from costura_optima.domain.nesting_models import Placement

SEED_CHECKPOINT = Path("artifacts/phase2f7g8/unified_two_way/run1/checkpoints/piece_15.json")
PROTECTED_INCUMBENT_LENGTH_CM = 229.5
PROTECTED_INCUMBENT_EFFICIENCY_PCT = 70.534348
PIECE_AREA_CM2 = 28490.234
USABLE_WIDTH_CM = 176


def _request():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        seed_catalog(session); pattern_set, _ = generate_and_persist(session)
        service = MarkerPreviewService(session); fabric, table = service.catalog.list_fabrics()[0], service.catalog.list_tables()[0]
        captured = {}
        import costura_optima.application.services as services
        original = services.MARKER_ENGINE
        class Capture:
            def nest(self, request): captured["request"] = request; raise RuntimeError("captured")
        services.MARKER_ENGINE = Capture()
        try:
            service.generate(MarkerPreviewRequest(pattern_set_version_id=pattern_set.id, fabric_configuration_id=fabric.id,
                cutting_table_configuration_id=table.id, composition=[{"size_code": "M", "quantity": 2}, {"size_code": "L", "quantity": 1}],
                deterministic=True, seed=1, evaluation_budget=100_000, debug=True))
        except RuntimeError as exc:
            if str(exc) != "captured": raise
        finally: services.MARKER_ENGINE = original
    return captured["request"]


def _seed_placements():
    checkpoint = json.loads(SEED_CHECKPOINT.read_text(encoding="utf-8"))
    return tuple(Placement(
        row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"], row["rotation"], row["mirrored"],
        tuple(row["translation"]), tuple(tuple(point) for point in row["transformed_polygon"]),
        tuple(tuple(point) for point in row["transformed_grainline"]), tuple(row["bbox"]), row["geometry_hash"], row["sequence"],
    ) for row in checkpoint["placed_pieces"])


def efficiency(length_cm: float) -> float:
    return round(PIECE_AREA_CM2 / (USABLE_WIDTH_CM * length_cm) * 100, 6)


def main():
    parser = ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--time-budget", type=float, default=600.0)
    parser.add_argument("--candidate-budget", type=int, default=5000)
    parser.add_argument("--top-k", type=int, default=1)
    parser.add_argument("--beam-width", type=int, default=1)
    parser.add_argument("--run-tag", default="small")
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/phase2f7h"))
    args = parser.parse_args()

    request = _request()
    by_id = {item.instance_id: item for item in request.piece_instances}
    seed_placements = _seed_placements()
    initial_state = make_state(seed_placements, 0, None, None)

    output_dir = args.output_root / args.run_tag
    output_dir.mkdir(parents=True, exist_ok=True)

    def heartbeat(stats):
        best_cm = stats["best_length_units"] / 1000
        current_cm = stats["current_length_units"] / 1000
        print(f"[ALNS][{args.run_tag}] iter={stats['iteration']}/{stats['max_iterations']} "
              f"current={current_cm:.3f}cm best={best_cm:.3f}cm eff={efficiency(best_cm):.3f}% "
              f"accepted={stats['accepted_states']} new_incumbents={stats['incumbent_improvements']} "
              f"elapsed={stats['elapsed_s']}s", flush=True)

    search = GlobalNestingSearch(
        request, by_id, seed=args.seed, candidate_budget=args.candidate_budget,
        top_k=args.top_k, beam_width=args.beam_width, heartbeat=heartbeat,
    )
    result = search.run(initial_state, max_iterations=args.iterations, max_runtime_s=args.time_budget)

    best = result["best_state"]
    best_length_cm = best.marker_length_units / 1000
    best_efficiency = efficiency(best_length_cm)
    protected_best_length_cm = min(PROTECTED_INCUMBENT_LENGTH_CM, best_length_cm)
    protected_best_efficiency = PROTECTED_INCUMBENT_EFFICIENCY_PCT if protected_best_length_cm == PROTECTED_INCUMBENT_LENGTH_CM else best_efficiency

    final_validation = search.full_validator.validate(request, best.placements, request.max_length)

    orientation_counts = {0: 0, 180: 0}
    source_counts = {}
    for placement in best.placements:
        orientation_counts[placement.rotation] = orientation_counts.get(placement.rotation, 0) + 1

    summary = {
        "run_tag": args.run_tag, "seed": args.seed, "iterations": result["iterations"],
        "max_iterations": args.iterations, "runtime_s": result["runtime_s"],
        "candidate_budget": args.candidate_budget, "top_k": args.top_k, "beam_width": args.beam_width,
        "initial_length_cm": initial_state.marker_length_units / 1000,
        "best_length_cm": best_length_cm, "best_efficiency_pct": best_efficiency,
        "protected_best_length_cm": protected_best_length_cm, "protected_best_efficiency_pct": protected_best_efficiency,
        "accepted_states": result["accepted_states"],
        "unique_valid_layouts_visited": result["unique_valid_layouts_visited"],
        "incumbent_improvements": len(search.incumbent_history) - 1,
        "first_baseline_beat_iteration": result["first_baseline_beat_iteration"] or "NOT_REACHED",
        "best_layout_hash": best.layout_hash,
        "final_validation_status": final_validation.status,
        "orientation_counts": orientation_counts,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output_dir / "incumbent-history.json").write_text(json.dumps(search.incumbent_history, indent=2), encoding="utf-8")
    with (output_dir / "search-trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in search.search_trace:
            handle.write(json.dumps(row) + "\n")
    (output_dir / "best-marker.json").write_text(json.dumps({
        "layout_hash": best.layout_hash, "marker_length_cm": best_length_cm,
        "placements": [{"piece_instance_id": p.piece_instance_id, "translation": p.translation,
                        "rotation": p.rotation, "geometry_hash": p.geometry_hash} for p in best.placements],
    }, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
