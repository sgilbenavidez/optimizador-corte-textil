from dataclasses import replace

import pytest
from shapely.geometry import Point, Polygon

from costura_optima.domain.integer_kernel import (
    GeometryOperationCache,
    IntegerGeometryKernel,
    canonical_path,
    canonical_paths,
    boundary_intersections,
    path_bbox,
    PIECE_REFERENCE_POINT,
    transform_piece,
    transform_and_normalize,
    translate_path,
)
from costura_optima.domain.marker_validator import IncrementalCandidateValidator, IndependentMarkerValidator
from costura_optima.domain.candidate_space import CandidateSpaceEngine, PlacedGeometry
from costura_optima.domain.nesting_engine import DeterministicNestingEngine
from costura_optima.domain.nesting_models import (
    MarkerMargins,
    MarkerRequest,
    NestingPiece,
    PieceInstance,
    Placement,
    PrecisionConfiguration,
)


PRECISION = PrecisionConfiguration()


def rectangle(width=10_000, height=20_000, *, reverse=False, duplicate=False):
    points = [(0, 0), (width, 0), (width, height), (0, height)]
    if duplicate:
        points.insert(1, (0, 0))
    if reverse:
        points.reverse()
    return canonical_path(points)


def piece(instance_id="M_FRONT_001", polygon=None, rotations=(0, 180)):
    polygon = polygon or rectangle()
    geometry = NestingPiece(
        pattern_piece_id="pattern-front-m",
        size_code="M",
        piece_code="FRONT",
        cut_polygon=polygon,
        grainline=((5_000, 2_000), (5_000, 18_000)),
        allowed_rotations=rotations,
        mirror_allowed=False,
        geometry_hash=f"hash-{instance_id}",
    )
    return PieceInstance(instance_id, geometry)


def request(instances, *, clearance=500, width=50_000, length=100_000, transforms=(0, 180), margins=None):
    return MarkerRequest(
        request_id="test-request",
        piece_instances=tuple(instances),
        usable_width=width,
        physical_width=width,
        max_length=length,
        clearance=clearance,
        margins=margins or MarkerMargins(0, 0, 0, 0),
        allowed_transforms=transforms,
        evaluation_budget=10_000,
        precision=PRECISION,
    )


def placement(instance, translation=(0, 0), rotation=0, sequence=1):
    oriented = transform_and_normalize(instance.piece.cut_polygon, rotation)
    polygon = translate_path(oriented, *translation)
    # Rectangular fixture has a vertical grainline in both supported rotations.
    grain = ((translation[0] + 5_000, translation[1] + 2_000), (translation[0] + 5_000, translation[1] + 18_000))
    if rotation == 180:
        grain = tuple(reversed(grain))
    return Placement(
        piece_instance_id=instance.instance_id,
        pattern_piece_id=instance.piece.pattern_piece_id,
        size_code="M",
        piece_code="FRONT",
        rotation=rotation,
        mirrored=False,
        translation=translation,
        transformed_polygon=polygon,
        transformed_grainline=grain,
        bbox=path_bbox(polygon),
        geometry_hash=instance.piece.geometry_hash,
        sequence=sequence,
    )


def test_incremental_validator_matches_full_prefix_validator():
    """The broad-phase shortcut cannot change the independent decision."""
    first, moving = piece("A"), piece("B")
    full_request = request((first, moving), clearance=500)
    prefix = placement(first, (0, 0), 0, 1)
    incremental = IncrementalCandidateValidator(full_request, (prefix,))
    full = IndependentMarkerValidator()
    # Inside/no-clearance, clearance boundary, and containment cases exercise
    # both exact and broad-phase paths for both legal orientations.
    for rotation, position in ((0, (10_500, 0)), (180, (10_500, 0)), (0, (10_499, 0)), (180, (90_001, 0))):
        candidate = placement(moving, position, rotation, 2)
        incremental_ok = incremental.validate(moving, candidate).accepted
        full_ok = full.validate(full_request, (prefix, candidate), full_request.max_length).status == "VALIDATED"
        assert incremental_ok is full_ok


