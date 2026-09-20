"""Benchmark the mandatory 216-garment order in all local planner modes."""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

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
ENGINES = ("heuristic", "cp_sat", "hybrid")


def main() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
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
    results = []
    with TestClient(app) as client:
        model = client.get("/api/v1/garment-models").json()[0]["versions"][0]
        fabric = client.get("/api/v1/fabric-configurations").json()[0]
        table = client.get("/api/v1/cutting-table-configurations").json()[0]
        order = client.post("/api/v1/production-orders", json={
            "garment_model_version_id": model["id"],
            "fabric_configuration_id": fabric["id"],
            "cutting_table_configuration_id": table["id"],
            "demand": [{"size_code": size, "quantity": quantity} for size, quantity in DEMAND.items()],
        }).json()

        for planner_engine in ENGINES:
            run = client.post(
                f"/api/v1/production-orders/{order['id']}/optimization-runs",
                headers={"Idempotency-Key": f"phase2f5-large-{planner_engine}"},
                json={
                    "planner_refinement_engine": planner_engine,
                    "max_garments_per_marker": 15,
                    "max_distinct_sizes_per_marker": 5,
                    "max_candidate_compositions": 48,
                    "max_marker_candidates": 24,
                    "max_rounds": 2,
                    "geometry_evaluation_budget_per_candidate": 25_000,
                    "fast_plan_budget_seconds": 5,
                    "candidate_generation_budget_seconds": 5,
                    "geometry_budget_seconds": 45,
                    "planning_budget_seconds": 25,
                    "total_budget_seconds": 120,
                    "geometry_top_k_initial": 20,
                    "geometry_top_k_per_round": 10,
                    "beam_width": 10,
                    "no_improvement_rounds": 1,
                },
            ).json()
            started = perf_counter()
            with factory() as session:
                PlanningCoordinator(session).execute(run["id"])
            wall_ms = round((perf_counter() - started) * 1000, 3)
            final_run = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
            audit = client.get(f"/api/v1/optimization-runs/{run['id']}/audit").json()
            summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
            recommended = next((item for item in summaries if item["recommended"]), summaries[0] if summaries else None)
            detail = (client.get(f"/api/v1/optimization-solutions/{recommended['id']}").json()
                      if recommended else None)
            metrics = detail["metrics"] if detail else {}
            results.append({
                "engine": planner_engine,
                "status": final_run["status"],
                "best_solution_available": final_run["best_solution_available"],
                "first_feasible_time_ms": (final_run.get("incumbent") or {}).get("first_solution_elapsed_ms"),
                "final_time_ms": (final_run.get("incumbent") or {}).get("final_solution_elapsed_ms"),
                "wall_time_ms": wall_ms,
                "physical_spreads": metrics.get("physical_spreads"),
                "distinct_markers": metrics.get("distinct_marker_designs"),
                "fabric_m": metrics.get("total_linear_consumption_m"),
                "primary_spread_coverage_percentage": metrics.get("primary_spread_coverage_percentage"),
                "geometry_evaluations": final_run["progress"]["candidates_evaluated"],
                "cache_hits": audit["audit"].get("marker_cache_hits", sum(
                    1 for item in audit["candidates"] if item["cache_hit"]
                )),
                "candidate_counts": {
                    "generated": final_run["progress"]["candidates_generated"],
                    "evaluated": final_run["progress"]["candidates_evaluated"],
                    "not_evaluated": final_run["progress"]["candidates_not_evaluated"],
                    "feasible": final_run["progress"]["candidates_feasible"],
                    "infeasible": final_run["progress"]["candidates_infeasible"],
                },
                "funnel": audit["audit"].get("candidate_funnel", []),
            })

    payload = {
        "demand": DEMAND,
        "results": results,
        "all_modes_local": True,
        "external_optimization_services": False,
        "pattern_validation_status": "ENGINEERING",
        "production_ready": False,
        "stop_gate": "ACTIVE",
        "phase_2g_authorized": False,
    }
    output = Path("artifacts/phase2f5")
    output.mkdir(parents=True, exist_ok=True)
    (output / "large-order-benchmark.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
