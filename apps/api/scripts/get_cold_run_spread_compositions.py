"""One-shot helper: run the cold 216-benchmark once and dump the winning
solution's spread compositions ranked by fabric contribution -- feeds
Sections 14-16's ALNS budget experiment with the SAME markers the live
system actually selected, without re-deriving them by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator
from costura_optima.infrastructure.database import Base, get_db_session
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.main import create_app
from costura_optima.patterns.persistence import generate_and_persist

DEMAND = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
RUN_CONFIG = {
    "planner_refinement_engine": "hybrid", "allow_overproduction": False, "joint_optimization_enabled": False,
    "persistent_catalog_bootstrap_enabled": False,
    "max_garments_per_marker": 15, "max_distinct_sizes_per_marker": 5, "max_candidate_compositions": 48,
    "max_marker_candidates": 24, "max_rounds": 2, "geometry_evaluation_budget_per_candidate": 25_000,
    "fast_plan_budget_seconds": 5, "candidate_generation_budget_seconds": 5, "geometry_budget_seconds": 60,
    "planning_budget_seconds": 30, "total_budget_seconds": 280,
    "geometry_top_k_initial": 20, "geometry_top_k_per_round": 10, "beam_width": 10, "no_improvement_rounds": 1,
}


def main():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed_catalog(session)
        generate_and_persist(session)
    app = create_app()

    def override():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db_session] = override
    services.enqueue_optimization_run = lambda run_id, timeout: run_id
    with TestClient(app) as client:
        model = client.get("/api/v1/garment-models").json()[0]["versions"][0]
        fabric = client.get("/api/v1/fabric-configurations").json()[0]
        table = client.get("/api/v1/cutting-table-configurations").json()[0]
        order = client.post("/api/v1/production-orders", json={
            "garment_model_version_id": model["id"], "fabric_configuration_id": fabric["id"],
            "cutting_table_configuration_id": table["id"],
            "demand": [{"size_code": size, "quantity": quantity} for size, quantity in DEMAND.items()],
        }).json()
        run = client.post(
            f"/api/v1/production-orders/{order['id']}/optimization-runs",
            headers={"Idempotency-Key": "phase2f8-1-spread-dump"}, json=RUN_CONFIG,
        ).json()
        with factory() as session:
            PlanningCoordinator(session).execute(run["id"])
        summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
        recommended = next((item for item in summaries if item["recommended"]), summaries[0])
        detail = client.get(f"/api/v1/optimization-solutions/{recommended['id']}").json()

    spreads = detail["spreads"]
    ranked = sorted(spreads, key=lambda s: -(s["layers"] * s["repeats"] * s["marker_length_cm"]))
    output = [{"composition": s["composition"], "layers": s["layers"], "repeats": s["repeats"],
               "marker_hash": s["marker_hash"], "marker_length_cm": s["marker_length_cm"],
               "fabric_contribution_cm": s["layers"] * s["repeats"] * s["marker_length_cm"]} for s in ranked]
    Path("artifacts/phase2f8_1").mkdir(parents=True, exist_ok=True)
    Path("artifacts/phase2f8_1/cold-run-spread-compositions.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
