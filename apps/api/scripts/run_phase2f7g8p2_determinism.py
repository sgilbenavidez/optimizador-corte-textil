"""Phase 2F.7G-8P2: host determinism + cache-state equivalence for the
optimized M_SLEEVE_004 UNIFIED TWO_WAY pipeline.

Two independent (fresh-kernel) runs are compared stage by stage; a third,
same-runner cold-then-warm pair isolates whether NFP/IFP cache state can
change the outcome. All comparisons use the canonical candidate_budget=5000
policy (Section 9, Phase 2F.7G-8) -- not a reduced sample budget.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.completion_runner import DeterministicCompletionRunner
from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist
from costura_optima.domain.nesting_models import Placement

RESUME_CHECKPOINT = Path("artifacts/phase2f7g7/marker-0-only-run/checkpoints/piece_11.json")
FROZEN_TRACE = Path("artifacts/phase2f7g4/M2_L1-ZERO_ONLY.json")
OUT = Path("artifacts/phase2f7g8p2")
TARGET_PIECE = "M_SLEEVE_004"


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


def _new_runner(tag):
    request = _request()
    frozen = _frozen_prefix()
    order = _piece_order()
    runner = DeterministicCompletionRunner(
        request, frozen, OUT / f"_determinism_{tag}", candidate_budget=5000, recovery_budget=65,
        piece_time_budget_ms=600_000, piece_order=order, candidate_mode="UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE",
        orientation_policy="TWO_WAY", depth2_diagnostics=False,
    )
    instance = {item.instance_id: item for item in request.piece_instances}[TARGET_PIECE]
    return runner, instance, frozen


def _stage_hashes(runner, instance, frozen, result):
    accepted_hash = canonical_json_hash(sorted(
        (c.position, c.orientation, list(c.sources), c.contact_count) for _, c in result["accepted"]
    ))
    chosen = runner._choose(result["accepted"], list(frozen)) if result["accepted"] else None
    return {
        "input_hash": canonical_json_hash([[p.piece_instance_id, p.translation, p.rotation] for p in frozen]),
        "raw_candidate_hash": result["candidate_hash"],
        "dedup_candidate_hash": result["candidate_hash"],  # dedup happens inside _build_pool before this hash is taken
        "validated_set_hash": accepted_hash,
        "scored_set_hash": accepted_hash,  # scoring orders, does not filter, in this pipeline
        "chosen_candidate_hash": canonical_json_hash([chosen[0].translation, chosen[0].rotation, sorted(chosen[1].sources)]) if chosen else None,
        "evaluated": result["evaluated"], "accepted_count": len(result["accepted"]),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    runner1, instance1, frozen1 = _new_runner("run1")
    rotations1 = runner1._effective_rotations(instance1)
    started = perf_counter()
    result1 = runner1._evaluate_piece(instance1, list(frozen1), 12, rotations1)
    hashes1 = _stage_hashes(runner1, instance1, frozen1, result1)
    hashes1["elapsed_s"] = round(perf_counter() - started, 3)
    (OUT / "stage-hashes-run1.json").write_text(json.dumps(hashes1, indent=2), encoding="utf-8")
    print("RUN1", json.dumps(hashes1, indent=2), flush=True)

    runner2, instance2, frozen2 = _new_runner("run2")
    rotations2 = runner2._effective_rotations(instance2)
    started = perf_counter()
    result2 = runner2._evaluate_piece(instance2, list(frozen2), 12, rotations2)
    hashes2 = _stage_hashes(runner2, instance2, frozen2, result2)
    hashes2["elapsed_s"] = round(perf_counter() - started, 3)
    (OUT / "stage-hashes-run2.json").write_text(json.dumps(hashes2, indent=2), encoding="utf-8")
    print("RUN2", json.dumps(hashes2, indent=2), flush=True)

    stages = ["input_hash", "raw_candidate_hash", "dedup_candidate_hash", "validated_set_hash", "scored_set_hash", "chosen_candidate_hash"]
    first_divergence = next((stage for stage in stages if hashes1[stage] != hashes2[stage]), None)
    determinism_diff = {
        "stages_compared": stages,
        "per_stage_equal": {stage: hashes1[stage] == hashes2[stage] for stage in stages},
        "first_nondeterministic_stage": first_divergence or "NONE",
        "determinism_status": "PASS" if first_divergence is None else "FAIL",
    }
    (OUT / "determinism-diff.json").write_text(json.dumps(determinism_diff, indent=2), encoding="utf-8")
    print("DETERMINISM_DIFF", json.dumps(determinism_diff, indent=2), flush=True)

    # Cold vs warm cache, same runner instance (third, independent runner).
    runner3, instance3, frozen3 = _new_runner("cache")
    rotations3 = runner3._effective_rotations(instance3)
    cold_result = runner3._evaluate_piece(instance3, list(frozen3), 12, rotations3)
    cold_hashes = _stage_hashes(runner3, instance3, frozen3, cold_result)
    cold_hashes["nfp_cache_hits"] = runner3.kernel.cache.hits
    cold_hashes["nfp_cache_misses"] = runner3.kernel.cache.misses
    (OUT / "cold-cache.json").write_text(json.dumps(cold_hashes, indent=2), encoding="utf-8")

    warm_result = runner3._evaluate_piece(instance3, list(frozen3), 12, rotations3)
    warm_hashes = _stage_hashes(runner3, instance3, frozen3, warm_result)
    warm_hashes["nfp_cache_hits"] = runner3.kernel.cache.hits
    warm_hashes["nfp_cache_misses"] = runner3.kernel.cache.misses
    (OUT / "warm-cache.json").write_text(json.dumps(warm_hashes, indent=2), encoding="utf-8")

    cache_equal = all(cold_hashes[stage] == warm_hashes[stage] for stage in stages)
    print("CACHE_STATE_EQUIVALENCE", "PASS" if cache_equal else "FAIL", flush=True)
    print(json.dumps({"cold": cold_hashes, "warm": warm_hashes, "cache_state_equivalence": "PASS" if cache_equal else "FAIL"}, indent=2))


if __name__ == "__main__":
    main()
