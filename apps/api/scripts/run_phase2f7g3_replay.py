"""Frozen, single-piece M_SLEEVE_002 CandidateSpace replay."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.candidate_space import CandidateSpaceEngine, PlacedGeometry
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash, path_bbox, rotate_and_translate_point, transform_piece
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import Placement
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist

SOURCE = Path("artifacts/phase2f7g1/M2_L1-ZERO_ONLY.json")
OUT = Path("artifacts/phase2f7g3")
TARGET = "M_SLEEVE_002"


def make_placement(instance, position, sequence):
    polygon = transform_piece(instance.piece.cut_polygon, 0, position)
    return Placement(instance.instance_id, instance.piece.pattern_piece_id, instance.piece.size_code,
        instance.piece.piece_code, 0, False, tuple(position), polygon,
        tuple(rotate_and_translate_point(point, 0, instance.piece.cut_polygon, tuple(position)) for point in instance.piece.grainline),
        path_bbox(polygon), instance.piece.geometry_hash, sequence)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        seed_catalog(session); pattern_set, _ = generate_and_persist(session)
        service = MarkerPreviewService(session)
        fabric, table = service.catalog.list_fabrics()[0], service.catalog.list_tables()[0]
        # Build the authoritative request without running its engine.
        captured = {}
        original = __import__("costura_optima.application.services", fromlist=["MARKER_ENGINE"]).MARKER_ENGINE
        class Capture:
            def nest(self, request): captured["request"] = request; raise RuntimeError("captured")
        import costura_optima.application.services as svc
        svc.MARKER_ENGINE = Capture()
        try:
            service.generate(MarkerPreviewRequest(pattern_set_version_id=pattern_set.id, fabric_configuration_id=fabric.id,
                cutting_table_configuration_id=table.id, composition=[{"size_code":"M","quantity":2},{"size_code":"L","quantity":1}],
                deterministic=True, seed=1, evaluation_budget=100000, debug=True))
        except RuntimeError: pass
        finally: svc.MARKER_ENGINE = original
    request = captured["request"]
    trace = json.loads(SOURCE.read_text(encoding="utf-8"))["candidate_trace"]
    prefix = trace[:9]
    by_id = {item.instance_id:item for item in request.piece_instances}
    placements = tuple(make_placement(by_id[row["piece_instance_id"]], row["chosen_candidate"], row["piece_index"]) for row in prefix)
    target = by_id[TARGET]
    region = (request.margins.start, request.margins.left, request.max_length-request.margins.end, request.margins.left+request.usable_width)
    kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())
    placed = tuple(PlacedGeometry(transform_piece(p.transformed_polygon, 0, (-p.translation[0],-p.translation[1])), p.geometry_hash, p.rotation, p.translation) for p in placements)
    result = CandidateSpaceEngine().build(kernel, target.piece.cut_polygon, target.piece.geometry_hash, 0, region, request.clearance, placed)
    raw = list(result.candidates) + list(CandidateSpaceEngine.interior_candidates(result, 0))
    rows=[]
    partial_request = replace(request, piece_instances=tuple([by_id[row["piece_instance_id"]] for row in prefix] + [target]))
    for row in raw:
        polygon=transform_piece(target.piece.cut_polygon,0,row.position)
        if not CandidateSpaceEngine.is_valid_reference_position(result,row.position): reason="OUTSIDE_IFP"
        elif not kernel.inside_rectangle(polygon,region): reason="CONTAINMENT"
        else:
            bad=[p for p in placements if kernel.conflicts(polygon,p.transformed_polygon,request.clearance)]
            reason="CLEARANCE" if bad else None
        valid=False
        if reason is None:
            report=IndependentMarkerValidator().validate(partial_request, placements+(make_placement(target,row.position,10),), request.max_length)
            valid=report.status=="VALIDATED"; reason=None if valid else "OTHER"
        rows.append({"position":row.position,"sources":row.sources,"accepted":valid,"reason":reason})
    payload={"target":TARGET,"partial_layout_hash":canonical_json_hash([asdict(p) for p in placements]),"ifp":result.ifp_geometry,"nfp":result.nfp_geometries,"forbidden":result.forbidden_union,"feasible":result.feasible_space,"candidates":rows}
    (OUT/"m_sleeve_002_replay.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    print(json.dumps({"raw":len(rows),"accepted":sum(x["accepted"] for x in rows),"hash":canonical_json_hash(payload)}))

if __name__ == "__main__": main()
