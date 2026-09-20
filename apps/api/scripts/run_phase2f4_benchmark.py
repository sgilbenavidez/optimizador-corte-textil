"""Run the mandatory 97-garment operational benchmark with real geometry."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator, _marker_from_payload
from costura_optima.domain.production_models import PlanningConfig
from costura_optima.domain.production_planner import ProductionPlanner
from costura_optima.infrastructure.database import Base, get_db_session
from costura_optima.infrastructure.db_models import MarkerArtifactORM, OptimizationCandidateORM
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.main import create_app
from costura_optima.patterns.persistence import generate_and_persist


DEMAND = {"XS": 20, "S": 10, "M": 30, "L": 20, "XL": 14, "XXL": 3, "XXXL": 0}


def main() -> None:
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
    output = Path("artifacts/phase2f4")
    output.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        model = client.get("/api/v1/garment-models").json()[0]["versions"][0]
        fabric = client.get("/api/v1/fabric-configurations").json()[0]
        table = client.get("/api/v1/cutting-table-configurations").json()[0]
        order = client.post("/api/v1/production-orders", json={
            "garment_model_version_id": model["id"], "fabric_configuration_id": fabric["id"],
            "cutting_table_configuration_id": table["id"],
            "demand": [{"size_code": size, "quantity": quantity} for size, quantity in DEMAND.items()],
        }).json()
        run = client.post(f"/api/v1/production-orders/{order['id']}/optimization-runs",
                          headers={"Idempotency-Key": "phase2f4-order-97"}, json={
                              "max_garments_per_marker": 15, "max_distinct_sizes_per_marker": 5,
                              "max_candidate_compositions": 48, "max_marker_candidates": 24,
                              "max_rounds": 2, "time_limit_seconds": 300,
                              "global_geometry_budget_seconds": 100,
                              "planner_time_limit_seconds": 30,
                          }).json()
        started = perf_counter()
        with factory() as session:
            PlanningCoordinator(session).execute(run["id"])
        calculation_ms = round((perf_counter() - started) * 1000, 3)
        summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
        recommended_summary = next(item for item in summaries if item["recommended"])
        recommended = client.get(f"/api/v1/optimization-solutions/{recommended_summary['id']}").json()
        primary = recommended["spreads"][0]
        svg = client.get(f"/api/v1/markers/{primary['marker_hash']}/svg").text
        (output / "primary-spread.svg").write_text(svg, encoding="utf-8")

        with factory() as session:
            candidates = list(session.scalars(select(OptimizationCandidateORM).where(
                OptimizationCandidateORM.optimization_run_id == run["id"],
                OptimizationCandidateORM.origin == "SINGLE_SIZE_FALLBACK",
                OptimizationCandidateORM.marker_hash.is_not(None),
            )))
            artifacts = {row.marker_hash: row for row in session.scalars(select(MarkerArtifactORM))}
            singles = tuple(replace(_marker_from_payload(artifacts[row.marker_hash]), origin=row.origin)
                            for row in candidates if row.marker_hash in artifacts)
        maximum = {size: max(2, round(quantity * .03)) for size, quantity in DEMAND.items()}
        baseline = next(solution for solution in ProductionPlanner(PlanningConfig(
            max_layers=30, time_limit_seconds=20,
        )).solve_profiles(DEMAND, maximum, singles) if "MAX_ORDER_PER_CUT" in solution.profiles)
        result = {
            "demand": DEMAND, "recommended_profile": "MAX_ORDER_PER_CUT",
            "before": {
                "physical_spreads": baseline.spread_count, "distinct_markers": baseline.marker_design_count,
                "changeovers": baseline.marker_change_count, "fabric_m": baseline.total_fabric_units / 100_000,
                "efficiency": baseline.global_efficiency_percentage,
                "primary_spread_coverage": baseline.primary_spread_coverage_percentage,
                "largest_marker_piece_count": baseline.max_pieces_per_marker,
            },
            "after": {
                "physical_spreads": recommended["metrics"]["physical_spreads"],
                "distinct_markers": recommended["metrics"]["distinct_marker_designs"],
                "changeovers": recommended["metrics"]["marker_changeovers"],
                "fabric_m": recommended["metrics"]["total_linear_consumption_m"],
                "efficiency": recommended["metrics"]["global_efficiency_percentage"],
                "primary_spread_coverage": recommended["metrics"]["primary_spread_coverage_percentage"],
                "largest_marker_piece_count": recommended["metrics"]["max_pieces_per_marker"],
                "calculation_time_ms": calculation_ms,
            },
            "primary_spread": primary, "run_id": run["id"], "solution_id": recommended["id"],
            "pattern_validation_status": "ENGINEERING", "production_ready": False,
            "stop_gate": "ACTIVE", "phase_2g_authorized": False,
        }
        (output / "benchmark-order-97.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
