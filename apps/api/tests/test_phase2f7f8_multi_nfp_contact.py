"""FASE 2F.7F-8: multi-NFP contacts and an auditable golden-hole fixture."""
from __future__ import annotations

import json
from pathlib import Path

from costura_optima.domain.candidate_space import CandidateSpaceEngine, PlacedGeometry
from costura_optima.domain.integer_kernel import (
    GeometryOperationCache, IntegerGeometryKernel, boundary_intersections, canonical_json_hash, canonical_path, path_bbox,
    rotate_and_translate_point, transform_piece,
)
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import MarkerMargins, MarkerRequest, NestingPiece, PieceInstance, Placement
from costura_optima.patterns.generator import EngineeringPatternGenerator
from costura_optima.patterns.profile import ENGINEERING_PROFILE
from test_phase2f7f6_analytic_geometry import CLEARANCE, PRECISION, make_instance, placement, rect


B = rect(10_000, 10_000)
A1 = rect(10_000, 10_000)
A2 = rect(10_000, 10_000)
REGION = (0, 0, 50_000, 50_000)
A1_POSITION = (21_500, 10_000)
A2_POSITION = (10_000, 21_500)
GOLDEN_POSITION = (11_000, 11_000)


def golden_space():
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    placed = (PlacedGeometry(A1, "A1", 0, A1_POSITION), PlacedGeometry(A2, "A2", 0, A2_POSITION))
    return CandidateSpaceEngine().build(kernel, B, "B", 0, REGION, CLEARANCE, placed), placed


def golden_validator(position):
    first, second, moving = make_instance("A1", A1), make_instance("A2", A2), make_instance("B", B)
    request = MarkerRequest("golden-hole", (first, second, moving), 50_000, 50_000, 50_000, CLEARANCE,
        MarkerMargins(0, 0, 0, 0), (0,), precision=PRECISION)
    report = IndependentMarkerValidator().validate(
        request, (placement(first, A1_POSITION), placement(second, A2_POSITION, sequence=2), placement(moving, position, sequence=3)), 50_000
    )
    return report.status == "VALIDATED", report


def legacy_candidates_only():
    """Frozen legacy extrema proposal set: no NFP intersection or feasible boundary."""
    min_x, min_y, max_x, max_y = path_bbox(B)
    width, height = max_x - min_x, max_y - min_y
    x0, y0, x1, y1 = REGION
    candidates = {(x0, y0), (x0, y1 - height)}
    for obstacle, position in ((A1, A1_POSITION), (A2, A2_POSITION)):
        bx0, by0, bx1, by1 = path_bbox(transform_piece(obstacle, 0, position))
        xs = (x0, bx0, bx1 + CLEARANCE, bx0 - width - CLEARANCE, bx1 - width)
        ys = (y0, by0, by1 + CLEARANCE, by0 - height - CLEARANCE, by1 - height)
        candidates.update((x, y) for x in xs for y in ys)
    return tuple(sorted((x, y) for x, y in candidates if x0 <= x <= x1 - width and y0 <= y <= y1 - height))


def choose(candidates):
    valid = []
    for candidate in candidates:
        position = candidate.position if hasattr(candidate, "position") else candidate
        ok, _ = golden_validator(position)
        if ok:
            contacts = candidate.contact_count if hasattr(candidate, "contact_count") else 0
            # A1 fixes the marker's right extent at 31,500; contact then breaks
            # the compact-length tie, before deterministic X/Y ordering.
            valid.append((max(31_500, position[0] + 10_000), -contacts, position[0], position[1], candidate))
    return min(valid) if valid else None


