"""Analytic marker-space proof fixture for FASE 2F.7F-6.

Coordinates are integer kernel units (1 cm = 1_000).  The principal fixture is
resolved independently on paper: B's reference point is its lower-left corner.
The current clearance contract is Euclidean: positions whose polygon distance is
strictly less than 500 are invalid and exact 500-unit contact is valid.
"""
from __future__ import annotations

from math import pi
from pathlib import Path

import pyclipper
import pytest
from shapely.geometry import Point, Polygon

from costura_optima.domain.candidate_space import CandidateSpaceEngine, PlacedGeometry
from costura_optima.domain.integer_kernel import (
    GeometryOperationCache, IntegerGeometryKernel, canonical_path, canonical_paths, path_bbox,
    rotate_and_translate_point, signed_area2, transform_piece, translate_path,
)
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import (
    MarkerMargins, MarkerRequest, NestingPiece, PieceInstance, Placement, PrecisionConfiguration,
)


U = 1_000
CLEARANCE = 500
REGION = (0, 0, 100 * U, 50 * U)
PRECISION = PrecisionConfiguration()

# Independent formula: (A [- B) dilated by the 0.5cm Euclidean disk.
# The rectangle core is [10,40] x [15,30] cm; its rounded bounds are below.
EXPECTED_NFP_BOUNDS = (9_500, 14_500, 40_500, 30_500)
EXPECTED_IFP = ((0, 0), (90_000, 0), (90_000, 45_000), (0, 45_000))
EXPECTED_NFP_EUCLIDEAN_AREA = 30_000 * 15_000 + (2 * (30_000 + 15_000)) * CLEARANCE + pi * CLEARANCE**2


def rect(width: int, height: int):
    return canonical_path(((0, 0), (width, 0), (width, height), (0, height)))


def fixture(a_size=(20 * U, 10 * U), a_position=(20 * U, 20 * U), b_size=(10 * U, 5 * U), rotation=0):
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    a, b = rect(*a_size), rect(*b_size)
    result = CandidateSpaceEngine().build(
        kernel, b, "B", rotation, REGION, CLEARANCE,
        (PlacedGeometry(a, "A", 0, a_position),),
    )
    return kernel, a, b, result


def make_instance(instance_id: str, polygon, rotations=(0, 180)):
    width = path_bbox(polygon)[2]
    return PieceInstance(instance_id, NestingPiece(
        pattern_piece_id=instance_id, size_code="analytic", piece_code=instance_id,
        cut_polygon=polygon, grainline=((0, 0), (0, 0)), allowed_rotations=rotations,
        mirror_allowed=False, geometry_hash=instance_id,
    ))


def placement(instance: PieceInstance, position, rotation=0, sequence=1):
    polygon = transform_piece(instance.piece.cut_polygon, rotation, position)
    grain_point = rotate_and_translate_point((0, 0), rotation, instance.piece.cut_polygon, position)
    return Placement(instance.instance_id, instance.piece.pattern_piece_id, "analytic", instance.instance_id,
        rotation, False, position, polygon, (grain_point, grain_point),
        path_bbox(polygon), instance.piece.geometry_hash, sequence)


def validator_valid(a, b, a_position, b_position, rotation=0):
    first, second = make_instance("A", a), make_instance("B", b, (0, 180))
    request = MarkerRequest("analytic-proof", (first, second), 50 * U, 50 * U, 100 * U, CLEARANCE,
        MarkerMargins(0, 0, 0, 0), (0, 180), precision=PRECISION)
    report = IndependentMarkerValidator().validate(
        request, (placement(first, a_position), placement(second, b_position, rotation, 2)), 100 * U
    )
    return report.status == "VALIDATED"


def candidate_valid(result, position):
    return CandidateSpaceEngine.is_valid_reference_position(result, position)


def nfp_area(path):
    return abs(signed_area2(path)) / 2


