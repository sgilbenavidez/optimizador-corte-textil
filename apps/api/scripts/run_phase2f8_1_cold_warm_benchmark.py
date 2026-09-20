"""Phase 2F.8.1 Section 22: isolated cold vs. warm benchmark, plus a
separately-labeled Section 10 catalog-accumulation experiment.

COLD: fresh isolated DB, no import, persistent_catalog_bootstrap_enabled
irrelevant (nothing to bootstrap).
WARM: a SEPARATE fresh isolated DB (never shares state with COLD), import
runs first, then the benchmark runs with persistent_catalog_bootstrap_enabled=True.
ACCUMULATION: a third, explicitly separate experiment against ONE shared
persistent DB across 3 sequential runs (cold -> after run 1 -> after
enrichment), kept apart from the isolated cold/warm pair per the task's
explicit warning against accidentally sharing DB state between modes.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator
from costura_optima.infrastructure.database import Base, get_db_session
from costura_optima.infrastructure.db_models import MarkerArtifactORM
from costura_optima.infrastructure.repositories import CatalogRepository
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.main import create_app
from costura_optima.patterns.persistence import generate_and_persist

from import_phase2f7h_marker_evidence import import_all  # noqa: E402

DEMAND = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
RUN_CONFIG = {
    "planner_refinement_engine": "hybrid", "allow_overproduction": False, "joint_optimization_enabled": False,
    "max_garments_per_marker": 15, "max_distinct_sizes_per_marker": 5, "max_candidate_compositions": 48,
    "max_marker_candidates": 24, "max_rounds": 2, "geometry_evaluation_budget_per_candidate": 25_000,
    "fast_plan_budget_seconds": 5, "candidate_generation_budget_seconds": 5, "geometry_budget_seconds": 60,
    "planning_budget_seconds": 30, "total_budget_seconds": 280,
    "geometry_top_k_initial": 20, "geometry_top_k_per_round": 10, "beam_width": 10, "no_improvement_rounds": 1,
}


def _fresh_env():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed_catalog(session)
        generate_and_persist(session)
    return factory


def _run_order_and_run(factory, *, tag: str, bootstrap_enabled: bool, idempotency_suffix: str) -> dict:
    app = create_app()

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override
    services.enqueue_optimization_run = lambda run_id, timeout: run_id
    with factory() as session:
        catalog_entries = session.scalars(select(MarkerArtifactORM)).all()
        catalog_size_before = len(catalog_entries)
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
            headers={"Idempotency-Key": f"phase2f8-1-{tag}-{idempotency_suffix}"},
            json={**RUN_CONFIG, "persistent_catalog_bootstrap_enabled": bootstrap_enabled},
        ).json()
        started = perf_counter()
        with factory() as session:
            PlanningCoordinator(session).execute(run["id"])
        wall_s = round(perf_counter() - started, 3)
        final_run = client.get(f"/api/v1/optimization-runs/{run['id']}").json()
        audit = client.get(f"/api/v1/optimization-runs/{run['id']}/audit").json()["audit"]
        summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
        recommended = next((item for item in summaries if item["recommended"]), summaries[0] if summaries else None)
        detail = client.get(f"/api/v1/optimization-solutions/{recommended['id']}").json() if recommended else None
        metrics = detail["metrics"] if detail else {}
    with factory() as session:
        catalog_size_after = len(session.scalars(select(MarkerArtifactORM)).all())
    return {
        "tag": tag, "status": final_run["status"], "best_solution_available": final_run["best_solution_available"],
        "wall_time_s": wall_s,
        "catalog_entries_before": catalog_size_before, "catalog_entries_after": catalog_size_after,
        "marker_cache_hits": audit.get("marker_cache_hits"),
        "geometry_evaluations": final_run["progress"]["candidates_evaluated"],
        "catalog_bootstrap": audit.get("catalog_bootstrap"),
        "overproduction": metrics.get("total_overproduction"),
        "physical_spreads": metrics.get("physical_spreads"),
        "distinct_marker_designs": metrics.get("distinct_marker_designs"),
        "total_fabric_m": metrics.get("total_linear_consumption_m"),
        "global_weighted_efficiency_pct": metrics.get("global_efficiency_percentage"),
    }


def run_cold() -> dict:
    factory = _fresh_env()
    return _run_order_and_run(factory, tag="cold", bootstrap_enabled=False, idempotency_suffix="1")


def run_warm() -> dict:
    factory = _fresh_env()
    with factory() as session:
        catalog = CatalogRepository(session)
        fabric = catalog.list_fabrics()[0]
        table = catalog.list_tables()[0]
        from costura_optima.infrastructure.db_models import PatternSetVersionORM
        pattern_set = session.scalars(select(PatternSetVersionORM)).first()
        import_results = import_all(session, pattern_set, fabric, table)
        session.commit()
    result = _run_order_and_run(factory, tag="warm", bootstrap_enabled=True, idempotency_suffix="1")
    result["import_results"] = import_results
    return result


def run_accumulation() -> list[dict]:
    """Section 10: 3 sequential runs against ONE shared persistent DB,
    explicitly separate from the isolated cold/warm comparison above.
    """
    factory = _fresh_env()
    run1 = _run_order_and_run(factory, tag="accumulation_run1_cold", bootstrap_enabled=True, idempotency_suffix="acc1")
    run2 = _run_order_and_run(factory, tag="accumulation_run2_after_run1", bootstrap_enabled=True, idempotency_suffix="acc2")
    with factory() as session:
        catalog = CatalogRepository(session)
        fabric = catalog.list_fabrics()[0]
        table = catalog.list_tables()[0]
        from costura_optima.infrastructure.db_models import PatternSetVersionORM
        pattern_set = session.scalars(select(PatternSetVersionORM)).first()
        import_results = import_all(session, pattern_set, fabric, table)
        session.commit()
    run3 = _run_order_and_run(factory, tag="accumulation_run3_after_enrichment", bootstrap_enabled=True, idempotency_suffix="acc3")
    run3["import_results_before_run3"] = import_results
    return [run1, run2, run3]


def main() -> None:
    cold = run_cold()
    warm = run_warm()
    accumulation = run_accumulation()
    payload = {
        "demand": DEMAND, "cold": cold, "warm": warm, "accumulation": accumulation,
        "known_reference_total_fabric_m": 167.209, "known_reference_global_efficiency_pct": 72.578893,
        "pattern_validation_status": "ENGINEERING", "production_ready": False, "stop_gate": "ACTIVE",
    }
    output_dir = Path("artifacts/phase2f8_1")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cold-warm-accumulation-benchmark.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
