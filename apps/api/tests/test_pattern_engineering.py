from copy import deepcopy
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from costura_optima.domain.geometry import flatten_path, path_length, polygon_metrics
from costura_optima.infrastructure.db_models import PatternPieceORM, PatternSetVersionORM
from costura_optima.infrastructure.seed_data import BODY_CODES, BODY_TABLE, GARMENT_CODES, GARMENT_TABLE
from costura_optima.patterns.generator import EngineeringPatternGenerator, validate_compatibility
from costura_optima.patterns.persistence import generate_and_persist
from costura_optima.patterns.profile import ENGINEERING_PROFILE


SIZES = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]


def measurements(size_code: str) -> dict[str, float]:
    result = dict(zip(BODY_CODES, BODY_TABLE[size_code]))
    result.update(dict(zip(GARMENT_CODES, GARMENT_TABLE[size_code])))
    return result


@pytest.mark.parametrize("size_code", SIZES)
def test_all_sizes_have_four_valid_non_rectangular_engineering_pieces(size_code):
    generator = EngineeringPatternGenerator(ENGINEERING_PROFILE)
    pieces = generator.generate_size(size_code, SIZES.index(size_code), measurements(size_code))
    assert [(piece.piece_code, piece.quantity) for piece in pieces] == [
        ("FRONT", 1), ("BACK", 1), ("SLEEVE", 2), ("NECKBAND", 1)
    ]
    validate_compatibility(pieces, ENGINEERING_PROFILE["compatibility_tolerance_cm"])
    for piece in pieces:
        seam = Polygon(piece.seamline_geometry["coordinates"][0])
        cut = Polygon(piece.cutline_geometry["coordinates"][0])
        assert seam.is_valid and seam.area > 0
        assert cut.is_valid and cut.covers(seam)
        assert piece.allowed_rotations_degrees == [0, 180]
        assert piece.mirror_allowed is False
        assert all(isinstance(value, int) for point in piece.operational_geometry["coordinates"][0] for value in point)
    assert any(segment["kind"] == "CUBIC_BEZIER" for segment in pieces[0].source_geometry["segments"])
    assert any(segment["kind"] == "CUBIC_BEZIER" for segment in pieces[2].source_geometry["segments"])


@pytest.mark.parametrize("size_code", SIZES)
def test_front_back_seams_and_sleeve_neckband_relationships(size_code):
    generator = EngineeringPatternGenerator(ENGINEERING_PROFILE)
    pieces = {piece.piece_code: piece for piece in generator.generate_size(size_code, SIZES.index(size_code), measurements(size_code))}
    for edge in ("SIDE", "SHOULDER"):
        front = sum(
            path_length([generator._body_segments("FRONT", SIZES.index(size_code), measurements(size_code))[i]], .01)
            for i, segment in enumerate(generator._body_segments("FRONT", SIZES.index(size_code), measurements(size_code))) if segment.edge == edge
        )
        back = sum(
            path_length([generator._body_segments("BACK", SIZES.index(size_code), measurements(size_code))[i]], .01)
            for i, segment in enumerate(generator._body_segments("BACK", SIZES.index(size_code), measurements(size_code))) if segment.edge == edge
        )
        assert front == pytest.approx(back, abs=0.001)
    sleeve = pieces["SLEEVE"].technical_measurements if hasattr(pieces["SLEEVE"], "technical_measurements") else pieces["SLEEVE"].measurements
    assert sleeve["sleeve_cap_actual_cm"] == pytest.approx(sleeve["sleeve_cap_target_cm"], abs=.01)
    neck = pieces["NECKBAND"].measurements
    assert neck["neckband_seam_length_cm"] == pytest.approx(neck["actual_neckline_cm"] * .85, abs=.001)


