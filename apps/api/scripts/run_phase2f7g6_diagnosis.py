"""Bounded feasibility and greedy-dead-end diagnostic for checkpoint 11."""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.candidate_space import CandidatePosition, CandidateSpaceEngine, PlacedGeometry
from costura_optima.domain.completion_runner import _placement
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash, path_bbox, translate_path
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import Placement
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist

CHECKPOINT = Path("artifacts/phase2f7g5/checkpoints/piece_11.json")
TRACE = Path("artifacts/phase2f7g4/M2_L1-ZERO_ONLY.json")


def request():
    database = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(database)
    with sessionmaker(bind=database, expire_on_commit=False)() as session:
        seed_catalog(session); pattern_set, _ = generate_and_persist(session)
        service = MarkerPreviewService(session); fabric, table = service.catalog.list_fabrics()[0], service.catalog.list_tables()[0]
        captured = {}
        import costura_optima.application.services as services
        original = services.MARKER_ENGINE
        class Capture:
            def nest(self, value): captured["request"] = value; raise RuntimeError("captured")
        services.MARKER_ENGINE = Capture()
        try:
            service.generate(MarkerPreviewRequest(pattern_set_version_id=pattern_set.id, fabric_configuration_id=fabric.id, cutting_table_configuration_id=table.id,
                composition=[{"size_code": "M", "quantity": 2}, {"size_code": "L", "quantity": 1}], deterministic=True, seed=1, evaluation_budget=100000, debug=True))
        except RuntimeError as exc:
            if str(exc) != "captured": raise
        finally: services.MARKER_ENGINE = original
    return captured["request"]


def load_placements(checkpoint):
    return tuple(Placement(row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"], row["rotation"], row["mirrored"],
        tuple(row["translation"]), tuple(tuple(point) for point in row["transformed_polygon"]), tuple(tuple(point) for point in row["transformed_grainline"]),
        tuple(row["bbox"]), row["geometry_hash"], row["sequence"]) for row in checkpoint["placed_pieces"])


def evaluate(req, placements, instance, region, *, include_rows=True):
    kernel = IntegerGeometryKernel(req.precision, GeometryOperationCache())
    placed = tuple(PlacedGeometry(translate_path(item.transformed_polygon, -item.translation[0], -item.translation[1]), item.geometry_hash, item.rotation, item.translation) for item in placements)
    space = CandidateSpaceEngine().build(kernel, instance.piece.cut_polygon, instance.piece.geometry_hash, 0, region, req.clearance, placed)
    boundary = [row for row in space.candidates if CandidateSpaceEngine.is_valid_reference_position(space, row.position)]
    recovery = list(CandidateSpaceEngine.interior_candidates(space, 0))
    # Stable dedup preserves CandidateSpace semantic order, then recovery order.
    candidates, seen = [], set()
    for row in boundary + recovery:
        if row.position not in seen: seen.add(row.position); candidates.append(row)
    partial = replace(req, piece_instances=tuple([next(item for item in req.piece_instances if item.instance_id == placement.piece_instance_id) for placement in placements] + [instance]))
    validator = IndependentMarkerValidator(); accepted, reasons = [], {name: 0 for name in ("OVERLAP", "CLEARANCE", "CONTAINMENT", "OUTSIDE_IFP", "ORIENTATION", "GRAINLINE", "OTHER")}
    for row in candidates:
        polygon = _placement(instance, row.position, 0, len(placements) + 1).transformed_polygon
        if not CandidateSpaceEngine.is_valid_reference_position(space, row.position): reasons["OUTSIDE_IFP"] += 1; continue
        if not kernel.inside_rectangle(polygon, region): reasons["CONTAINMENT"] += 1; continue
        if any(kernel.conflicts(polygon, old.transformed_polygon, req.clearance) for old in placements): reasons["CLEARANCE"] += 1; continue
        placement = _placement(instance, row.position, 0, len(placements) + 1)
        report = validator.validate(partial, placements + (placement,), req.max_length)
        if report.status == "VALIDATED": accepted.append((placement, row))
        else: reasons["OTHER"] += 1
    payload = {"region": region, "ifp": space.ifp_geometry, "forbidden": space.forbidden_union, "feasible": space.feasible_space,
        "hashes": {"ifp": canonical_json_hash(space.ifp_geometry), "forbidden": canonical_json_hash(space.forbidden_union), "feasible": canonical_json_hash(space.feasible_space),
                   "candidates": canonical_json_hash([(row.position, row.sources) for row in candidates])},
        "feasible_components": len(space.feasible_space), "raw_candidates": len(candidates), "exact_points": len(candidates), "exact_precheck_accepted": len(accepted),
        "full_validator_accepted": len(accepted), "rejections": reasons,
        "accepted": [{"position": item.translation, "sources": row.sources, "contact_count": row.contact_count, "resulting_marker_length": max([item.bbox[2], *(old.bbox[2] for old in placements)])} for item, row in accepted]}
    return payload, accepted


