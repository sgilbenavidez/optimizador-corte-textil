from costura_optima.application import services
from costura_optima.infrastructure.db_models import OptimizationRunORM, OptimizationSolutionORM


def order_payload(ids, demand):
    return {
        "garment_model_version_id": ids["version"],
        "fabric_configuration_id": ids["fabric"],
        "cutting_table_configuration_id": ids["table"],
        "demand": [{"size_code": size, "quantity": quantity} for size, quantity in demand.items()],
    }


def test_run_creation_is_async_and_idempotent(client, catalog_ids, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"M": 3})).json()
    first = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "same-click"}, json={},
    )
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "QUEUED"
    second = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "same-click"}, json={},
    )
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    fetched = client.get(f"/api/v1/optimization-runs/{first.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["input_hash"] == first.json()["input_hash"]


def test_idempotency_key_cannot_change_configuration(client, catalog_ids, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"S": 3})).json()
    endpoint = f"/api/v1/production-orders/{order['id']}/optimization-runs"
    assert client.post(endpoint, headers={"Idempotency-Key": "key"}, json={}).status_code == 202
    changed = client.post(endpoint, headers={"Idempotency-Key": "key"}, json={"allow_overproduction": False})
    assert changed.status_code == 422


def test_queued_run_can_be_cancelled(client, catalog_ids, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    monkeypatch.setattr(services, "cancel_queued_job", lambda run_id: None)
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"XXXL": 31})).json()
    run = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "cancel-me"}, json={},
    ).json()
    cancelled = client.post(f"/api/v1/optimization-runs/{run['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancel_requested"] is True


def test_idempotency_header_is_required(client, catalog_ids):
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"S": 1})).json()
    response = client.post(f"/api/v1/production-orders/{order['id']}/optimization-runs", json={})
    assert response.status_code == 422


def test_order_and_run_hashes_include_demand(client, catalog_ids, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    order_s = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"S": 3})).json()
    order_m = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"M": 3})).json()
    assert order_s["snapshot_hash"] != order_m["snapshot_hash"]
    run_s = client.post(
        f"/api/v1/production-orders/{order_s['id']}/optimization-runs",
        headers={"Idempotency-Key": "demand-s"}, json={},
    ).json()
    run_m = client.post(
        f"/api/v1/production-orders/{order_m['id']}/optimization-runs",
        headers={"Idempotency-Key": "demand-m"}, json={},
    ).json()
    assert run_s["input_hash"] != run_m["input_hash"]


def test_use_current_plan_requests_successful_early_stop(client, catalog_ids, database, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids, {"M": 3})).json()
    run = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "anytime"}, json={"planner_refinement_engine": "heuristic"},
    ).json()
    with database() as session:
        row = session.get(OptimizationRunORM, run["id"]); row.status = "RUNNING"
        session.add(OptimizationSolutionORM(
            id="incumbent", optimization_run_id=row.id, solution_hash="solution", fingerprint="fingerprint",
            rank=999, planning_status="FEASIBLE", planning_optimality="HEURISTIC_FEASIBLE",
            solution_origin="OPERATIONAL_HEURISTIC", metrics={}, validation_certificate={"status": "VALIDATED_PLAN"},
            explanation="BEST_VALIDATED_SO_FAR",
        ))
        session.commit()
    response = client.post(f"/api/v1/optimization-runs/{run['id']}/use-current-plan")
    assert response.status_code == 200
    assert response.json()["best_solution_available"] is True
    assert response.json()["use_current_plan_requested"] is True
