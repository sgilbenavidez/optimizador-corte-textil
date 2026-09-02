import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.infrastructure.database import Base, get_db_session
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.main import create_app


@pytest.fixture()
def database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed_catalog(session)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client(database):
    app = create_app()

    def override_session():
        session = database()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def catalog_ids(client):
    models = client.get("/api/v1/garment-models").json()
    fabrics = client.get("/api/v1/fabric-configurations").json()
    tables = client.get("/api/v1/cutting-table-configurations").json()
    return {
        "version": models[0]["versions"][0]["id"],
        "fabric": fabrics[0]["id"],
        "table": tables[0]["id"],
    }

