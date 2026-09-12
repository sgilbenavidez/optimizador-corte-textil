from datetime import datetime, timedelta, timezone

import pytest

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator, RunTimedOut
from costura_optima.infrastructure.db_models import OptimizationRunORM
from costura_optima.worker.recovery import recover_stale_runs, recovery_status
from costura_optima.patterns.persistence import generate_and_persist


def order_payload(ids, demand):
    return {
        "garment_model_version_id": ids["version"], "fabric_configuration_id": ids["fabric"],
        "cutting_table_configuration_id": ids["table"],
        "demand": [{"size_code": size, "quantity": quantity} for size, quantity in demand.items()],
    }


def test_case_b_evaluates_multi_size_runs_residual_round_and_exposes_stages(client, catalog_ids, database, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    with database() as session:
        generate_and_persist(session)
    demand = {"S": 3, "M": 20, "L": 10, "XL": 12, "XXL": 30}
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, demand)).json()
    run = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "phase2e1-case-b"},
        json={"max_marker_candidates": 14, "geometry_evaluation_budget_per_candidate": 100,
              "planner_time_limit_seconds": 6, "global_geometry_budget_seconds": 30},
    ).json()
    with database() as session:
        PlanningCoordinator(session).execute(run["id"])
    audit_response = client.get(f"/api/v1/optimization-runs/{run['id']}/audit")
    assert audit_response.status_code == 200
    audit = audit_response.json()
    assert audit["audit"]["rounds"]
    assert len(audit["audit"]["rounds"]) == 2
    assert audit["audit"]["rounds"][1]["generated"] > 0
    assert audit["audit"]["evaluated_multi_size_candidates"]
    assert any(len(item["composition"]) > 1 for item in audit["audit"]["evaluated_multi_size_candidates"])
    assert audit["solutions"]
    stages = audit["solutions"][0]["profiles"][0]["objective_stages"]
    assert stages and {"stage", "objective", "status", "value", "elapsed_ms", "fixed_from_previous_stage"} <= set(stages[0])


def test_timeout_checkpoint_and_recovery_classification(database):
    with database() as session:
        coordinator = PlanningCoordinator(session)
        with pytest.raises(RunTimedOut):
            coordinator._check_cancel(object(), deadline=0)
    assert recovery_status("JobTimeoutException: hard timeout") == ("TIMED_OUT", "worker_hard_timeout")
    assert recovery_status("worker interrupted", "stopped") == ("FAILED", "worker_interrupted")


def test_execute_reaches_timed_out_and_stale_recovery_distinguishes_failures(client, catalog_ids, database, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    with database() as session:
        generate_and_persist(session)
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"M": 3})).json()
    endpoint = f"/api/v1/production-orders/{order['id']}/optimization-runs"
    soft = client.post(endpoint, headers={"Idempotency-Key": "soft-timeout"}, json={}).json()
    coordinator = None
    with database() as session:
        coordinator = PlanningCoordinator(session)
        def expire(run, deadline=None):
            raise RunTimedOut()
        coordinator._check_cancel = expire
        coordinator.execute(soft["id"])
    assert client.get(f"/api/v1/optimization-runs/{soft['id']}").json()["status"] == "TIMED_OUT"

    hard = client.post(endpoint, headers={"Idempotency-Key": "hard-timeout"}, json={}).json()
    interrupted = client.post(endpoint, headers={"Idempotency-Key": "interrupted"}, json={}).json()
    now = datetime.now(timezone.utc)
    with database() as session:
        hard_row = session.get(OptimizationRunORM, hard["id"]); interrupted_row = session.get(OptimizationRunORM, interrupted["id"])
        for row in (hard_row, interrupted_row):
            row.status = "RUNNING"; row.heartbeat_at = now - timedelta(minutes=10)
        hard_row.phase = "NESTING"
        interrupted_row.phase = "PLANNING"
        hard_row.error_detail = "JobTimeoutException: exceeded hard timeout"
        interrupted_row.error_detail = "worker interrupted"
        session.commit()
        assert recover_stale_runs(session, now=now) == 2
    hard_result = client.get(f"/api/v1/optimization-runs/{hard['id']}").json()
    assert hard_result["status"] == "TIMED_OUT"
    assert hard_result["failure_phase"] == "NESTING"
    interrupted_result = client.get(f"/api/v1/optimization-runs/{interrupted['id']}").json()
    assert interrupted_result["status"] == "FAILED"
    assert interrupted_result["failure_phase"] == "PLANNING"
    assert interrupted_result["error_detail"] == "worker_interrupted"