def write_golden_artifacts(result, legacy, winner):
    debug = Path(__file__).parent / "debug"
    debug.mkdir(exist_ok=True)
    intersections = [candidate for candidate in result.candidates if "NFP_NFP_INTERSECTION" in candidate.sources]
    payload = {
        "fixture": "golden-hole-v1", "container": REGION, "clearance_units": CLEARANCE,
        "a1": {"position": A1_POSITION, "geometry": A1}, "a2": {"position": A2_POSITION, "geometry": A2}, "b": B,
        "ifp": result.ifp_geometry, "nfp_a1": result.nfp_geometries[0], "nfp_a2": result.nfp_geometries[1],
        "forbidden": result.forbidden_union, "feasible": result.feasible_space,
        "nfp_nfp_intersections": [{"position": row.position, "sources": row.sources, "contact_count": row.contact_count} for row in intersections],
        "legacy_candidates": legacy,
        "candidate_space_candidates": [{"position": row.position, "sources": row.sources, "contact_count": row.contact_count} for row in result.candidates],
        "winner": {"position": winner.position, "sources": winner.sources, "contact_count": winner.contact_count},
    }
    (debug / "golden-hole.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    def box(path, fill, name):
        x0, y0, x1, y1 = path_bbox(path)
        return f'<rect x="{x0/100}" y="{(50000-y1)/100}" width="{(x1-x0)/100}" height="{(y1-y0)/100}" fill="{fill}" stroke="#222"/><text x="{x0/100}" y="{(50000-y1)/100-3}">{name}</text>'
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="650" height="350" viewBox="-20 -30 650 350">', '<style>text{font:10px sans-serif}.p{stroke:#111}</style>',
           '<rect x="0" y="0" width="500" height="500" fill="white" stroke="black"/>',
           box(transform_piece(A1, 0, A1_POSITION), '#8dd3ff', 'A1'), box(transform_piece(A2, 0, A2_POSITION), '#8dd3ff', 'A2'),
           box(result.ifp_geometry, 'none', 'IFP'), box(result.nfp_geometries[0][0], '#ffd0d0', 'NFP1'), box(result.nfp_geometries[1][0], '#ffd0d0', 'NFP2'),
           box(transform_piece(B, 0, winner.position), 'none', 'B winner')]
    for point in legacy:
        svg.append(f'<circle cx="{point[0]/100}" cy="{(50000-point[1])/100}" r="2" fill="#999"/>')
    for row in intersections:
        svg.append(f'<circle class="p" cx="{row.position[0]/100}" cy="{(50000-row.position[1])/100}" r="4" fill="#00a56a"/><text x="{row.position[0]/100+4}" y="{(50000-row.position[1])/100-4}">NFP-NFP / {row.contact_count} contacts</text>')
    svg.append('</svg>')
    (debug / "golden-hole.svg").write_text('\n'.join(svg), encoding="utf-8")


def test_nfp_nfp_intersection_yields_real_multi_contact_not_metadata():
    result, placed = golden_space()
    rows = [row for row in result.candidates if "NFP_NFP_INTERSECTION" in row.sources and row.position == GOLDEN_POSITION]
    assert len(rows) == 1
    row = rows[0]
    assert row.contact_count == 2
    assert CandidateSpaceEngine.real_contact_count(B, 0, GOLDEN_POSITION, placed, CLEARANCE) == 2
    valid, report = golden_validator(GOLDEN_POSITION)
    assert valid and report.checks["clearance_satisfied"]


def test_boundary_intersection_keeps_proper_and_collinear_overlap_endpoints():
    first = rect(10, 2)
    second = canonical_path(((5, 0), (15, 0), (15, -2), (5, -2)))
    # The shared collinear boundary is represented by its exact overlap ends.
    assert set(boundary_intersections(first, second)) >= {(5, 0), (10, 0)}


def test_golden_hole_candidate_is_absent_from_legacy_and_wins_by_real_contact():
    result, _ = golden_space()
    legacy = legacy_candidates_only()
    golden = next(row for row in result.candidates if row.position == GOLDEN_POSITION)
    assert GOLDEN_POSITION not in legacy
    assert "NFP_NFP_INTERSECTION" in golden.sources
    legacy_winner = choose(legacy)
    space_winner = choose(result.candidates)
    assert legacy_winner is not None and space_winner is not None
    assert space_winner[-1] == golden
    assert legacy_winner[0] == space_winner[0] == 31_500
    assert canonical_json_hash([(row.position, row.orientation, row.sources, row.contact_count) for row in result.candidates]) == "674ceeebd47fbd0ff11e92843c202c945c41264fa267e5188532113f10a2f1aa"
    write_golden_artifacts(result, legacy, golden)


def test_golden_feasible_property_uses_validator_for_acceptance():
    result, _ = golden_space()
    samples = {"multi_boundary": GOLDEN_POSITION, "forbidden": (11_500, 11_500), "outside_ifp": (-1, 11_000), "feasible": (10_500, 10_500)}
    for kind, position in samples.items():
        raw = CandidateSpaceEngine.is_valid_reference_position(result, position)
        exact, _ = golden_validator(position)
        if kind == "outside_ifp":
            assert not raw and not exact
        elif kind == "forbidden":
            assert not raw and not exact
        else:
            assert raw and exact


def real_pattern_instances():
    measurements = {"BICEPS": 31, "SLEEVE_OPENING": 36, "FINISHED_CHEST": 108, "FINISHED_HEM": 108,
                    "HPS_LENGTH": 68, "FINISHED_SHOULDERS": 43, "SHORT_SLEEVE_LENGTH": 21}
    generated = {item.piece_code: item for item in EngineeringPatternGenerator(ENGINEERING_PROFILE).generate_size("M", 2, measurements)}
    instances = {}
    for code in ("FRONT", "BACK", "SLEEVE"):
        item = generated[code]
        source = canonical_path(item.operational_geometry["coordinates"][0])
        grain = tuple(tuple(round(value * 1000) for value in item.grainline[key]) for key in ("start", "end"))
        piece = NestingPiece(code, "M", code, source, grain, tuple(item.allowed_rotations_degrees), item.mirror_allowed, item.geometry_hash)
        instances[code] = PieceInstance(code, piece)
    return instances


def real_placement(instance, position, rotation, sequence):
    polygon = transform_piece(instance.piece.cut_polygon, rotation, position)
    grain = tuple(rotate_and_translate_point(point, rotation, instance.piece.cut_polygon, position) for point in instance.piece.grainline)
    return Placement(instance.instance_id, instance.piece.pattern_piece_id, "M", instance.piece.piece_code, rotation, False,
                     position, polygon, grain, path_bbox(polygon), instance.piece.geometry_hash, sequence)


def body_sleeve_validator(position, sleeve_rotation=0):
    instances = real_pattern_instances()
    request = MarkerRequest("body-sleeve-real", tuple(instances.values()), 120_000, 120_000, 180_000, CLEARANCE,
        MarkerMargins(0, 0, 0, 0), (0, 180), precision=PRECISION)
    rows = (real_placement(instances["FRONT"], (0, 0), 0, 1), real_placement(instances["BACK"], (60_000, 30_000), 0, 2),
            real_placement(instances["SLEEVE"], position, sleeve_rotation, 3))
    return IndependentMarkerValidator().validate(request, rows, 180_000)


def test_real_body_sleeve_candidate_space_finds_new_two_contact_candidate():
    instances = real_pattern_instances()
    front, back, sleeve = (instances[code].piece.cut_polygon for code in ("FRONT", "BACK", "SLEEVE"))
    placed = (PlacedGeometry(front, "FRONT", 0, (0, 0)), PlacedGeometry(back, "BACK", 0, (60_000, 30_000)))
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    result = CandidateSpaceEngine().build(kernel, sleeve, "SLEEVE", 0, (0, 0, 180_000, 120_000), CLEARANCE, placed)
    expected = (58_000, 5_001)
    row = next(candidate for candidate in result.candidates if candidate.position == expected)
    assert "NFP_NFP_INTERSECTION" in row.sources and row.contact_count == 2
    assert CandidateSpaceEngine.real_contact_count(sleeve, 0, expected, placed, CLEARANCE) == 2
    report = body_sleeve_validator(expected)
    assert report.status == "VALIDATED"
    turned = CandidateSpaceEngine().build(kernel, sleeve, "SLEEVE", 180, (0, 0, 180_000, 120_000), CLEARANCE, placed)
    turned_row = next(candidate for candidate in turned.candidates if candidate.position == expected)
    assert "NFP_NFP_INTERSECTION" in turned_row.sources and turned_row.contact_count == 2
    assert body_sleeve_validator(expected, 180).status == "VALIDATED"
    # The frozen legacy extrema set is intentionally isolated from CandidateSpace.
    x0, y0, x1, y1 = (0, 0, 180_000, 120_000)
    sx0, sy0, sx1, sy1 = path_bbox(sleeve)
    legacy = {(x0, y0), (x0, y1 - (sy1 - sy0))}
    for item in placed:
        bx0, by0, bx1, by1 = path_bbox(transform_piece(item.geometry, item.rotation, item.marker_position))
        legacy.update((x, y) for x in (x0, bx0, bx1 + CLEARANCE, bx0 - (sx1 - sx0) - CLEARANCE, bx1 - (sx1 - sx0))
                      for y in (y0, by0, by1 + CLEARANCE, by0 - (sy1 - sy0) - CLEARANCE, by1 - (sy1 - sy0)))
    assert expected not in legacy
    assert canonical_json_hash([(row.position, row.orientation, row.sources, row.contact_count) for row in result.candidates]) == "ed22d32eace4c54b0221904d6ec6839d9dd204227744de77e8e2807d0ae69444"
