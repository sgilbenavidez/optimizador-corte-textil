from sqlalchemy import func, select

from costura_optima.infrastructure.db_models import MeasurementValueORM, SizeDefinitionORM
from costura_optima.infrastructure.seed_data import seed_catalog


def test_catalog_is_loaded_from_backend(client):
    models = client.get("/api/v1/garment-models")
    assert models.status_code == 200
    model = models.json()[0]
    assert model["code"] == "TSHIRT_REGULAR_STRAIGHT_ADULT"
    version = model["versions"][0]
    assert version["version_code"] == "TSHIRT-REGULAR-STRAIGHT-ADULT-v0"
    assert version["lifecycle_status"] == "ENGINEERING"
    assert version["warning"] == "Patrón experimental de ingeniería — no validado para producción."

    sizes = client.get(f"/api/v1/garment-model-versions/{version['id']}/sizes")
    assert sizes.status_code == 200
    assert [item["code"] for item in sizes.json()] == ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]
    assert all(item["measurements"] for item in sizes.json())


def test_cutting_resources_match_approved_configuration(client):
    fabric = client.get("/api/v1/fabric-configurations").json()[0]
    table = client.get("/api/v1/cutting-table-configurations").json()[0]
    assert fabric["physical_width_cm"] == 180
    assert fabric["usable_width_cm"] == 176
    assert fabric["piece_clearance_cm"] == 0.5
    assert fabric["directional"] is False
    assert table["physical_length_cm"] == 800
    assert table["usable_length_cm"] == 700
    assert table["max_layers"] == 30


def test_seed_is_idempotent(database):
    with database() as session:
        first = seed_catalog(session)
        second = seed_catalog(session)
        assert first == second
        assert session.scalar(select(func.count()).select_from(SizeDefinitionORM)) == 7
        assert session.scalar(select(func.count()).select_from(MeasurementValueORM)) == 91
