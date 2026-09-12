"""Freeze the Phase 2F.7B geometry baseline for the exact-ratio order 216.

This is a measurement harness.  It deliberately does not change the nesting
engine: the JSON it writes is the evidence used to decide whether further
search work is warranted.
"""
from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application import services
from costura_optima.application.planning_coordinator import PlanningCoordinator
from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.nesting_engine import DeterministicNestingEngine
from costura_optima.domain.integer_kernel import canonical_path, signed_area2
from costura_optima.infrastructure.database import Base, get_db_session
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist
from costura_optima.main import create_app


DEMAND_216 = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
EXACT_RATIOS = {
    "M2_L1": {"composition": [("M", 2), ("L", 1)], "layers": 21},
    "S3_XXL5": {"composition": [("S", 3), ("XXL", 5)], "layers": 11},
    "XS6_XL7": {"composition": [("XS", 6), ("XL", 7)], "layers": 5},
}


def _orientation(response: dict) -> dict:
    audit = (response.get("debug_geometry") or {}).get("orientation_audit", {})
    all_rows = list(audit.values())
    debug = response.get("debug_geometry") or {}
    return {
        "eligible_0": sum(0 in row.get("effective", []) for row in all_rows),
        "eligible_180": sum(180 in row.get("effective", []) for row in all_rows),
        "evaluated_0": sum(0 in row.get("evaluated", []) for row in all_rows),
        "evaluated_180": sum(180 in row.get("evaluated", []) for row in all_rows),
        "selected_0": sum(row.get("selected") == 0 for row in all_rows),
        "selected_180": sum(row.get("selected") == 180 for row in all_rows),
    }


def _svg(case_id: str, marker: dict) -> str:
    units = 1000
    width = round(marker["physical_width_cm"] * units)
    length = round((marker["marker_length_cm"] or marker["max_length_cm"]) * units)
    colors = {"XS": "#8dd3c7", "S": "#ffffb3", "M": "#bebada", "L": "#fb8072", "XL": "#80b1d3", "XXL": "#fdb462"}
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {length} {width}">',
            '<rect width="100%" height="100%" fill="#d8d2c4"/>']
    for item in marker["placements"]:
        points = " ".join(f"{x},{width-y}" for x, y in item["transformed_polygon"]["coordinates"][0])
        label = f'{item["piece_code"]} {item["size_code"]} {item["transform"]["rotation"]}°'
        x0, _, _, y1 = item["bbox"]["units"]
        rows.append(f'<polygon points="{points}" fill="{colors.get(item["size_code"], "#ddd")}" stroke="#102b32" stroke-width="450"/>')
        rows.append(f'<text x="{x0 + 1000}" y="{width - y1 + 3000}" font-family="sans-serif" font-size="2200">{label}</text>')
    rows.append(f'<text x="1800" y="{width - 1800}" font-family="sans-serif" font-size="2200">{case_id}: {marker["efficiency_percentage"]}% · {marker["piece_count"]} pieces</text>')
    rows.append("</svg>")
    return "\n".join(rows)


def _measure_ratio(service, pattern_set, fabric, table, case_id: str, spec: dict, transforms: str,
                   candidate_mode: str = "LEGACY", evaluation_budget: int = 100_000) -> dict:
    services.MARKER_ENGINE = DeterministicNestingEngine(candidate_mode=candidate_mode)
    started = perf_counter()
    original_policy = fabric.marker_direction_policy
    if transforms == "ZERO_ONLY":
        fabric.marker_direction_policy = "ONE_WAY"
    try:
        response = service.generate(MarkerPreviewRequest(
            pattern_set_version_id=pattern_set.id,
            fabric_configuration_id=fabric.id,
            cutting_table_configuration_id=table.id,
            composition=[{"size_code": size, "quantity": quantity} for size, quantity in spec["composition"]],
            deterministic=True, seed=1, evaluation_budget=evaluation_budget, debug=True,
        )).model_dump(mode="json")
    finally:
        fabric.marker_direction_policy = original_policy
    usable_width = response["usable_width_cm"]
    units = pattern_set.geometry_units_per_cm
    requested = dict(spec["composition"])
    requested_area = sum(
        (abs(signed_area2(canonical_path(piece.operational_geometry["coordinates"][0]))) / 2)
        * piece.quantity * requested.get(piece.size_code, 0) / (units * units)
        for piece in pattern_set.pieces
    )
    area = response["piece_area_total_cm2"] or requested_area
    debug = response.get("debug_geometry") or {}
    return {
        "case": case_id,
        "mode": transforms,
        "candidate_mode": candidate_mode,
        "composition": dict(spec["composition"]),
        "layers": spec["layers"],
        "piece_count": response["piece_count"],
        "total_piece_area_cm2": area,
        "area_lower_bound_length_cm": response["area_lower_bound_cm"],
        "geometry_lower_bound_cm": response["lower_bound_length_cm"],
        "target_length_100_cm": round(area / usable_width, 4),
        "target_length_90_cm": round(area / (usable_width * .90), 4),
        "target_length_95_cm": round(area / (usable_width * .95), 4),
        "required_efficiency_at_max_length_percentage": round(
            area / (usable_width * response["max_length_cm"]) * 100, 6
        ),
        "actual_marker_length_cm": response["marker_length_cm"],
        "efficiency_percentage": response["efficiency_percentage"],
        "waste_percentage": response["waste_percentage"],
        "geometry_status": response["status"],
        "search_status": response["search_status"],
        "validation_status": response["validation"]["status"],
        "elapsed_time_ms": response["elapsed_time_ms"],
        "wall_elapsed_ms": round((perf_counter() - started) * 1000, 3),
        "evaluation_count": response["evaluation_count"],
        "orientation": _orientation(response),
        "placement_strategy": debug.get("placement_strategy"),
        "legacy_fallback_enabled": debug.get("legacy_fallback_enabled"),
        "candidate_trace": debug.get("candidate_trace", []),
        "nfp_status": response["nfp_status"],
        "stopping_reason": response["stopping_reason"],
        "gap_to_90_cm": (round(response["marker_length_cm"] - area / (usable_width * .90), 4)
                         if response["marker_length_cm"] else None),
        "gap_to_95_cm": (round(response["marker_length_cm"] - area / (usable_width * .95), 4)
                         if response["marker_length_cm"] else None),
        "raw": response,
    }