def write_svg(result, a, b, points):
    """A deliberately plain, audit-friendly cm-scale diagnostic drawing."""
    def box(path, fill, label):
        x0, y0, x1, y1 = path_bbox(path)
        return f'<rect x="{x0 / U * 6}" y="{(50 * U - y1) / U * 6}" width="{(x1-x0)/U*6}" height="{(y1-y0)/U*6}" fill="{fill}"/><text x="{x0/U*6}" y="{(50*U-y1)/U*6-3}">{label}</text>'
    a_marker = transform_piece(a, 0, (20 * U, 20 * U))
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="760" height="390" viewBox="-30 -30 760 390">',
           '<style>text{font:12px sans-serif}.pt{stroke:#111;stroke-width:1}</style>',
           '<rect x="0" y="0" width="600" height="300" fill="white" stroke="black"/>',
           box(a_marker, '#82cfff', 'A'), box(result.ifp_geometry, 'none', 'IFP B'),
           box(result.forbidden_union[0], '#ffb3b3', 'NFP(A,B) bounds')]
    for name, p in points.items():
        placed = transform_piece(b, 0, p)
        svg.append(box(placed, 'none', f'B {name}'))
        svg.append(f'<circle class="pt" cx="{p[0]/U*6}" cy="{(50*U-p[1])/U*6}" r="3" fill="#111"/><text x="{p[0]/U*6+4}" y="{(50*U-p[1])/U*6-4}">{name} ref</text>')
    svg.append('</svg>')
    target = Path(__file__).parent / 'debug' / 'phase_2f_7f_6_analytic_geometry.svg'
    target.parent.mkdir(exist_ok=True)
    target.write_text('\n'.join(svg), encoding='utf-8')


def test_analytic_ifp_rectangle_and_direct_transform_common_space():
    _, a, b, result = fixture()
    assert result.ifp_geometry == EXPECTED_IFP
    points = {"P1": (20_000, 20_000), "P2": (0, 0), "P3": (40_500, 20_000), "P4": (40_499, 20_000)}
    for point in points.values():
        assert transform_piece(b, 0, point) == tuple((x + point[0], y + point[1]) for x, y in b)
    assert [candidate_valid(result, p) for p in points.values()] == [False, True, True, False]
    assert [validator_valid(a, b, (20_000, 20_000), p) for p in points.values()] == [False, True, True, False]
    write_svg(result, a, b, points)


def test_analytic_nfp_bounds_single_forbidden_and_feasible_area_diagnosis():
    _, _, _, result = fixture()
    nfp = result.nfp_geometries[0][0]
    assert path_bbox(nfp) == EXPECTED_NFP_BOUNDS
    assert result.forbidden_union == (nfp,)
    # The Euclidean rounded-rectangle formula is independent of NfpEngine.
    actual_forbidden_area = nfp_area(nfp)
    actual_feasible_area = (90_000 * 45_000) - actual_forbidden_area
    expected_feasible_area = (90_000 * 45_000) - EXPECTED_NFP_EUCLIDEAN_AREA
    assert actual_forbidden_area < EXPECTED_NFP_EUCLIDEAN_AREA
    assert actual_feasible_area > expected_feasible_area


def test_regular_half_centimeter_grid_matches_independent_validator():
    _, a, b, result = fixture()
    matrix = {"candidate_space_valid_validator_valid": 0, "candidate_space_valid_validator_invalid": 0,
              "candidate_space_invalid_validator_valid": 0, "candidate_space_invalid_validator_invalid": 0}
    for x in range(0, 90 * U + 1, 500):
        for y in range(0, 45 * U + 1, 500):
            candidate, valid = candidate_valid(result, (x, y)), validator_valid(a, b, (20 * U, 20 * U), (x, y))
            matrix[f"candidate_space_{'valid' if candidate else 'invalid'}_validator_{'valid' if valid else 'invalid'}"] += 1
    assert matrix["candidate_space_valid_validator_invalid"] == 0
    assert matrix["candidate_space_invalid_validator_valid"] == 0
    assert sum(matrix.values()) == 16_471