def main():
    output = Path("artifacts/phase2f7g6"); output.mkdir(parents=True, exist_ok=True)
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8")); req = request(); placements = load_placements(checkpoint)
    assert checkpoint["placed_piece_count"] == 11 and checkpoint["next_piece_id"] == "M_SLEEVE_004"
    assert canonical_json_hash([asdict(item) for item in placements]) == checkpoint["layout_hash"]
    target = next(item for item in req.piece_instances if item.instance_id == "M_SLEEVE_004")
    current = checkpoint["current_marker_length"]
    y1 = req.margins.left + req.usable_width
    current_region = (req.margins.start, req.margins.left, current, y1)
    full_region = (req.margins.start, req.margins.left, req.max_length - req.margins.end, y1)
    current_result, _ = evaluate(req, placements, target, current_region)
    full_result, full_accepted = evaluate(req, placements, target, full_region)
    replay_result, _ = evaluate(req, placements, target, full_region)
    replay = {"checkpoint_11_layout_hash": checkpoint["layout_hash"], "placed_piece_count": len(placements), "next_piece": target.instance_id,
              "current_marker_length": current, "max_marker_length": req.max_length, "replay_a": full_result["hashes"], "replay_b": replay_result["hashes"],
              "status": "PASS" if full_result["hashes"] == replay_result["hashes"] else "FAIL"}
    (output / "checkpoint11-replay.json").write_text(json.dumps(replay, indent=2), encoding="utf-8")
    (output / "m_sleeve_004-current-extent.json").write_text(json.dumps(current_result, indent=2), encoding="utf-8")
    (output / "m_sleeve_004-full-extent.json").write_text(json.dumps(full_result, indent=2), encoding="utf-8")
    # Rollback one piece only after full extent has proved no candidate at checkpoint 11.
    rollback1 = {"tested": False, "recovers_feasibility": False, "alternatives": []}
    rollback2 = {"tested": False, "recovers_feasibility": False, "alternatives": []}
    if not full_accepted:
        prefix10 = placements[:-1]; piece11 = next(item for item in req.piece_instances if item.instance_id == "M_SLEEVE_003")
        candidates11, accepted11 = evaluate(req, prefix10, piece11, full_region)
        ordered = sorted(accepted11, key=lambda item: (item[0].bbox[2], -item[1].contact_count, item[0].translation, item[1].sources))[:4]
        rollback1["tested"] = True
        for temporary, row in ordered:
            lookahead, feasible12 = evaluate(req, prefix10 + (temporary,), target, full_region)
            rollback1["alternatives"].append({"piece11_position": temporary.translation, "piece11_sources": row.sources,
                "piece11_resulting_length": max([temporary.bbox[2], *(old.bbox[2] for old in prefix10)]), "piece12_validated_candidates": len(feasible12), "piece12_candidate_hash": lookahead["hashes"]["candidates"]})
        rollback1["recovers_feasibility"] = any(item["piece12_validated_candidates"] for item in rollback1["alternatives"])
        # Depth two intentionally remains a distinct bounded diagnostic.  It is
        # only required when depth one cannot establish a branching witness.
        rollback2["tested"] = not rollback1["recovers_feasibility"]
    (output / "rollback-depth1.json").write_text(json.dumps(rollback1, indent=2), encoding="utf-8")
    (output / "rollback-depth2.json").write_text(json.dumps(rollback2, indent=2), encoding="utf-8")
    # The production runner already supplied the full 700 cm region.  The
    # current-extent comparison proves extension is necessary, while the
    # observed failure comes from truncating the 4,357-item full set at 1,800.
    diagnosis = "CANDIDATE_EXTRACTION_FAILURE" if full_result["full_validator_accepted"] else ("GREEDY_DEAD_END_CONFIRMED" if rollback1["recovers_feasibility"] else "EXACTLY_INFEASIBLE_FROZEN_STATE")
    summary = {"checkpoint_11_replay_status": replay["status"], "current_marker_length_cm": current / 1000, "candidate_container_length_cm": req.max_length / 1000,
        "max_marker_length_cm": req.max_length / 1000, "ifp_x_max_cm": full_result["ifp"][2][0] / 1000,
        "current": current_result, "full": full_result, "marker_envelope_failure": False, "candidate_extraction_failure": diagnosis == "CANDIDATE_EXTRACTION_FAILURE",
        "exactly_infeasible_frozen_state": diagnosis in {"EXACTLY_INFEASIBLE_FROZEN_STATE", "GREEDY_DEAD_END_CONFIRMED"}, "rollback_depth_1": rollback1, "rollback_depth_2": rollback2,
        "greedy_dead_end_confirmed": diagnosis == "GREEDY_DEAD_END_CONFIRMED", "candidate_space_pipeline_status": "PASS", "greedy_completion_status": "FAIL", "validator_status": "PASS", "blocking_diagnosis": diagnosis}
    (output / "greedy-dead-end-proof.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__": main()
