from __future__ import annotations

from argparse import ArgumentParser
import json
from pathlib import Path
import platform
from sys import getsizeof

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application import services
from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.nesting_engine import DeterministicNestingEngine
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist


CASES = {
    "M1": [{"size_code": "M", "quantity": 1}],
    "M2": [{"size_code": "M", "quantity": 2}],
    "M3": [{"size_code": "S", "quantity": 1}, {"size_code": "M", "quantity": 1}],
    "M4": [{"size_code": "M", "quantity": 1}, {"size_code": "XL", "quantity": 1}],
    "M5": [{"size_code": size, "quantity": 1} for size in ("XS", "S", "M", "L", "XL", "XXL", "XXXL")],
    "M6": [{"size_code": "XXXL", "quantity": 2}],
}
GOLDEN_CASES = {"M1", "M3", "M4"}


def _deep_size(value, seen=None) -> int:
    seen = seen or set()
    object_id = id(value)
    if object_id in seen:
        return 0
    seen.add(object_id)
    size = getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_size(key, seen) + _deep_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, set)):
        size += sum(_deep_size(item, seen) for item in value)
    return size


def _svg(case_id: str, marker: dict) -> str:
    units = 1000
    width = round(marker["physical_width_cm"] * units)
    length = round((marker["marker_length_cm"] or marker["max_length_cm"]) * units)
    colors = {"FRONT": "#dcebe5", "BACK": "#f4dfb9", "SLEEVE": "#d9e1ef", "NECKBAND": "#ead8e5"}
    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {length} {width}">',
        '<rect width="100%" height="100%" fill="#d8d2c4"/>',
        f'<rect x="{marker["margins_cm"]["start"]*units:.0f}" y="{marker["margins_cm"]["right"]*units:.0f}" width="{length-(marker["margins_cm"]["start"]+marker["margins_cm"]["end"])*units:.0f}" height="{marker["usable_width_cm"]*units:.0f}" fill="#fffdf7" stroke="#8da49f" stroke-width="400"/>',
    ]
    for item in marker["placements"]:
        points = " ".join(f'{x},{width-y}' for x, y in item["transformed_polygon"]["coordinates"][0])
        x0, _, _, y1 = item["bbox"]["units"]
        rows.append(f'<polygon points="{points}" fill="{colors.get(item["piece_code"], "#dcebe5")}" stroke="#102b32" stroke-width="450"/>')
        rows.append(f'<text x="{x0+1500}" y="{width-y1+3500}" font-family="sans-serif" font-size="2800" fill="#102b32">{item["piece_code"]} {item["size_code"]}</text>')
    rows.append(f'<text x="2500" y="{width-2500}" font-family="sans-serif" font-size="2400" fill="#9f352c">{case_id} · geometría validada · no es plan de producción</text>')
    rows.append("</svg>")
    return "\n".join(rows)


def run(output_dir: Path) -> dict:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    golden_dir = output_dir / "golden"
    golden_dir.mkdir(exist_ok=True)
    results = []
    with factory() as session:
        seed_catalog(session)
        pattern_set, _ = generate_and_persist(session)
        service = MarkerPreviewService(session)
        fabric = service.catalog.list_fabrics()[0]
        table = service.catalog.list_tables()[0]
        for case_id, composition in CASES.items():
            services.MARKER_ENGINE = DeterministicNestingEngine()
            response = service.generate(MarkerPreviewRequest(
                pattern_set_version_id=pattern_set.id,
                fabric_configuration_id=fabric.id,
                cutting_table_configuration_id=table.id,
                composition=composition,
                deterministic=True,
                seed=1,
                evaluation_budget=100_000,
            )).model_dump(mode="json")
            row = {
                "case_id": case_id,
                "composition": composition,
                "status": response["status"],
                "validation": response["validation"]["status"],
                "pieces": response["piece_count"],
                "marker_length_cm": response["marker_length_cm"],
                "efficiency_percentage": response["efficiency_percentage"],
                "lower_bound_length_cm": response["lower_bound_length_cm"],
                "evaluations": response["evaluation_count"],
                "elapsed_time_ms": response["elapsed_time_ms"],
                "result_memory_approx_mib": round(_deep_size(response) / 1024 / 1024, 3),
                "cache": response["cache"],
                "input_hash": response["input_hash"],
                "result_hash": response["result_hash"],
            }
            results.append(row)
            (output_dir / f"{case_id}.svg").write_text(_svg(case_id, response), encoding="utf-8")
            if case_id in GOLDEN_CASES:
                certificate = {
                    **row,
                    "placements": response["placements"],
                    "validation_certificate": response["validation"],
                    "golden_meaning": "Reproducibility fixture; not proof of global optimality.",
                }
                (golden_dir / f"{case_id}.json").write_text(json.dumps(certificate, indent=2, ensure_ascii=False), encoding="utf-8")
    document = {
        "schema_version": "nesting-baseline-v1",
        "platform": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
        "coordinate_convention": "X=marker length, Y=fabric width, integer scale 1000 units/cm",
        "nfp_status": "PARTIAL",
        "cases": results,
    }
    (output_dir / "baseline.json").write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return document


def main() -> None:
    parser = ArgumentParser(description="Run reproducible M1-M6 geometry marker baselines.")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/nesting"))
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
