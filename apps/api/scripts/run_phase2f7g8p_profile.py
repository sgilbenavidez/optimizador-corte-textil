"""Phase 2F.7G-8P microbenchmark: profile M_SLEEVE_004 UNIFIED 0+180 in isolation.

Resumes from the frozen checkpoint-11 prefix (same contract as 2F.7G-8) and
calls DeterministicCompletionRunner._evaluate_piece exactly once, for
M_SLEEVE_004 only, under UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE / TWO_WAY.  Does
not continue to the rest of the marker.  Writes a fine-grained timing/counter
profile plus an optional cProfile dump with top hot functions by cumulative time.
"""
from __future__ import annotations

import cProfile
import io
import json
import pstats
import sys
from argparse import ArgumentParser
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.completion_runner import DeterministicCompletionRunner
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist
from costura_optima.domain.nesting_models import Placement


RESUME_CHECKPOINT = Path("artifacts/phase2f7g7/marker-0-only-run/checkpoints/piece_11.json")
FROZEN_TRACE = Path("artifacts/phase2f7g4/M2_L1-ZERO_ONLY.json")
TARGET_PIECE = "M_SLEEVE_004"
TARGET_SEQUENCE = 12


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


def _frozen_prefix():
    checkpoint = json.loads(RESUME_CHECKPOINT.read_text(encoding="utf-8"))
    return tuple(Placement(
        row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"], row["rotation"], row["mirrored"],
        tuple(row["translation"]), tuple(tuple(point) for point in row["transformed_polygon"]),
        tuple(tuple(point) for point in row["transformed_grainline"]), tuple(row["bbox"]), row["geometry_hash"], row["sequence"],
    ) for row in checkpoint["placed_pieces"])


def _piece_order():
    trace = json.loads(FROZEN_TRACE.read_text(encoding="utf-8"))["candidate_trace"]
    return tuple(row["piece_instance_id"] for row in trace)


def run_once(candidate_mode: str, orientation_policy: str, candidate_budget: int):
    request = _request()
    instances = {item.instance_id: item for item in request.piece_instances}
    frozen = _frozen_prefix()
    order = _piece_order()
    def heartbeat(stats):
        print(f"[{stats['piece']}][{stats['orientation']}] "
              f"processed={stats['processed']}/{stats['total']} valid={stats['valid']} "
              f"elapsed={stats['elapsed_s']}s best_length={stats['best_length']} "
              f"nfp_cache_hit={stats['nfp_cache_hit_rate_pct']}%", flush=True)

    runner = DeterministicCompletionRunner(
        request, frozen, Path("artifacts/phase2f7g8p/_scratch_runner"), candidate_budget=candidate_budget, recovery_budget=65,
        piece_time_budget_ms=600_000, piece_order=order, candidate_mode=candidate_mode,
        orientation_policy=orientation_policy, depth2_diagnostics=False, heartbeat=heartbeat,
    )
    instance = instances[TARGET_PIECE]
    rotations = runner._effective_rotations(instance)
    started = perf_counter()
    result = runner._evaluate_piece(instance, list(frozen), TARGET_SEQUENCE, rotations)
    elapsed = perf_counter() - started
    chosen = None
    if result["accepted"]:
        chosen = runner._choose(result["accepted"], list(frozen))
    profile = {
        "candidate_mode": candidate_mode, "orientation_policy": orientation_policy, "rotations": list(rotations),
        "candidate_budget": candidate_budget, "total_ms": round(elapsed * 1000, 3),
        "generation_ms": round(result["generation_ms"], 3), "exact_ms": round(result["exact_ms"], 3),
        "recovery_ms": round(result["recovery_ms"], 3), "validator_elapsed_ms": round(result["validator_elapsed"], 3),
        "budget_expansions": result["budget_expansions"], "raw_candidates": len(result["raw_all"]),
        "recovery_candidates": len(result["recovery"]), "evaluated": result["evaluated"],
        "evaluated_by_orientation": result["evaluated_by_orientation"], "orientation_generated": result["orientation_generated"],
        "accepted": len(result["accepted"]), "candidate_hash": result["candidate_hash"],
        "chosen_position": list(chosen[0].translation) if chosen else None,
        "chosen_orientation": chosen[0].rotation if chosen else None,
        "chosen_source": list(chosen[1].sources) if chosen else None,
        "chosen_marker_length": max([chosen[0].bbox[2], *(p.bbox[2] for p in frozen)]) if chosen else None,
        "nfp_cache_hits": runner.kernel.cache.hits, "nfp_cache_misses": runner.kernel.cache.misses,
        "nfp_cache_hit_rate_pct": round(100 * runner.kernel.cache.hits / max(1, runner.kernel.cache.hits + runner.kernel.cache.misses), 3),
        # Fine-grained hot-path counters.  Stages not applicable to this
        # integer pipeline remain explicit zeroes rather than being silently
        # folded into a generic validation number.
        "timings_ms": {
            "transform_geometry_ms": 0.0, "ifp_ms": 0.0,
            "nfp_cache_lookup_ms": 0.0, "nfp_cache_miss_compute_ms": 0.0,
            "legacy_candidate_generation_ms": 0.0,
            "candidate_space_generation_ms": round(result["generation_ms"], 3),
            "candidate_merge_ms": 0.0, "candidate_dedup_ms": 0.0,
            "bbox_precheck_ms": 0.0, "exact_overlap_ms": 0.0,
            "exact_clearance_ms": round(result["validator_elapsed"], 3),
            "containment_ms": 0.0, "full_validator_ms": 0.0,
            "scoring_ms": 0.0, "total_ms": round(elapsed * 1000, 3),
        },
        "counters": {
            "legacy_candidates_raw": 0, "candidate_space_candidates_raw": len(result["raw_all"]),
            "orientation_0_raw": result["orientation_generated"].get(0, 0),
            "orientation_180_raw": result["orientation_generated"].get(180, 0),
            "candidates_before_dedup": sum(result["orientation_generated"].values()),
            "candidates_after_dedup": len(result["raw_all"]),
            **result.get("validation_counts", {}), "scored_candidates": len(result["accepted"]),
            "nfp_cache_hits": runner.kernel.cache.hits, "nfp_cache_misses": runner.kernel.cache.misses,
            "ifp_cache_hits": 0, "ifp_cache_misses": len(rotations),
        },
    }
    return profile


def main():
    parser = ArgumentParser()
    parser.add_argument("--candidate-mode", default="UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE")
    parser.add_argument("--orientation-policy", default="TWO_WAY")
    parser.add_argument("--candidate-budget", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=Path("artifacts/phase2f7g8p/baseline-profile.json"))
    parser.add_argument("--cprofile", type=Path, default=None, help="Write a cProfile .prof dump here.")
    parser.add_argument("--hot-functions-out", type=Path, default=None, help="Write a top-N cumulative-time text report here.")
    args = parser.parse_args()

    if args.cprofile:
        profiler = cProfile.Profile()
        profiler.enable()
        profile = run_once(args.candidate_mode, args.orientation_policy, args.candidate_budget)
        profiler.disable()
        profiler.dump_stats(str(args.cprofile))
        buffer = io.StringIO()
        stats = pstats.Stats(profiler, stream=buffer).sort_stats("cumulative")
        stats.print_stats(20)
        text = buffer.getvalue()
        print(text)
        if args.hot_functions_out:
            args.hot_functions_out.write_text(text, encoding="utf-8")
    else:
        profile = run_once(args.candidate_mode, args.orientation_policy, args.candidate_budget)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    print(json.dumps(profile, indent=2))


if __name__ == "__main__":
    main()
