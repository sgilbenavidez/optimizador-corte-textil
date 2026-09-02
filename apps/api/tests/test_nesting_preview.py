import pytest

from costura_optima.patterns.persistence import generate_and_persist


def _generated_ids(database, client):
    with database() as session:
        pattern_set, _ = generate_and_persist(session)
        pattern_id = pattern_set.id
    fabrics = client.get("/api/v1/fabric-configurations").json()
    tables = client.get("/api/v1/cutting-table-configurations").json()
    return pattern_id, fabrics[0]["id"], tables[0]["id"]


def test_preview_m1_uses_real_pattern_geometry(database, client):
    pattern_id, fabric_id, table_id = _generated_ids(database, client)
    response = client.post(
        "/api/v1/geometry/markers/preview",
        json={
            "pattern_set_version_id": pattern_id,
            "fabric_configuration_id": fabric_id,
            "cutting_table_configuration_id": table_id,
            "composition": [{"size_code": "M", "quantity": 1}],
            "deterministic": True,
            "seed": 1,
            "evaluation_budget": 100_000,
        },
    )
    assert response.status_code == 200, response.text
    marker = response.json()
    assert marker["status"] == "VALIDATED_FEASIBLE", marker
    assert marker["validation"]["status"] == "VALIDATED"
    assert marker["piece_count"] == 5
    assert marker["marker_length_cm"] <= 700
    assert marker["efficiency_percentage"] > 0
    assert marker["nfp_status"] == "PARTIAL"
    assert len(marker["placements"]) == 5


def test_preview_m1_is_deterministic(database, client):
    pattern_id, fabric_id, table_id = _generated_ids(database, client)
    payload = {
        "pattern_set_version_id": pattern_id,
        "fabric_configuration_id": fabric_id,
        "cutting_table_configuration_id": table_id,
        "composition": [{"size_code": "M", "quantity": 1}],
        "deterministic": True,
        "seed": 7,
    }
    first = client.post("/api/v1/geometry/markers/preview", json=payload).json()
    second = client.post("/api/v1/geometry/markers/preview", json=payload).json()
    assert first["result_hash"] == second["result_hash"]
    assert first["placements"] == second["placements"]


@pytest.mark.parametrize(
    ("case_id", "composition", "expected_pieces"),
    [
        ("M1", [{"size_code": "M", "quantity": 1}], 5),
        ("M2", [{"size_code": "M", "quantity": 2}], 10),
        ("M3", [{"size_code": "S", "quantity": 1}, {"size_code": "M", "quantity": 1}], 10),
        ("M4", [{"size_code": "M", "quantity": 1}, {"size_code": "XL", "quantity": 1}], 10),
        (
            "M5",
            [{"size_code": size, "quantity": 1} for size in ("XS", "S", "M", "L", "XL", "XXL", "XXXL")],
            35,
        ),
        ("M6", [{"size_code": "XXXL", "quantity": 2}], 10),
    ],
)
def test_real_composition_cases(database, client, case_id, composition, expected_pieces):
    pattern_id, fabric_id, table_id = _generated_ids(database, client)
    response = client.post(
        "/api/v1/geometry/markers/preview",
        json={
            "pattern_set_version_id": pattern_id,
            "fabric_configuration_id": fabric_id,
            "cutting_table_configuration_id": table_id,
            "composition": composition,
            "deterministic": True,
            "seed": 1,
            "evaluation_budget": 100_000,
        },
    )
    assert response.status_code == 200, response.text
    marker = response.json()
    assert marker["status"] == "VALIDATED_FEASIBLE", (case_id, marker["status"], marker["diagnostics"])
    assert marker["validation"]["status"] == "VALIDATED"
    assert marker["piece_count"] == expected_pieces