def test_grading_is_monotonic_and_front_back_are_different():
    generator = EngineeringPatternGenerator(ENGINEERING_PROFILE)
    areas = []
    for index, size_code in enumerate(SIZES):
        pieces = {piece.piece_code: piece for piece in generator.generate_size(size_code, index, measurements(size_code))}
        areas.append(pieces["FRONT"].metrics["seamline"]["area_cm2"])
        assert pieces["FRONT"].geometry_hash != pieces["BACK"].geometry_hash
    assert areas == sorted(areas)


def test_curve_flattening_converges_and_hash_is_deterministic():
    generator = EngineeringPatternGenerator(ENGINEERING_PROFILE)
    segments = generator._body_segments("FRONT", 2, measurements("M"))
    values = []
    for tolerance in (.04, .02, .01):
        points, _ = flatten_path(segments, tolerance)
        values.append(polygon_metrics([[point.x, point.y] for point in points]))
    assert abs(values[1]["area_cm2"] - values[2]["area_cm2"]) < .2
    assert abs(values[1]["perimeter_cm"] - values[2]["perimeter_cm"]) < .05
    first = generator.generate_size("M", 2, measurements("M"))
    second = generator.generate_size("M", 2, measurements("M"))
    assert [piece.geometry_hash for piece in first] == [piece.geometry_hash for piece in second]


def test_persistence_is_idempotent_and_exports_required_svg(database):
    tmp_path = Path(".test_artifacts")
    with database() as session:
        first, created = generate_and_persist(session, tmp_path)
        first_id = first.id
    with database() as session:
        second, created_again = generate_and_persist(session, tmp_path)
        assert second.id == first_id
        assert session.query(PatternSetVersionORM).count() == 1
        assert session.query(PatternPieceORM).count() == 28
    assert created is True and created_again is False
    assert {path.name for path in tmp_path.glob("*.svg")} == {"pattern-XS.svg", "pattern-M.svg", "pattern-XL.svg", "pattern-XXXL.svg"}


def test_changed_parameter_hash_would_change_geometry():
    changed = deepcopy(ENGINEERING_PROFILE)
    changed["body"]["front_neck_depth_base_cm"] += .5
    original = EngineeringPatternGenerator(ENGINEERING_PROFILE).generate_size("M", 2, measurements("M"))[0]
    modified = EngineeringPatternGenerator(changed).generate_size("M", 2, measurements("M"))[0]
    assert original.geometry_hash != modified.geometry_hash


def test_pattern_api_and_order_snapshot_freeze(client, catalog_ids, database):
    old_order = client.post("/api/v1/production-orders", json={
        "garment_model_version_id": catalog_ids["version"], "fabric_configuration_id": catalog_ids["fabric"],
        "cutting_table_configuration_id": catalog_ids["table"], "demand": [{"size_code": "M", "quantity": 1}],
    }).json()
    assert old_order["pattern_set_version_id"] is None
    with database() as session:
        pattern_set, _ = generate_and_persist(session)
        set_id = pattern_set.id
    sets = client.get("/api/v1/pattern-sets").json()
    assert sets[0]["warning"] == "Patrón experimental de ingeniería — no validado para producción."
    assert sets[0]["validation_status"] == "UNVALIDATED_FOR_PRODUCTION"
    selected = client.get(f"/api/v1/pattern-sets/{set_id}?size_code=M").json()
    assert len(selected["pieces"]) == 4
    geometry = client.get(f"/api/v1/pattern-pieces/{selected['pieces'][0]['id']}/geometry").json()
    assert geometry["source_geometry"]["type"] == "STRUCTURED_PATH"
    new_order = client.post("/api/v1/production-orders", json={
        "garment_model_version_id": catalog_ids["version"], "fabric_configuration_id": catalog_ids["fabric"],
        "cutting_table_configuration_id": catalog_ids["table"], "demand": [{"size_code": "M", "quantity": 1}],
    }).json()
    assert new_order["pattern_set_version_id"] == set_id
    assert client.get(f"/api/v1/production-orders/{old_order['id']}").json()["pattern_set_version_id"] is None
