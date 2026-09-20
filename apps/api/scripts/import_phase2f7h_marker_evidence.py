"""Phase 2F.8.1 Section 5: controlled, re-validated import of 2F.7H
experimental marker evidence into the real persistent marker catalog.

Each 2F.7H script used its own throwaway in-memory sqlite engine, so the 4
known-good markers (M2+L1, S3+XXL5, XS3+XL3, XL1) were never persisted as
real MarkerArtifactORM rows -- only as best-marker.json files carrying the
REDUCED {piece_instance_id, translation, rotation, geometry_hash} shape.
This reconstructs full Placement objects from that reduced shape via
transform_piece/rotate_and_translate_point against LIVE pattern geometry
(canonical polygon data -- never the SVGs, per Section 5's explicit
instruction), re-validates every one with IndependentMarkerValidator
before trusting it, and only then persists via
PlanningCoordinator._persist_marker_artifact (same race-safe insert path
production already uses). A marker that fails re-validation (including a
piece-count or geometry-hash mismatch against the live pattern, which would
mean the pattern changed since the evidence was captured -- Section 25) is
rejected and reported, never imported.

Importable as a function (`import_all`) for reuse by the cold/warm
benchmark script, and runnable standalone.
"""
from __future__ import annotations

import json
from math import ceil
from pathlib import Path
from types import SimpleNamespace

from costura_optima.application.planning_coordinator import (
    PlanningCoordinator, _marker_from_payload, _serialize_placement_for_payload,
)
from costura_optima.application.services import build_marker_request
from costura_optima.domain.integer_kernel import (
    GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash, path_bbox, rotate_and_translate_point, transform_piece,
)
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import Placement

TARGET_MARKERS = {
    "M2+L1": {"composition": [("M", 2), ("L", 1)], "path": Path("artifacts/phase2f7h2/M2+L1/best-marker.json")},
    "S3+XXL5": {"composition": [("S", 3), ("XXL", 5)], "path": Path("artifacts/phase2f7h2/S3+XXL5/best-marker.json")},
    "XS3+XL3": {"composition": [("XS", 3), ("XL", 3)], "path": Path("artifacts/phase2f7h3/XS3+XL3/best-marker.json")},
    "XL1": {"composition": [("XL", 1)], "path": Path("artifacts/phase2f7h3/XL1/best-marker.json")},
}


def _placement_from_reduced_row(row: dict, instance, sequence: int) -> Placement:
    rotation = row["rotation"]
    translation = tuple(row["translation"])
    polygon = transform_piece(instance.piece.cut_polygon, rotation, translation)
    grainline = tuple(rotate_and_translate_point(point, rotation, instance.piece.cut_polygon, translation)
                       for point in instance.piece.grainline)
    return Placement(
        instance.instance_id, instance.piece.pattern_piece_id, instance.piece.size_code, instance.piece.piece_code,
        rotation, False, translation, polygon, grainline, path_bbox(polygon), instance.piece.geometry_hash, sequence,
    )


def import_marker(session, pattern_set, fabric, table, tag: str, composition: list[tuple[str, int]], path: Path) -> dict:
    if not path.exists():
        return {"tag": tag, "status": "SKIPPED", "reason": "artifact_file_missing"}
    evidence = json.loads(path.read_text(encoding="utf-8"))
    request = build_marker_request(pattern_set, fabric, table, composition, deterministic=True, seed=1, evaluation_budget=100_000)
    by_id = {item.instance_id: item for item in request.piece_instances}
    rows = evidence["placements"]
    if len(rows) != len(by_id):
        return {"tag": tag, "status": "REJECTED", "reason": f"piece_count_mismatch: evidence={len(rows)} live_pattern={len(by_id)}"}

    placements = []
    for sequence, row in enumerate(sorted(rows, key=lambda item: item["piece_instance_id"]), start=1):
        instance = by_id.get(row["piece_instance_id"])
        if instance is None:
            return {"tag": tag, "status": "REJECTED", "reason": f"unknown_piece_instance:{row['piece_instance_id']}"}
        if instance.piece.geometry_hash != row["geometry_hash"]:
            return {"tag": tag, "status": "REJECTED",
                    "reason": f"geometry_hash_mismatch:{row['piece_instance_id']} (pattern changed since evidence was captured)"}
        placements.append(_placement_from_reduced_row(row, instance, sequence))

    marker_length_units = max(item.bbox[2] for item in placements)
    report = IndependentMarkerValidator().validate(request, tuple(placements), marker_length_units)
    if report.status != "VALIDATED":
        return {"tag": tag, "status": "REJECTED", "reason": f"revalidation_failed: {list(report.errors)}"}

    units = pattern_set.geometry_units_per_cm
    kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())
    piece_area_units2 = sum(kernel.area_units2(item.piece.cut_polygon) for item in request.piece_instances)
    marker_area_units2 = request.usable_width * marker_length_units
    waste_area_units2 = marker_area_units2 - piece_area_units2
    efficiency_pct = round(piece_area_units2 / marker_area_units2 * 100, 6) if marker_area_units2 else 0.0
    lower_bound_length_units = ceil(piece_area_units2 / request.usable_width)

    signature = {"pattern": pattern_set.content_hash, "fabric": fabric.content_hash, "table": table.content_hash,
                 "composition": composition, "engine": "imported-2f7h-evidence-v1", "source_tag": tag}
    content_key = canonical_json_hash(signature)
    marker_payload = {
        "geometry_units_per_cm": units,
        "marker_length_cm": marker_length_units / units,
        "usable_width_cm": request.usable_width / units,
        "piece_area_total_cm2": piece_area_units2 / (units * units),
        "marker_area_cm2": marker_area_units2 / (units * units),
        "waste_area_cm2": waste_area_units2 / (units * units),
        "efficiency_percentage": efficiency_pct,
        "placements": [_serialize_placement_for_payload(item, units) for item in placements],
        "validation": {"status": report.status, "checks": report.checks, "errors": list(report.errors), "pair_checks": report.pair_checks},
        "algorithm_version": "imported-2f7h-evidence-v1",
        "search_status": "IMPORTED_HISTORICAL_EVIDENCE",
        "input_hash": content_key,
        "result_hash": evidence["marker_hash"],
        "lower_bound_length_cm": lower_bound_length_units / units,
    }
    fake_order = SimpleNamespace(catalog_snapshot={
        "fabric_configuration": {"content_hash": fabric.content_hash},
        "cutting_table_configuration": {"content_hash": table.content_hash},
    })
    coordinator = PlanningCoordinator(session)
    artifact, cache_hit = coordinator._persist_marker_artifact(
        content_key, pattern_set, fake_order, dict(composition), marker_payload,
    )
    candidate = _marker_from_payload(artifact)
    return {
        "tag": tag, "status": "IMPORTED" if not cache_hit else "ALREADY_PRESENT",
        "marker_hash": candidate.marker_hash, "marker_length_cm": candidate.marker_length_units / units,
        "efficiency_percentage": candidate.efficiency_percentage,
    }


def import_all(session, pattern_set, fabric, table) -> list[dict]:
    return [
        import_marker(session, pattern_set, fabric, table, tag, spec["composition"], spec["path"])
        for tag, spec in TARGET_MARKERS.items()
    ]


def main() -> None:
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
        fabric = catalog.list_fabrics()[0]
        table = catalog.list_tables()[0]
        results = import_all(session, pattern_set, fabric, table)
        session.commit()

    output_dir = Path("artifacts/phase2f8_1")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "import-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
