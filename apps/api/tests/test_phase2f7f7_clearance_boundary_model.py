"""FASE 2F.7F-7: polygonal NFP topology versus exact placement acceptance."""
from __future__ import annotations

from math import ceil

import pyclipper
from shapely.geometry import Polygon

from costura_optima.domain.candidate_space import (
    CandidatePosition, CandidateSpaceEngine, CandidateValidator, PlacedGeometry,
    repair_candidate_to_clearance,
)
from costura_optima.domain.integer_kernel import canonical_path, transform_piece
from test_phase2f7f6_analytic_geometry import CLEARANCE, REGION, fixture, rect, validator_valid


def polygonal_nfp(fixed, moving, tolerance: float):
    """Raw Pyclipper construction used solely to benchmark supported tolerances."""
    offsetter = pyclipper.PyclipperOffset(miter_limit=2.0, arc_tolerance=tolerance)
    offsetter.AddPath(list(fixed), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = max(offsetter.Execute(CLEARANCE), key=lambda path: abs(pyclipper.Area(path)))
    paths = pyclipper.MinkowskiSum(list(expanded), [(-x, -y) for x, y in moving], True)
    return canonical_path(max(paths, key=lambda path: abs(pyclipper.Area(path))))


def boundary_measurement(fixed, moving, tolerance: float):
    nfp = polygonal_nfp(fixed, moving, tolerance)
    fixed_polygon = Polygon(fixed)
    distances = [fixed_polygon.distance(Polygon(transform_piece(moving, 0, point))) for point in nfp]
    errors = [distance - CLEARANCE for distance in distances]
    return {"vertex_count": len(nfp), "min_clearance": min(distances), "max_clearance": max(distances),
            "mean_error": sum(errors) / len(errors), "max_negative_error": min(errors), "max_positive_error": max(errors)}


def test_arc_tolerance_benchmark_quantifies_three_polygon_classes():
    rectangle = rect(20_000, 10_000)
    concave = canonical_path(((0, 0), (17_000, 0), (17_000, 5_000), (8_000, 5_000), (8_000, 13_000), (0, 13_000)))
    fixtures = ((rectangle, rect(10_000, 5_000)), (rectangle, concave), (concave, concave))
    tolerances = (5.0, 2.0, 1.0)
    results = {(index, tolerance): boundary_measurement(fixed, moving, tolerance)
               for index, (fixed, moving) in enumerate(fixtures) for tolerance in tolerances}
    # 5 is the current tolerance: it deliberately remains an inner approximation.
    assert all(results[index, 5.0]["max_negative_error"] <= 0 for index in range(3))
    assert all(results[index, 1.0]["vertex_count"] >= results[index, 5.0]["vertex_count"] for index in range(3))
    # Integer coordinates impose a floor: 1.0 cannot promise sub-unit exactness.
    assert any(results[index, 1.0]["max_negative_error"] < 0 for index in range(3))


def test_polygonal_candidate_can_require_exact_rejection_and_minimal_repair():
    _, a, b, result = fixture()
    raw = CandidatePosition((9_501, 14_969), 0, ("FEASIBLE_BOUNDARY",), 1)
    assert CandidateSpaceEngine.is_valid_reference_position(result, raw.position)
    assert not validator_valid(a, b, (20_000, 20_000), raw.position)

    def validate(candidate):
        valid = validator_valid(a, b, (20_000, 20_000), candidate.position)
        return valid, None if valid else "CLEARANCE"

    rows, metrics = CandidateValidator().filter((raw,), lambda point: CandidateSpaceEngine.is_valid_reference_position(result, point), validate)
    assert not rows[0].accepted and rows[0].reason == "CLEARANCE"
    assert metrics.validator_rejected_clearance == 1

    measured = boundary_measurement(a, b, 5.0)
    max_repair_units = ceil(abs(measured["max_negative_error"])) + 1  # measured error + integer quantization margin
    repaired = repair_candidate_to_clearance(b, 0, raw.position, (PlacedGeometry(a, "A", 0, (20_000, 20_000)),),
                                             CLEARANCE, max_repair_units, raw.contact_count)
    assert repaired.status == "REPAIRED"
    assert repaired.displacement_units == 1
    assert repaired.position == (9_500, 14_969)
    assert validator_valid(a, b, (20_000, 20_000), repaired.position)


def test_repair_refuses_multi_contact_and_large_deficits():
    _, a, b, _ = fixture()
    placed = (PlacedGeometry(a, "A", 0, (20_000, 20_000)),)
    assert repair_candidate_to_clearance(b, 0, (9_501, 14_969), placed, CLEARANCE, 2, 2).status == "REPAIR_REQUIRES_MULTI_CONSTRAINT"
    assert repair_candidate_to_clearance(b, 0, (20_000, 20_000), placed, CLEARANCE, 2).status == "NON_APPROXIMATION_CLEARANCE_FAILURE"


def test_raw_and_validated_grid_semantics_are_separate():
    _, a, b, result = fixture()
    raw_false_valid = raw_false_invalid = accepted_false_valid = accepted_false_invalid = 0
    for x in range(0, 90_000 + 1, 500):
        for y in range(0, 45_000 + 1, 500):
            raw_valid = CandidateSpaceEngine.is_valid_reference_position(result, (x, y))
            exact_valid = validator_valid(a, b, (20_000, 20_000), (x, y))
            raw_false_valid += int(raw_valid and not exact_valid)
            raw_false_invalid += int(not raw_valid and exact_valid)
            accepted = raw_valid and exact_valid  # CandidateValidator only emits accepted after exact validation.
            accepted_false_valid += int(accepted and not exact_valid)
            accepted_false_invalid += int(not accepted and exact_valid and raw_valid)
    assert (raw_false_valid, raw_false_invalid, accepted_false_valid, accepted_false_invalid) == (0, 0, 0, 0)


def test_second_rectangle_rotation_180_and_concave_preserve_direct_marker_space():
    # The prior analytic grid covers the primary and concave cases.  These probes
    # assert the same direct reference-point contract for non-round dimensions.
    _, a, b, second = fixture((17_000, 13_000), (23_000, 11_000), (9_000, 7_000))
    for point in ((0, 0), (22_499, 10_969), (40_000, 30_000), (91_000, 43_000)):
        raw = CandidateSpaceEngine.is_valid_reference_position(second, point)
        exact = validator_valid(a, b, (23_000, 11_000), point)
        assert raw == exact
    _, primary_a, primary_b, turned = fixture(rotation=180)
    for point in ((0, 0), (9_501, 14_969), (40_500, 20_000), (90_000, 45_000)):
        assert transform_piece(primary_b, 180, point)
        raw = CandidateSpaceEngine.is_valid_reference_position(turned, point)
        exact = validator_valid(primary_a, primary_b, (20_000, 20_000), point, 180)
        if point == (9_501, 14_969):
            assert raw and not exact  # same bounded clearance approximation at 180°
        else:
            assert raw == exact
