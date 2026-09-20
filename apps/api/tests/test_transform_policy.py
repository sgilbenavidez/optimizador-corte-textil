from dataclasses import replace

from costura_optima.domain.integer_kernel import canonical_path, path_bbox, transform_and_normalize, translate_path
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import MarkerMargins, MarkerRequest, NestingPiece, PieceInstance, Placement
from costura_optima.domain.transform_policy import EffectiveTransformResolver, STRAIGHT_GRAIN_TWO_WAY


def test_two_way_non_directional_keeps_0_and_180_even_with_face_one_way():
    resolver = EffectiveTransformResolver("NON_DIRECTIONAL", "TWO_WAY", "FACE_ONE_WAY")
    assert resolver.resolve((0, 90, 180, 270), STRAIGHT_GRAIN_TWO_WAY) == (0, 180)


def test_directional_fabric_and_one_way_grain_are_fail_closed():
    resolver = EffectiveTransformResolver("ONE_WAY_PRINT", "TWO_WAY", "FACE_ONE_WAY")
    assert resolver.resolve((0, 180), STRAIGHT_GRAIN_TWO_WAY) == (0,)
    assert EffectiveTransformResolver().resolve((0, 90, 180, 270), "STRAIGHT_GRAIN_ONE_WAY") == (0,)


def test_independent_validator_rejects_90_for_two_way_straight_grain():
    polygon = canonical_path(((0, 0), (10_000, 0), (10_000, 20_000), (0, 20_000)))
    piece = NestingPiece("piece", "M", "FRONT", polygon, ((5_000, 1_000), (5_000, 19_000)), (0, 90, 180, 270), False, "hash")
    instance = PieceInstance("M_FRONT_001", piece)
    request = MarkerRequest("orientation-negative", (instance,), 50_000, 50_000, 100_000, 0, MarkerMargins(0, 0, 0, 0), (0, 90, 180, 270))
    placement = Placement(instance.instance_id, piece.pattern_piece_id, "M", "FRONT", 90, False, (0, 0), polygon,
                          ((0, 0), (0, 0)), path_bbox(polygon), "hash", 1)
    report = IndependentMarkerValidator().validate(request, (placement,), 20_000)
    assert report.status == "INVALID"
    assert not report.checks["transforms_allowed"]