@pytest.mark.parametrize("point, expected", [
    ((40_499, 20_000), False), ((40_500, 20_000), True), ((40_501, 20_000), True),
    ((20_000, 14_499), True), ((20_000, 14_500), True), ((20_000, 14_501), False),
])
def test_boundary_plus_minus_one_units(point, expected):
    _, a, b, result = fixture()
    assert candidate_valid(result, point) == expected
    assert validator_valid(a, b, (20 * U, 20 * U), point) == expected


def test_second_rectangle_and_180_keep_marker_reference_semantics():
    _, a, b, result = fixture((17_000, 13_000), (23_000, 11_000), (9_000, 7_000))
    # IFP and direct transform are independently determined from B's dimensions.
    assert result.ifp_geometry == ((0, 0), (91_000, 0), (91_000, 43_000), (0, 43_000))
    for rotation in (0, 180):
        _, _, _, turned = fixture(rotation=rotation)
        for point in ((0, 0), (40_500, 20_000), (40_499, 20_000)):
            assert candidate_valid(turned, point) == validator_valid(rect(20_000, 10_000), rect(10_000, 5_000), (20_000, 20_000), point, rotation)


def test_concave_l_property_grid_has_no_half_centimeter_divergence():
    concave = canonical_path(((0, 0), (17_000, 0), (17_000, 5_000), (8_000, 5_000), (8_000, 13_000), (0, 13_000)))
    kernel, _, b, result = fixture((17_000, 13_000), (23_000, 11_000), (9_000, 7_000))
    # Rebuild only this fixture's placed obstacle as the L; no manual NFP claimed.
    result = CandidateSpaceEngine().build(kernel, b, "B", 0, REGION, CLEARANCE, (PlacedGeometry(concave, "L", 0, (23_000, 11_000)),))
    for x in range(0, 91_000 + 1, 500):
        for y in range(0, 43_000 + 1, 500):
            assert candidate_valid(result, (x, y)) == validator_valid(concave, b, (23_000, 11_000), (x, y))


def test_canonicalization_preserves_nfp_area_membership_and_topology():
    _, a, b, result = fixture()
    # Reconstruct the raw Pyclipper output; expected data is not read from the
    # candidate-space result before canonicalization.
    offsetter = pyclipper.PyclipperOffset(miter_limit=2.0, arc_tolerance=5.0)
    offsetter.AddPath(list(a), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = offsetter.Execute(CLEARANCE)
    raw_paths = pyclipper.MinkowskiSum(list(max(expanded, key=lambda path: abs(pyclipper.Area(path)))), [(-x, -y) for x, y in b], True)
    raw = (translate_path(tuple(max(raw_paths, key=lambda path: abs(pyclipper.Area(path)))), 20_000, 20_000),)
    canonical = canonical_paths(raw)
    assert sum(nfp_area(path) for path in raw) == sum(nfp_area(path) for path in canonical)
    assert len(raw) == len(canonical)
    assert canonical == result.forbidden_union
    for point in ((9_500, 15_000), (10_000, 15_000), (0, 0), (40_500, 30_000)):
        assert [pyclipper.PointInPolygon(point, list(path)) for path in raw] == [pyclipper.PointInPolygon(point, list(path)) for path in canonical]


def test_candidate_space_counterexample_001_clearance_error_is_preserved():
    """Minimum integer counterexample: rounded Pyclipper NFP chord misses a validly forbidden point."""
    _, a, b, result = fixture()
    point = (9_501, 14_969)
    distance = Polygon(transform_piece(a, 0, (20_000, 20_000))).distance(Polygon(transform_piece(b, 0, point)))
    assert candidate_valid(result, point) is True
    assert validator_valid(a, b, (20_000, 20_000), point) is False
    assert distance < CLEARANCE
    assert pyclipper.PointInPolygon(point, list(result.forbidden_union[0])) == 0
