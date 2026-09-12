from costura_optima.application import services
from costura_optima.infrastructure.db_models import MarkerArtifactORM, OptimizationRunORM


def order_payload(ids, demand=None):
    return {
        "garment_model_version_id": ids["version"], "fabric_configuration_id": ids["fabric"],
        "cutting_table_configuration_id": ids["table"],
        "demand": [{"size_code": size, "quantity": quantity} for size, quantity in (demand or {"M": 3}).items()],
    }


def test_problem_contract_and_correlation_id(client):
    response = client.get("/api/v1/optimization-runs/not-found", headers={"X-Request-ID": "trace-2f"})
    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "trace-2f"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "detail", "instance", "error_code", "request_id"} <= response.json().keys()


def test_paginated_order_and_run_history_and_retry(client, catalog_ids, database, monkeypatch):
    monkeypatch.setattr(services, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    order = client.post("/api/v1/production-orders", json=order_payload(catalog_ids)).json()
    run = client.post(f"/api/v1/production-orders/{order['id']}/optimization-runs",
                      headers={"Idempotency-Key": "original", "X-Request-ID": "order-trace"}, json={}).json()
    assert run["request_id"] == "order-trace"
    with database() as session:
        row = session.get(OptimizationRunORM, run["id"])
        row.status = "FAILED"; row.error_detail = "worker_execution_failed"; row.error_code = "OPTIMIZATION_FAILED"
        session.commit()
    retry = client.post(f"/api/v1/optimization-runs/{run['id']}/retry")
    assert retry.status_code == 202
    assert retry.json()["id"] != run["id"]
    history = client.get(f"/api/v1/production-orders/{order['id']}/optimization-runs?page=1&page_size=1").json()
    assert history["total"] == 2 and len(history["items"]) == 1 and history["pages"] == 2
    orders = client.get("/api/v1/production-orders?page=1&page_size=20").json()
    assert orders["total"] == 1 and orders["items"][0]["latest_run"] is not None


def test_readiness_reports_redis_failure_without_hiding_database(client, monkeypatch):
    from costura_optima.api import routes
    monkeypatch.setattr(routes, "redis_connection", lambda: (_ for _ in ()).throw(ConnectionError("offline")))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": True, "redis": False}


def test_metrics_endpoint_is_openmetrics_compatible(client):
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert "optimization_runs_total" in response.text
    assert "queue_depth" in response.text


def test_svg_download_serializes_only_certified_placements(client, database):
    marker_hash = "a" * 64
    marker = {
        "status": "VALIDATED_FEASIBLE", "geometry_units_per_cm": 1000,
        "marker_length_cm": 10, "physical_width_cm": 5,
        "placements": [{"piece_code": "FRONT", "size_code": "M",
                         "transform": {"rotation": 180, "mirrored": False},
                         "transformed_polygon": {"coordinates": [[[0, 0], [1000, 0], [1000, 1000], [0, 0]]]},
                         "grainline": {"start": [200, 200], "end": [800, 200]}}],
    }
    with database() as session:
        session.add(MarkerArtifactORM(marker_hash=marker_hash, content_key="b" * 64, pattern_hash="c" * 64,
            fabric_hash="d" * 64, table_hash="e" * 64, composition={"M": 1}, marker_payload=marker,
            geometry_engine_version="test", marker_search_status="FEASIBLE", validation_status="VALIDATED"))
        session.commit()
    response = client.get(f"/api/v1/markers/{marker_hash}/svg")
    assert response.status_code == 200
    assert 'points="0,5000 1000,5000 1000,4000 0,5000"' in response.text
    assert "FRONT · M · 180°" in response.text
