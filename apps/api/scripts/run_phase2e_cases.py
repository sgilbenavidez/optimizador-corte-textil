"""Execute and audit the mandatory Phase 2E orders through the public HTTP API."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


CASES = {
    "A": {"S": 3},
    "B": {"S": 3, "M": 20, "L": 10, "XL": 12, "XXL": 30},
    "C": {"XS": 1, "S": 1, "M": 1, "L": 1, "XL": 1, "XXL": 1, "XXXL": 1},
    "D": {"M": 100},
    "E": {"XXXL": 31},
}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "INFEASIBLE"}


def request(base_url: str, method: str, path: str, payload=None, headers=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v1{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{method} {path}: HTTP {error.code}: {error.read().decode()}") from error


def create_runs(base_url: str, include_exact: bool):
    model = request(base_url, "GET", "/garment-models")[0]["versions"][0]
    fabric = request(base_url, "GET", "/fabric-configurations")[0]
    table = request(base_url, "GET", "/cutting-table-configurations")[0]
    runs = {}
    modes = (True, False) if include_exact else (True,)
    stamp = int(time.time())
    for allow_overproduction in modes:
        mode = "bounded" if allow_overproduction else "exact"
        for case, requested in CASES.items():
            demand = [
                {"size_code": size["code"], "quantity": requested.get(size["code"], 0)}
                for size in model["sizes"]
            ]
            order = request(base_url, "POST", "/production-orders", {
                "garment_model_version_id": model["id"],
                "fabric_configuration_id": fabric["id"],
                "cutting_table_configuration_id": table["id"],
                "demand": demand,
            })
            run = request(
                base_url,
                "POST",
                f"/production-orders/{order['id']}/optimization-runs",
                {"allow_overproduction": allow_overproduction},
                {"Idempotency-Key": f"phase2e-{stamp}-{mode}-{case}"},
            )
            runs[f"{case}_{mode}"] = run["id"]
    return runs


def wait_and_collect(base_url: str, runs: dict[str, str], timeout_seconds: int):
    deadline = time.monotonic() + timeout_seconds
    pending = dict(runs)
    completed = {}
    while pending and time.monotonic() < deadline:
        for name, run_id in list(pending.items()):
            run = request(base_url, "GET", f"/optimization-runs/{run_id}")
            if run["status"] in TERMINAL:
                summaries = request(base_url, "GET", f"/optimization-runs/{run_id}/solutions")
                solutions = [
                    request(base_url, "GET", f"/optimization-solutions/{summary['id']}")
                    for summary in summaries
                ]
                audit = request(base_url, "GET", f"/optimization-runs/{run_id}/audit")
                completed[name] = {"run": run, "solutions": solutions, "audit": audit}
                del pending[name]
        if pending:
            time.sleep(2)
    if pending:
        raise TimeoutError(f"Timed out waiting for {pending}")
    return completed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--exact", action="store_true", help="also execute allow_overproduction=false")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    runs = create_runs(args.base_url, args.exact)
    results = wait_and_collect(args.base_url, runs, args.timeout)
    document = {"started_at": started, "runs": runs, "results": results}
    rendered = json.dumps(document, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    summary = {
        name: {
            "status": item["run"]["status"],
            "solutions": len(item["solutions"]),
            "candidates": item["run"]["progress"],
            "elapsed": item["run"]["elapsed"],
        }
        for name, item in results.items()
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