def test_contact_semantics_for_zero_and_positive_clearance():
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    first = rectangle()
    touching = translate_path(first, 10_000, 0)
    point_touching = translate_path(first, 10_000, 20_000)
    overlap = translate_path(first, 9_999, 0)
    exact_clearance = translate_path(first, 10_500, 0)
    short_clearance = translate_path(first, 10_499, 0)
    assert not kernel.conflicts(first, touching, 0)
    assert not kernel.conflicts(first, point_touching, 0)
    assert kernel.conflicts(first, overlap, 0)
    assert kernel.conflicts(first, touching, 500)
    assert not kernel.conflicts(first, exact_clearance, 500)
    assert kernel.conflicts(first, short_clearance, 500)


@pytest.mark.parametrize("polygon", [
    rectangle(reverse=True),
    rectangle(duplicate=True),
    canonical_path([(-10, -10), (5_000, -10), (5_000, 2_000), (-10, 2_000)]),
    canonical_path([(0, 0), (8_000, 0), (8_000, 8_000), (4_000, 3_000), (0, 8_000)]),
    canonical_path([(0, 0), (10_000, 0), (10_000, 1), (9_999, 1), (9_999, 5_000), (0, 5_000)]),
])
def test_canonicalization_and_rotation_preserve_area_without_crash(polygon):
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    rotated = transform_and_normalize(polygon, 180)
    assert kernel.area_units2(polygon) == kernel.area_units2(rotated)
    assert kernel.perimeter_units(polygon) == pytest.approx(kernel.perimeter_units(rotated))


def test_independent_validator_accepts_valid_and_rejects_overlap_and_duplicates():
    first, second = piece(), piece("M_FRONT_002")
    marker_request = request((first, second), clearance=500)
    valid = (placement(first), placement(second, (10_500, 0), sequence=2))
    validator = IndependentMarkerValidator()
    assert validator.validate(marker_request, valid, 20_500).status == "VALIDATED"
    overlapped = (placement(first), placement(second, (9_999, 0), sequence=2))
    invalid = validator.validate(marker_request, overlapped, 20_000)
    assert invalid.status == "INVALID"
    assert not invalid.checks["clearance_satisfied"]
    duplicate = (placement(first), replace(placement(second, (10_500, 0), sequence=2), piece_instance_id=first.instance_id))
    assert not validator.validate(marker_request, duplicate, 20_500).checks["no_duplicate_instances"]
    outside = (placement(first, (-1, 0)), placement(second, (10_500, 0), sequence=2))
    assert not validator.validate(marker_request, outside, 20_500).checks["inside_valid_region"]
    unauthorized = (replace(placement(first), rotation=90), placement(second, (10_500, 0), sequence=2))
    assert not validator.validate(marker_request, unauthorized, 20_500).checks["transforms_allowed"]


@pytest.mark.parametrize(
    "marker_request,diagnostic",
    [
        (request((piece(rotations=()),), transforms=()), "no_allowed_transform"),
        (request((piece(polygon=rectangle(10_000, 60_000)),), width=50_000), "piece_exceeds_valid_region"),
        (request((piece(),), margins=MarkerMargins(0, 0, 60_000, 50_000)), "margins_leave_no_usable_length"),
        (request(tuple()), "composition_has_no_piece_instances"),
    ],
)
def test_known_impossibilities_are_proven_before_heuristic_search(marker_request, diagnostic):
    result = DeterministicNestingEngine().nest(marker_request)
    assert result.status == "PROVEN_INFEASIBLE"
    assert any(diagnostic in item for item in result.diagnostics)


def test_multiple_identical_pieces_are_deterministic_and_validated():
    marker_request = request(tuple(piece(f"M_FRONT_{index:03d}") for index in range(1, 5)))
    first = DeterministicNestingEngine().nest(marker_request)
    second = DeterministicNestingEngine().nest(marker_request)
    assert first.status == "VALIDATED_FEASIBLE"
    assert first.result_hash == second.result_hash
    assert first.placements == second.placements


def test_excessive_clearance_is_not_misrepresented_as_a_proof():
    two = (piece(), piece("M_FRONT_002"))
    clearance_result = DeterministicNestingEngine().nest(request(two, clearance=50_000, width=25_000, length=25_000))
    assert clearance_result.status == "SEARCH_EXHAUSTED"
    crowded = tuple(piece(f"M_FRONT_{index:03d}", rectangle(20_000, 20_000)) for index in range(1, 8))
    capacity_result = DeterministicNestingEngine().nest(request(crowded, clearance=0, width=50_000, length=50_000))
    assert capacity_result.status == "PROVEN_INFEASIBLE"
    assert "total_piece_area_exceeds_marker_capacity" in capacity_result.diagnostics


