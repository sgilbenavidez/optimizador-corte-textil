import pytest
from sqlalchemy import select

from costura_optima.infrastructure.db_models import GarmentModelVersionORM


CASES = {
    "A": {"S": 3},
    "B": {"S": 3, "M": 20, "L": 10, "XL": 12, "XXL": 30},
    "C": {"XS": 1, "S": 1, "M": 1, "L": 1, "XL": 1, "XXL": 1, "XXXL": 1},
    "D": {"M": 100},
    "E": {"XXXL": 31},
}


def payload(ids, demand):
    return {
        "garment_model_version_id": ids["version"],
        "fabric_configuration_id": ids["fabric"],
        "cutting_table_configuration_id": ids["table"],
        "demand": [{"size_code": code, "quantity": quantity} for code, quantity in demand.items()],
    }


@pytest.mark.parametrize(("case_name", "demand"), CASES.items())
def test_required_cases_create_and_round_trip(case_name, demand, client, catalog_ids):
    response = client.post("/api/v1/production-orders", json=payload(catalog_ids, demand))
    assert response.status_code == 201, (case_name, response.text)
    created = response.json()
    assert created["status"] == "PREPARED_FOR_OPTIMIZATION"
    assert created["total_quantity"] == sum(demand.values())
    assert len(created["snapshot_hash"]) == 64
    reconstructed = {item["size_code"]: item["quantity"] for item in created["demand"]}
    assert list(reconstructed) == ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]
    for size_code in reconstructed:
        assert reconstructed[size_code] == demand.get(size_code, 0)

    fetched = client.get(f"/api/v1/production-orders/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


@pytest.mark.parametrize(
    ("name", "demand"),
    [
        ("EMPTY", {"XS": 0, "S": 0}),
        ("NEGATIVE", {"M": -1}),
        ("DECIMAL", {"L": 1.5}),
        ("UNKNOWN", {"XXXXL": 10}),
    ],
)
def test_invalid_cases_are_rejected(name, demand, client, catalog_ids):
    response = client.post("/api/v1/production-orders", json=payload(catalog_ids, demand))
    assert response.status_code == 422, (name, response.text)


def test_duplicate_size_is_rejected(client, catalog_ids):
    request = payload(catalog_ids, {})
    request["demand"] = [{"size_code": "M", "quantity": 1}, {"size_code": "M", "quantity": 2}]
    response = client.post("/api/v1/production-orders", json=request)
    assert response.status_code == 422


def test_missing_configuration_is_rejected(client, catalog_ids):
    request = payload(catalog_ids, {"S": 3})
    request["fabric_configuration_id"] = "00000000-0000-0000-0000-000000000000"
    response = client.post("/api/v1/production-orders", json=request)
    assert response.status_code == 404


def test_non_orderable_model_is_rejected(client, catalog_ids, database):
    with database() as session:
        version = session.get(GarmentModelVersionORM, catalog_ids["version"])
        version.is_orderable = False
        session.commit()
    response = client.post("/api/v1/production-orders", json=payload(catalog_ids, {"S": 3}))
    assert response.status_code == 422


def test_order_snapshot_is_immutable_when_catalog_changes(client, catalog_ids, database):
    created = client.post("/api/v1/production-orders", json=payload(catalog_ids, {"S": 3})).json()
    original_name = created["garment_model"]["display_name"]
    original_hash = created["snapshot_hash"]

    with database() as session:
        version = session.scalar(select(GarmentModelVersionORM).where(GarmentModelVersionORM.id == catalog_ids["version"]))
        version.display_name = "Nombre nuevo que no debe afectar historia"
        session.commit()

    fetched = client.get(f"/api/v1/production-orders/{created['id']}").json()
    assert fetched["garment_model"]["display_name"] == original_name
    assert fetched["snapshot_hash"] == original_hash


def test_order_snapshot_contains_approved_future_policy(client, catalog_ids):
    order = client.post("/api/v1/production-orders", json=payload(catalog_ids, {"XXXL": 31})).json()
    policy = order["optimization_policy"]
    assert policy["allow_overproduction"] is True
    assert policy["max_overproduction_rule"] == "max(2, ceil(demand * 0.03))"
    assert policy["time_limit_seconds"] == 120
    assert policy["timeout_feasible_status"] == "FEASIBLE_NOT_PROVEN_BEST"
