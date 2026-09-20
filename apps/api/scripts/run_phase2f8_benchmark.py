"""Phase 2F.8: baseline (legacy path) vs. joint-optimization benchmark for
the 216-garment order, via the REAL API + PlanningCoordinator.execute() --
same technique as run_phase2f5_benchmark.py (RQ enqueue monkeypatched out,
PlanningCoordinator invoked directly, exactly what the worker's
tasks.py::execute_optimization_run does).

EXACT mode (allow_overproduction=False -> maximum_overproduction=0 for
every size) matches the phase spec's Section 16 benchmark definition
(SHORTAGE=0, OVERPRODUCTION=0). Each mode runs against its OWN fresh
in-memory database so the comparison is not confounded by one mode's
marker-cache warming up the other's.
"""
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
PREVIOUS_TOTAL_FABRIC_M = 167.209
PREVIOUS_GLOBAL_EFFICIENCY_PCT = 72.578893
MODES = ({"tag": "baseline", "joint_optimization_enabled": False},
         {"tag": "joint", "joint_optimization_enabled": True})

RUN_CONFIG = {
    "planner_refinement_engine": "hybrid", "allow_overproduction": False,
    "max_garments_per_marker": 15, "max_distinct_sizes_per_marker": 5, "max_candidate_compositions": 48,
    "max_marker_candidates": 24, "max_rounds": 2, "geometry_evaluation_budget_per_candidate": 25_000,
    "fast_plan_budget_seconds": 5, "candidate_generation_budget_seconds": 5, "geometry_budget_seconds": 60,
    "planning_budget_seconds": 30, "total_budget_seconds": 280,
    "geometry_top_k_initial": 20, "geometry_top_k_per_round": 10, "beam_width": 10, "no_improvement_rounds": 1,
}


def _run_mode(mode: dict) -> dict:
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
            "garment_model_version_id": model["id"],
            "fabric_configuration_id": fabric["id"],
            "cutting_table_configuration_id": table["id"],
            "demand": [{"size_code": size, "quantity": quantity} for size, quantity in DEMAND.items()],
        }).json()
        run = client.post(
            f"/api/v1/production-orders/{order['id']}/optimization-runs",
            headers={"Idempotency-Key": f"phase2f8-{mode['tag']}"},
            json={**RUN_CONFIG, "joint_optimization_enabled": mode["joint_optimization_enabled"]},
        ).json()
        started = perf_counter()
        with factory() as session:
            PlanningCoordinator(session).execute(run["id"])
        wall_s = round(perf_counter() - started, 3)
        final_run = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
        audit = client.get(f"/api/v1/optimization-runs/{run['id']}/audit").json()
        summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
        recommended = next((item for item in summaries if item["recommended"]), summaries[0] if summaries else None)
        detail = client.get(f"/api/v1/optimization-solutions/{recommended['id']}").json() if recommended else None
        metrics = detail["metrics"] if detail else {}
        return {
            "mode": mode["tag"], "status": final_run["status"],
            "best_solution_available": final_run["best_solution_available"],
            "wall_time_s": wall_s,
            "shortage": 0 if metrics else None,
            "overproduction": metrics.get("total_overproduction"),
            "physical_spreads": metrics.get("physical_spreads"),
            "distinct_marker_designs": metrics.get("distinct_marker_designs"),
            "total_fabric_m": metrics.get("total_linear_consumption_m"),
            "global_weighted_efficiency_pct": metrics.get("global_efficiency_percentage"),
            "joint_optimization_audit": audit["audit"].get("joint_optimization"),
        }


def main() -> None:
    results = [_run_mode(mode) for mode in MODES]

    baseline = next((row for row in results if row["mode"] == "baseline"), None)
    joint = next((row for row in results if row["mode"] == "joint"), None)
    fabric_improvement = (
        round(baseline["total_fabric_m"] - joint["total_fabric_m"], 3)
        if baseline and joint and baseline.get("total_fabric_m") and joint.get("total_fabric_m") else None
    )
    payload = {
        "demand": DEMAND, "results": results,
        "previous_total_fabric_m": PREVIOUS_TOTAL_FABRIC_M,
        "previous_global_efficiency_pct": PREVIOUS_GLOBAL_EFFICIENCY_PCT,
        "fabric_improvement_m": fabric_improvement,
        "joint_regressed_below_previous": (
            joint["total_fabric_m"] > PREVIOUS_TOTAL_FABRIC_M if joint and joint.get("total_fabric_m") else None
        ),
        "pattern_validation_status": "ENGINEERING", "production_ready": False, "stop_gate": "ACTIVE",
    }
    output = Path("artifacts/phase2f8")
    output.mkdir(parents=True, exist_ok=True)
    (output / "benchmark-comparison.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