def test_translation_and_wider_fabric_preserve_a_known_valid_layout():
    first, second = piece(), piece("M_FRONT_002")
    marker_request = request((first, second), clearance=500, width=40_000)
    base = (placement(first), placement(second, (10_500, 0), sequence=2))
    validator = IndependentMarkerValidator()
    assert validator.validate(marker_request, base, 20_500).status == "VALIDATED"
    wider = replace(marker_request, usable_width=50_000, physical_width=50_000)
    assert validator.validate(wider, base, 20_500).status == "VALIDATED"
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    shifted = translate_path(first.piece.cut_polygon, 12_345, -6_789)
    assert kernel.area_units2(shifted) == kernel.area_units2(first.piece.cut_polygon)


def test_integer_nfp_rectangle_contact_and_cache_are_canonical():
    cache = GeometryOperationCache()
    kernel = IntegerGeometryKernel(PRECISION, cache)
    fixed, moving = rectangle(10_000, 10_000), rectangle(5_000, 5_000)
    first = kernel.nfp(fixed, moving, "A", 0, "B", 0, 0)
    second = kernel.nfp(fixed, moving, "A", 0, "B", 0, 0)
    assert first == second
    assert cache.hits >= 1
    # B at origin overlaps A; a clearly remote reference point does not.
    assert any(Polygon(path).covers(Point(0, 0)) for path in first)
    assert not any(Polygon(path).covers(Point(30_000, 30_000)) for path in first)


def test_integer_nfp_orientation_key_and_rectangular_ifp():
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    fixed = canonical_path([(0, 0), (12_000, 0), (12_000, 6_000), (0, 6_000)])
    moving = canonical_path([(0, 0), (4_000, 0), (2_000, 3_000)])
    zero = kernel.nfp(fixed, moving, "A", 0, "B", 0, 500)
    turned = kernel.nfp(fixed, transform_and_normalize(moving, 180), "A", 0, "B", 180, 500)
    assert zero != turned
    assert kernel.ifp_bounds(rectangle(10_000, 20_000), (1_000, 2_000, 50_000, 40_000)) == (1_000, 2_000, 40_000, 20_000)


def test_piece_reference_contract_is_single_for_zero_and_180():
    source = canonical_path([(5, 7), (25, 7), (20, 30), (5, 30)])
    position = (100, 200)
    assert PIECE_REFERENCE_POINT == (0, 0)
    for rotation in (0, 180):
        local = transform_and_normalize(source, rotation)
        marker = transform_piece(source, rotation, position)
        assert marker == translate_path(local, *position)
        assert path_bbox(local)[:2] == PIECE_REFERENCE_POINT
        assert path_bbox(marker)[:2] == position


def test_integer_boundary_intersections_and_component_canonicalization():
    horizontal = canonical_path([(0, 0), (10, 0), (10, 2), (0, 2)])
    vertical = canonical_path([(4, -5), (6, -5), (6, 5), (4, 5)])
    assert set(boundary_intersections(horizontal, vertical)) == {(4, 0), (6, 0), (4, 2), (6, 2)}
    assert canonical_paths((vertical, horizontal)) == canonical_paths((horizontal, vertical))


def test_candidate_space_uses_common_marker_space_and_deduplicates_sources():
    kernel = IntegerGeometryKernel(PRECISION, GeometryOperationCache())
    engine = CandidateSpaceEngine()
    square = rectangle(10_000, 10_000)
    result = engine.build(kernel, square, "B", 0, (0, 0, 40_000, 20_000), 500, (
        PlacedGeometry(square, "A1", 0, (0, 0)),
        PlacedGeometry(square, "A2", 0, (22_000, 0)),
    ))
    assert result.ifp_geometry == ((0, 0), (30_000, 0), (30_000, 10_000), (0, 10_000))
    assert result.forbidden_union
    assert result.feasible_space
    assert len({row.position for row in result.candidates}) == len(result.candidates)
    assert any("NFP_IFP_INTERSECTION" in row.sources for row in result.candidates)


@pytest.mark.parametrize("mode", ("LEGACY", "NFP_VERTEX_ONLY", "CANDIDATE_SPACE"))
def test_candidate_modes_remain_validator_gated(mode):
    first, second = piece(), piece("M_FRONT_002")
    result = DeterministicNestingEngine(candidate_mode=mode).nest(request((first, second), clearance=500))
    assert result.status == "VALIDATED_FEASIBLE"
    assert result.validation.status == "VALIDATED"
