"""Exercise the built stack from inside its network without external access."""
from __future__ import annotations

import json
import time
import urllib.request
import uuid


BASE = "http://127.0.0.1:8000/api/v1"


def request(path: str, method: str = "GET", payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    with urllib.request.urlopen(urllib.request.Request(
        BASE + path, data=body, headers=request_headers, method=method,
    ), timeout=10) as response:
        return json.load(response)


def main() -> None:
    models = request("/garment-models")
    fabrics = request("/fabric-configurations")
    tables = request("/cutting-table-configurations")
    order = request("/production-orders", "POST", {
        "garment_model_version_id": models[0]["versions"][0]["id"],
        "fabric_configuration_id": fabrics[0]["id"],
        "cutting_table_configuration_id": tables[0]["id"],
        "demand": [{"size_code": "M", "quantity": 3}],
    })
    run = request(
        f"/production-orders/{order['id']}/optimization-runs", "POST",
        {
            "planner_refinement_engine": "heuristic", "time_limit_seconds": 30,
            "total_budget_seconds": 30, "geometry_budget_seconds": 10,
            "max_rounds": 1, "geometry_top_k_initial": 10,
            "max_marker_candidates": 10, "geometry_evaluation_budget_per_candidate": 25_000,
        },
        {"Idempotency-Key": f"offline-smoke-{uuid.uuid4()}"},
    )
    deadline = time.monotonic() + 45
    state = run
    while state["status"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
        time.sleep(1)
        state = request(f"/optimization-runs/{run['id']}")
    solutions = request(f"/optimization-runs/{run['id']}/solutions")
    result = {
        "offline_runtime": state["status"] in {"SUCCEEDED", "SUCCEEDED_EARLY", "TIMED_OUT"}
                           and state["best_solution_available"],
        "order_id": order["id"], "run_id": run["id"], "status": state["status"],
        "best_solution_available": state["best_solution_available"],
        "first_solution_elapsed_ms": (state.get("incumbent") or {}).get("first_solution_elapsed_ms"),
        "solution_count": len(solutions),
        "planner_refinement_engine": state["configuration"]["planner_refinement_engine"],
    }
    print(json.dumps(result, indent=2))
    if not result["offline_runtime"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
