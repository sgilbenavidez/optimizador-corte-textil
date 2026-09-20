"""Generate Phase 2E.1 A-E, reproducibility, and candidate-budget evidence locally."""
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

CASES = {
    "A": {"S": 3},
    "B": {"S": 3, "M": 20, "L": 10, "XL": 12, "XXL": 30},
    "C": {"XS": 1, "S": 1, "M": 1, "L": 1, "XL": 1, "XXL": 1, "XXXL": 1},
    "D": {"M": 100},
    "E": {"XXXL": 31},
}


def setup():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed_catalog(session); generate_and_persist(session)
    app = create_app()
    def override():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db_session] = override
    services.enqueue_optimization_run = lambda run_id, timeout: run_id
    return TestClient(app), factory


def execute_case(client, factory, ids, name, demand, allow_overproduction, limit, key):
    order = client.post("/api/v1/production-orders", json={
        "garment_model_version_id": ids["version"], "fabric_configuration_id": ids["fabric"],
        "cutting_table_configuration_id": ids["table"],
        "demand": [{"size_code": size, "quantity": quantity} for size, quantity in demand.items()],
    }).json()
    run = client.post(f"/api/v1/production-orders/{order['id']}/optimization-runs", headers={"Idempotency-Key": key}, json={
        "allow_overproduction": allow_overproduction, "max_marker_candidates": limit,
        "geometry_evaluation_budget_per_candidate": 100, "planner_time_limit_seconds": 6,
        "global_geometry_budget_seconds": 60,
    }).json()
    started = perf_counter()
    with factory() as session:
        PlanningCoordinator(session).execute(run["id"])
    elapsed_ms = round((perf_counter() - started) * 1000, 3)
    final_run = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
    summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
    audit = client.get(f"/api/v1/optimization-runs/{run['id']}/audit").json()
    solutions = [client.get(f"/api/v1/optimization-solutions/{item['id']}").json() for item in summaries]
    return {"case": name, "order": order, "run": final_run, "solutions": solutions, "audit": audit, "wall_elapsed_ms": elapsed_ms}


def concise(item):
    solution = item["solutions"][0] if item["solutions"] else None
    return {
        "status": item["run"]["status"], "time_ms": item["wall_elapsed_ms"],
        "candidates": item["run"]["progress"],
        "multi_size_evaluated": len(item["audit"]["audit"].get("evaluated_multi_size_candidates", [])),
        "fabric_m": solution["metrics"]["total_linear_consumption_m"] if solution else None,
        "spreads": solution["metrics"]["spread_count"] if solution else None,
        "planning_status": solution["planning_status"] if solution else None,
        "solution_hashes": [row["solution_hash"] for row in item["solutions"]],
        "marker_hashes": item["audit"]["audit"].get("marker_hashes", []),
        "input_hash": item["run"]["input_hash"],
    }


def main():
    output = Path("artifacts/phase2e1"); output.mkdir(parents=True, exist_ok=True)
    client, factory = setup()
    models = client.get("/api/v1/garment-models").json(); fabrics = client.get("/api/v1/fabric-configurations").json(); tables = client.get("/api/v1/cutting-table-configurations").json()
    ids = {"version": models[0]["versions"][0]["id"], "fabric": fabrics[0]["id"], "table": tables[0]["id"]}
    bounded = {name: execute_case(client, factory, ids, name, demand, True, 24, f"bounded-{name}") for name, demand in CASES.items()}
    exact = {name: execute_case(client, factory, ids, name, demand, False, 24, f"exact-{name}") for name, demand in CASES.items()}
    repro_bounded = {name: execute_case(client, factory, ids, name, demand, True, 24, f"repro-{name}") for name, demand in CASES.items()}
    reproducibility = {name: {
        "first": concise(bounded[name]), "second": concise(repro_bounded[name]),
        "input_hash_match": bounded[name]["run"]["input_hash"] == repro_bounded[name]["run"]["input_hash"],
        "solution_hashes_match": [s["solution_hash"] for s in bounded[name]["solutions"]] == [s["solution_hash"] for s in repro_bounded[name]["solutions"]],
        "marker_hashes_match": bounded[name]["audit"]["audit"].get("marker_hashes") == repro_bounded[name]["audit"]["audit"].get("marker_hashes"),
    } for name in CASES}
    benchmark = {}
    for limit in (10, 14, 18, 24):
        benchmark[str(limit)] = {}
        for name, demand in CASES.items():
            item = execute_case(client, factory, ids, name, demand, True, limit, f"benchmark-{limit}-{name}")
            benchmark[str(limit)][name] = concise(item)
    baseline = benchmark["10"]
    for limit, cases in benchmark.items():
        for name, row in cases.items():
            base_fabric = baseline[name]["fabric_m"]
            row["improvement_vs_10_fabric_m"] = None if row["fabric_m"] is None or base_fabric is None else round(base_fabric - row["fabric_m"], 6)
    (output / "cases-a-e.json").write_text(json.dumps(bounded, indent=2, sort_keys=True), encoding="utf-8")
    (output / "cases-a-e-exact.json").write_text(json.dumps(exact, indent=2, sort_keys=True), encoding="utf-8")
    (output / "cases-a-e-repro.json").write_text(json.dumps(reproducibility, indent=2, sort_keys=True), encoding="utf-8")
    (output / "candidate-budget-benchmark.json").write_text(json.dumps(benchmark, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"bounded": {k: concise(v) for k, v in bounded.items()}, "exact": {k: concise(v) for k, v in exact.items()}, "reproducible": {k: all((v["input_hash_match"], v["solution_hashes_match"], v["marker_hashes_match"])) for k, v in reproducibility.items()}}, indent=2))


if __name__ == "__main__":
    main()
