"""Execute the bounded Phase 2F.7G-5 continuation from frozen M_SLEEVE_002."""
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


def main():
    parser = ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase2f7g5"))
    parser.add_argument("--frozen-trace", type=Path, default=Path("artifacts/phase2f7g4/M2_L1-ZERO_ONLY.json"))
    # 5,000 covers the complete deterministic M_SLEEVE_004 CandidateSpace
    # set (4,357 at checkpoint 11); the old 1,800 prefix silently excluded
    # all 197 independently validated expandable-container candidates.
    parser.add_argument("--candidate-budget", type=int, default=5000)
    parser.add_argument("--recovery-budget", type=int, default=65)
    parser.add_argument("--piece-time-budget-ms", type=int, default=60000)
    parser.add_argument("--resume", type=Path, help="Resume exactly from a phase2f7g5 checkpoint JSON.")
    args = parser.parse_args(); request = _request()
    instances = {item.instance_id: item for item in request.piece_instances}
    trace = json.loads(args.frozen_trace.read_text(encoding="utf-8"))["candidate_trace"]
    piece_order = tuple(row["piece_instance_id"] for row in trace)
    if args.resume:
        checkpoint = json.loads(args.resume.read_text(encoding="utf-8"))
        frozen = tuple(Placement(
            row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"], row["rotation"], row["mirrored"],
            tuple(row["translation"]), tuple(tuple(point) for point in row["transformed_polygon"]),
            tuple(tuple(point) for point in row["transformed_grainline"]), tuple(row["bbox"]), row["geometry_hash"], row["sequence"],
        ) for row in checkpoint["placed_pieces"])
    else:
        frozen = tuple(_placement(instances[row["piece_instance_id"]], tuple(row["chosen_candidate"]), 0, row["piece_index"]) for row in trace[:10])
    summary = DeterministicCompletionRunner(request, frozen, args.output_dir, candidate_budget=args.candidate_budget,
        recovery_budget=args.recovery_budget, piece_time_budget_ms=args.piece_time_budget_ms, piece_order=piece_order).run()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__": main()
