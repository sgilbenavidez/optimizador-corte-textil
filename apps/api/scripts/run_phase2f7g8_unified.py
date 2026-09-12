"""Execute the Phase 2F.7G-8 unified-pool / two-way-orientation completion runs.

Resumes from the exact same frozen 11-piece prefix used at the end of
2F.7G-7 (same piece order, same geometry) and completes the remaining four
pieces (M_SLEEVE_004, L_NECKBAND_001, M_NECKBAND_001, M_NECKBAND_002) under
one of three configurations:

  candidate_space_only  -- regression check: must reproduce 238.852 cm.
  unified_zero          -- UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE, ZERO_ONLY.
  unified_two_way       -- UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE, TWO_WAY.
"""
from __future__ import annotations

from argparse import ArgumentParser
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.completion_runner import DeterministicCompletionRunner, _placement
from costura_optima.domain.nesting_models import Placement
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist


FROZEN_TRACE = Path("artifacts/phase2f7g4/M2_L1-ZERO_ONLY.json")
RESUME_CHECKPOINT = Path("artifacts/phase2f7g7/marker-0-only-run/checkpoints/piece_11.json")

CONFIGS = {
    "candidate_space_only": {"candidate_mode": "CANDIDATE_SPACE_ONLY", "orientation_policy": "ZERO_ONLY", "depth2": False},
    "unified_zero": {"candidate_mode": "UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE", "orientation_policy": "ZERO_ONLY", "depth2": True},
    "unified_two_way": {"candidate_mode": "UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE", "orientation_policy": "TWO_WAY", "depth2": True},
}


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


def _frozen_prefix(instances):
    checkpoint = json.loads(RESUME_CHECKPOINT.read_text(encoding="utf-8"))
    return tuple(Placement(
        row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"], row["rotation"], row["mirrored"],
        tuple(row["translation"]), tuple(tuple(point) for point in row["transformed_polygon"]),
        tuple(tuple(point) for point in row["transformed_grainline"]), tuple(row["bbox"]), row["geometry_hash"], row["sequence"],
    ) for row in checkpoint["placed_pieces"])


def main():
    parser = ArgumentParser()
    parser.add_argument("--config", choices=tuple(CONFIGS) + ("all",), required=True)
    parser.add_argument("--run-tag", default="run1", help="Output subdirectory tag, e.g. run1 / run2 for determinism pairs.")
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/phase2f7g8"))
    parser.add_argument("--candidate-budget", type=int, default=5000)
    parser.add_argument("--recovery-budget", type=int, default=65)
    parser.add_argument("--piece-time-budget-ms", type=int, default=60000)
    args = parser.parse_args()

    request = _request()
    instances = {item.instance_id: item for item in request.piece_instances}
    trace = json.loads(FROZEN_TRACE.read_text(encoding="utf-8"))["candidate_trace"]
    piece_order = tuple(row["piece_instance_id"] for row in trace)
    frozen = _frozen_prefix(instances)

    def heartbeat(stats):
        print(f"[{stats['piece']}][{stats['orientation']}] "
              f"processed={stats['processed']}/{stats['total']} valid={stats['valid']} "
              f"elapsed={stats['elapsed_s']}s best_length={stats['best_length']} "
              f"nfp_cache_hit={stats['nfp_cache_hit_rate_pct']}%", flush=True)

    configs = list(CONFIGS) if args.config == "all" else [args.config]
    results = {}
    for name in configs:
        spec = CONFIGS[name]
        output_dir = args.output_root / name / args.run_tag
        summary = DeterministicCompletionRunner(
            request, frozen, output_dir, candidate_budget=args.candidate_budget, heartbeat=heartbeat,
            recovery_budget=args.recovery_budget, piece_time_budget_ms=args.piece_time_budget_ms,
            piece_order=piece_order, candidate_mode=spec["candidate_mode"],
            orientation_policy=spec["orientation_policy"], depth2_diagnostics=spec["depth2"],
        ).run()
        results[name] = {"outcome": summary["outcome"], "layout_hash": summary["layout_hash"],
                          "marker_length_units": max((row["marker_length_after_placement"] for row in summary["piece_metrics"] if row["marker_length_after_placement"]), default=None)}
        print(json.dumps({name: results[name]}, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
