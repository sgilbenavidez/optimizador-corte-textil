"""Bounded large-composition exploration against the existing Geometry API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from urllib.request import Request, urlopen

from costura_optima.domain.candidate_generator import CandidateCompositionGenerator
from costura_optima.domain.production_models import CandidateGenerationConfig


DEMAND = {"XS": 20, "S": 10, "M": 30, "L": 20, "XL": 14, "XXL": 3}
LIMITS = (3, 4, 5, 6, 8, 10, 12, 15)


def request_json(url: str, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=600) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000/api/v1")
    parser.add_argument("--output", default="artifacts/phase2f2/large-marker-exploration.json")
    args = parser.parse_args()
    pattern = request_json(f"{args.api}/pattern-sets")[0]
    fabric = request_json(f"{args.api}/fabric-configurations")[0]
    table = request_json(f"{args.api}/cutting-table-configurations")[0]
    maximum = {size: max(2, round(quantity * .03)) for size, quantity in DEMAND.items()}
    results = []
    for limit in LIMITS:
        generated = CandidateCompositionGenerator(CandidateGenerationConfig(
            max_garments_per_marker=limit,
            max_distinct_sizes_per_marker=min(5, len(DEMAND)),
            max_candidate_compositions=48,
            max_rounds=1,
        )).generate(
            DEMAND, maximum,
            {size: 1_000 for size in DEMAND}, {size: True for size in DEMAND},
            usable_width_units=176_000, max_marker_length_units=700_000,
        )
        candidates = [
            item for item in generated.candidates
            if sum(quantity for _, quantity in item.composition) == limit
            and (item.origin.startswith("PROPORTIONAL") or limit == 3)
        ]
        candidate = candidates[0] if candidates else max(
            generated.candidates, key=lambda item: (sum(quantity for _, quantity in item.composition), len(item.composition))
        )
        composition = dict(candidate.composition)
        started = perf_counter()
        marker = request_json(f"{args.api}/geometry/markers/preview", {
            "pattern_set_version_id": pattern["id"],
            "fabric_configuration_id": fabric["id"],
            "cutting_table_configuration_id": table["id"],
            "composition": [{"size_code": size, "quantity": quantity} for size, quantity in candidate.composition],
            "deterministic": True, "seed": 1, "evaluation_budget": 25_000, "debug": False,
        })
        results.append({
            "max_garments_per_marker": limit,
            "origin": candidate.origin,
            "composition": composition,
            "garments_per_layer": sum(composition.values()),
            "piece_count": len(marker["placements"]),
            "marker_length_cm": marker["marker_length_cm"],
            "marker_efficiency": marker["efficiency_percentage"],
            "status": marker["status"],
            "result_hash": marker["result_hash"],
            "geometry_elapsed_ms": marker["elapsed_time_ms"],
            "wall_elapsed_ms": round((perf_counter() - started) * 1000, 3),
            "within_table_700_cm": marker["marker_length_cm"] is not None and marker["marker_length_cm"] <= 700,
        })
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "order_demand": DEMAND,
        "strategy": "bounded proportional demand candidates from CandidateCompositionGenerator",
        "results": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