def main() -> None:
    parser = ArgumentParser(description="Measure exact-ratio marker geometry incrementally.")
    parser.add_argument("--case", choices=tuple(EXACT_RATIOS), help="Measure one ratio only.")
    parser.add_argument("--ratios-only", action="store_true", help="Do not run the global 216 planner.")
    parser.add_argument("--plan-only", action="store_true", help="Run only the frozen global 216 baseline.")
    parser.add_argument("--assemble", action="store_true", help="Merge completed incremental measurements into benchmark.json.")
    parser.add_argument("--render-png", action="store_true", help="Render golden SVG evidence to local PNG screenshots.")
    parser.add_argument("--candidate-mode", choices=("LEGACY", "NFP_VERTEX_ONLY", "CANDIDATE_SPACE", "CANDIDATE_SPACE_PLUS_LEGACY"), default="LEGACY")
    parser.add_argument("--evaluation-budget", type=int, default=100_000)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/phase2f7b"))
    args = parser.parse_args()
    output = args.output_dir
    golden = output / "golden"
    golden.mkdir(parents=True, exist_ok=True)
    if args.render_png:
        from resvg_py import svg_to_bytes
        rendered = []
        for source in sorted(golden.glob("*.svg")):
            target = source.with_suffix(".png")
            target.write_bytes(svg_to_bytes(source.read_text(encoding="utf-8"), width=1800))
            rendered.append(str(target))
        print(json.dumps({"rendered_png": rendered}, indent=2))
        return
    if args.assemble:
        document_path = output / "benchmark.json"
        document = json.loads(document_path.read_text(encoding="utf-8"))
        document["ratios"] = [
            json.loads((output / f"{case}-{mode}.json").read_text(encoding="utf-8"))
            for case in EXACT_RATIOS for mode in ("ZERO_ONLY", "LEGAL_TWO_WAY")
        ]
        document_path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"assembled": str(document_path), "ratio_count": len(document["ratios"])}, indent=2))
        return
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed_catalog(session)
        pattern_set, _ = generate_and_persist(session)
        service = MarkerPreviewService(session)
        fabric, table = service.catalog.list_fabrics()[0], service.catalog.list_tables()[0]

        # Freeze individual marker geometry first; no planner state can affect it.
        ratios = []
        if not args.plan_only:
            cases = {args.case: EXACT_RATIOS[args.case]} if args.case else EXACT_RATIOS
            for case_id, spec in cases.items():
                for mode in ("ZERO_ONLY", "LEGAL_TWO_WAY"):
                    row = _measure_ratio(service, pattern_set, fabric, table, case_id, spec, mode, args.candidate_mode, args.evaluation_budget)
                    response = row.pop("raw")
                    (golden / f"{case_id}-{mode}.svg").write_text(_svg(f"{case_id} {mode}", response), encoding="utf-8")
                    ratios.append(row)
                    (output / f"{case_id}-{mode}.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.ratios_only:
        print(json.dumps({"ratios": ratios}, indent=2, ensure_ascii=False))
        return

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
            "demand": [{"size_code": size, "quantity": quantity} for size, quantity in DEMAND_216.items()],
        }).json()
        run = client.post(f"/api/v1/production-orders/{order['id']}/optimization-runs", headers={"Idempotency-Key": "phase2f7b-216-baseline"}, json={
            "max_garments_per_marker": 15, "max_distinct_sizes_per_marker": 5,
            "max_candidate_compositions": 48, "max_marker_candidates": 24, "max_rounds": 2,
            "time_limit_seconds": 180, "total_budget_seconds": 180,
            "global_geometry_budget_seconds": 100, "geometry_budget_seconds": 100,
            "geometry_evaluation_budget_per_candidate": 25_000, "planner_time_limit_seconds": 30,
            "seed": 1,
        }).json()
        started = perf_counter()
        with factory() as session:
            PlanningCoordinator(session).execute(run["id"])
        elapsed = round((perf_counter() - started) * 1000, 3)
        summaries = client.get(f"/api/v1/optimization-runs/{run['id']}/solutions").json()
        recommended = next((item for item in summaries if item["recommended"]), None)
        solution = client.get(f"/api/v1/optimization-solutions/{recommended['id']}").json() if recommended else None
    result = {"schema_version": "phase-2f7b-v1", "demand": DEMAND_216, "ratios": ratios,
              "baseline_216": {"run_id": run["id"], "elapsed_time_ms": elapsed, "solution": solution}}
    (output / "benchmark.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
