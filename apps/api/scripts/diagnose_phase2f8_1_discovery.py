"""Phase 2F.8.1 Section 8/9: factual diagnostic (not a guess) of why the
live round loop does/doesn't generate, evaluate, and select the 4 known-good
compositions from 2F.7H-1/2/3 for the 216-garment benchmark.

Runs the order+run through PlanningCoordinator.execute() exactly like
run_phase2f8_benchmark.py, then inspects the /audit trail (rounds,
candidate_funnel, candidates_outside_budget, evaluated_candidates -- all
already recorded by the existing round loop, no new instrumentation needed)
for M2+L1, S3+XXL5, XS3+XL3, XL1 specifically.
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
TARGETS = {
    "M2+L1": {"L": 1, "M": 2},
    "S3+XXL5": {"S": 3, "XXL": 5},
    "XS3+XL3": {"XL": 3, "XS": 3},
    "XL1": {"XL": 1},
}
RUN_CONFIG = {
    "planner_refinement_engine": "hybrid", "allow_overproduction": False, "joint_optimization_enabled": False,
    "max_garments_per_marker": 15, "max_distinct_sizes_per_marker": 5, "max_candidate_compositions": 48,
    "max_marker_candidates": 24, "max_rounds": 2, "geometry_evaluation_budget_per_candidate": 25_000,
    "fast_plan_budget_seconds": 5, "candidate_generation_budget_seconds": 5, "geometry_budget_seconds": 60,
    "planning_budget_seconds": 30, "total_budget_seconds": 280,
    "geometry_top_k_initial": 20, "geometry_top_k_per_round": 10, "beam_width": 10, "no_improvement_rounds": 1,
}


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
            headers={"Idempotency-Key": "phase2f8-1-discovery-trace"}, json=RUN_CONFIG,
        ).json()
        with factory() as session:
            PlanningCoordinator(session).execute(run["id"])
        final_run = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
        audit = client.get(f"/api/v1/optimization-runs/{run['id']}/audit").json()["audit"]

    rounds = audit.get("rounds", [])
    pruning = audit.get("candidate_pruning", [])
    outside_budget = audit.get("candidates_outside_budget", [])
    distribution = audit.get("candidate_distribution", {})

    trace = {}
    for tag, composition in TARGETS.items():
        row = {"generated": False, "round": None, "prefiltered": None, "prefilter_reason": None,
               "geometry_evaluated": False, "geometry_result": None, "persisted": False,
               "excluded_from_geometry_top_k": False, "exclusion_reason": None, "planner_selected": None}
        for prune in pruning:
            if prune.get("composition") == composition:
                row["prefiltered"] = True
                row["prefilter_reason"] = prune.get("reason")
        for excl in outside_budget:
            if excl.get("composition") == composition:
                row["generated"] = True
                row["round"] = excl.get("round")
                row["excluded_from_geometry_top_k"] = True
                row["exclusion_reason"] = excl.get("reason")
        for round_entry in rounds:
            for candidate in round_entry.get("evaluated_candidates", []):
                if candidate.get("composition") == composition:
                    row["generated"] = True
                    row["round"] = round_entry["round_number"]
                    row["geometry_evaluated"] = True
        trace[tag] = row

    output = {
        "demand": DEMAND, "final_status": final_run["status"],
        "rounds_summary": [{"round_number": r["round_number"], "generated": r["generated"],
                            "distribution": r["distribution"], "deduplicated": r["deduplicated"],
                            "pruned": r["pruned"], "evaluated": r["evaluated"], "feasible": r["feasible"],
                            "not_evaluated": r["not_evaluated"], "stop_reason": r["stop_reason"]} for r in rounds],
        "candidate_distribution_round1": distribution,
        "total_pruned_by_reason": {reason: sum(1 for p in pruning if p.get("reason") == reason)
                                    for reason in {p.get("reason") for p in pruning}},
        "total_excluded_by_reason": {reason: sum(1 for e in outside_budget if e.get("reason") == reason)
                                      for reason in {e.get("reason") for e in outside_budget}},
        "discovery_trace": trace,
    }
    output_dir = Path("artifacts/phase2f8_1")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "discovery-trace.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
