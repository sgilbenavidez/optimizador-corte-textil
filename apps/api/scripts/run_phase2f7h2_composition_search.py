"""Phase 2F.7H-2: CompositionExplorer harness over the 216-garment benchmark.

Generalizes run_phase2f7h_global_search.py's M2+L1-only, checkpoint-seeded
flow: CompositionExplorer generates/screens arithmetic composition
candidates (reusing CandidateCompositionGenerator unmodified), then this
script geometrically evaluates a scoped subset by cold-starting from the
already-trusted legacy engine (MARKER_ENGINE.nest, the same engine
production uses) and improving it with GlobalNestingSearch.

Scope (documented in docs/plans -- see FASE-2F.7H-1-2-composition-search.md):
SMALL/MEDIUM-tier ALNS budgets, 2 seeds per composition (3 for the single
most important mixed-size candidate), M1/M2/M3 always evaluated, M4 only if
its own area bound doesn't already reject it, M2+L1 refreshed through the new
pipeline, plus one auto-selected mixed-size candidate and the S3xXXL5 /
XS6xXL7 divisibility candidates gated by their own attainability tier.
"""
from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MARKER_ENGINE, MarkerPreviewService
from costura_optima.domain.composition_explorer import (
    CompositionCandidateReport,
    CompositionExplorer,
    compute_attainability,
)
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel, canonical_path
from costura_optima.domain.nesting_models import PrecisionConfiguration
from costura_optima.domain.production_models import CandidateGenerationConfig
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist

SIZE_ORDER = ("XS", "S", "M", "L", "XL", "XXL", "XXXL")
DEMAND_216 = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
ZERO_OVERPRODUCTION = {size: 0 for size in DEMAND_216}


class _SessionFixture:
    """One shared in-memory pattern/catalog for every composition this run
    evaluates -- avoids re-seeding+re-generating per composition.
    """
    def __init__(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine, expire_on_commit=False)()
        seed_catalog(self.session)
        self.pattern_set, _ = generate_and_persist(self.session)
        service = MarkerPreviewService(self.session)
        self.fabric = service.catalog.list_fabrics()[0]
        self.table = service.catalog.list_tables()[0]
        self.units = self.pattern_set.geometry_units_per_cm


def _composition_payload(composition):
    return [{"size_code": size, "quantity": quantity} for size, quantity in composition]


def build_request(fixture: _SessionFixture, composition, *, seed: int = 1):
    """Builds a real MarkerRequest for `composition` via the production
    MarkerPreviewService path (same construction production uses), aborting
    before the expensive legacy nest actually runs -- identical technique to
    run_phase2f7h_global_search.py::_request, generalized to any composition.
    """
    service = MarkerPreviewService(fixture.session)
    captured = {}
    import costura_optima.application.services as services
    original = services.MARKER_ENGINE

    class Capture:
        def nest(self, request):
            captured["request"] = request
            raise RuntimeError("captured")

    services.MARKER_ENGINE = Capture()
    try:
        service.generate(MarkerPreviewRequest(
            pattern_set_version_id=fixture.pattern_set.id, fabric_configuration_id=fixture.fabric.id,
            cutting_table_configuration_id=fixture.table.id, composition=_composition_payload(composition),
            deterministic=True, seed=seed, evaluation_budget=100_000, debug=True,
        ))
    except RuntimeError as exc:
        if str(exc) != "captured":
            raise
    finally:
        services.MARKER_ENGINE = original
    return captured["request"]


def cold_start(request):
    """Cold-starts ALNS from the trusted legacy engine's own result -- the
    same engine (MARKER_ENGINE / DeterministicNestingEngine) production
    already uses and trusts. No hardcoded checkpoint file.
    """
    result = MARKER_ENGINE.nest(request)
    if result.status != "VALIDATED_FEASIBLE" or not result.placements:
        raise ValueError(f"Legacy engine could not produce a feasible seed marker: {result.status}")
    return result.placements


def size_area_units2(fixture: _SessionFixture) -> dict[str, int]:
    kernel = IntegerGeometryKernel(PrecisionConfiguration(geometry_units_per_cm=fixture.units), GeometryOperationCache())
    totals: dict[str, int] = {size: 0 for size in SIZE_ORDER}
    for piece in fixture.pattern_set.pieces:
        cut_path = canonical_path(piece.operational_geometry["coordinates"][0])
        totals[piece.size_code] = totals.get(piece.size_code, 0) + kernel.area_units2(cut_path) * piece.quantity
    return totals


def size_piece_fits(fixture: _SessionFixture, usable_width_units: int) -> dict[str, bool]:
    fits: dict[str, bool] = {size: True for size in SIZE_ORDER}
    for piece in fixture.pattern_set.pieces:
        ring = piece.operational_geometry["coordinates"][0]
        ys = [round(y * fixture.units) for _x, y in ring]
        if (max(ys) - min(ys)) > usable_width_units:
            fits[piece.size_code] = False
    return fits


def _write_composition_artifacts(output_dir: Path, result, report: CompositionCandidateReport | None):
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "composition_id": result.composition_id, "size_counts": result.size_counts,
        "garment_count": result.garment_count, "piece_count": result.piece_count,
        "piece_area_units2": result.piece_area_units2, "usable_width_units": result.usable_width_units,
        "theoretical_length_100_units": result.theoretical_length_100_units,
        "theoretical_length_90_units": result.theoretical_length_90_units,
        "best_marker_length_cm": (result.best_marker_length_units / 1000) if result.best_marker_length_units else None,
        "best_efficiency_pct": result.best_efficiency_pct, "best_seed": result.best_seed,
        "validated": result.validated, "marker_hash": result.marker_hash,
        "runtime_seconds": result.runtime_seconds, "search_iterations": result.search_iterations,
        "attainability_class": result.attainability_class, "status": result.status,
        "seed_lengths_cm": [length / 1000 for length in result.seed_lengths_units],
        "median_length_cm": (result.median_length_units / 1000) if result.median_length_units else None,
        "worst_length_cm": (result.worst_length_units / 1000) if result.worst_length_units else None,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if result.operator_statistics:
        (output_dir / "operator-statistics.json").write_text(json.dumps(result.operator_statistics, indent=2), encoding="utf-8")
    if result.placements:
        (output_dir / "best-marker.json").write_text(json.dumps({
            "marker_hash": result.marker_hash,
            "placements": [{"piece_instance_id": p.piece_instance_id, "translation": p.translation,
                            "rotation": p.rotation, "geometry_hash": p.geometry_hash} for p in result.placements],
        }, indent=2), encoding="utf-8")
    return summary


def main():
    parser = ArgumentParser()
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--time-budget", type=float, default=600.0)
    parser.add_argument("--candidate-budget", type=int, default=5000)
    parser.add_argument("--top-k", type=int, default=1)
    parser.add_argument("--beam-width", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/phase2f7h2"))
    parser.add_argument("--only", default=None, help="Comma-separated composition tags (e.g. M1,M2) to run this invocation; default runs all.")
    parser.add_argument("--list", action="store_true", help="Print the planned composition set (with attainability) and exit without evaluating.")
    args = parser.parse_args()

    fixture = _SessionFixture()
    usable_width_units = round(float(fixture.fabric.usable_width_cm) * fixture.units)
    max_length_units = round(float(fixture.table.usable_length_cm) * fixture.units)
    area_by_size = size_area_units2(fixture)
    fits_by_size = size_piece_fits(fixture, usable_width_units)

    explorer = CompositionExplorer(CandidateGenerationConfig(
        max_garments_per_marker=15, max_distinct_sizes_per_marker=4, max_candidate_compositions=48,
    ))
    reports = explorer.generate_candidates(
        DEMAND_216, ZERO_OVERPRODUCTION, area_by_size, fits_by_size, usable_width_units, max_length_units,
    )
    reports_by_composition = {report.composition: report for report in reports}

    def report_for(composition):
        existing = reports_by_composition.get(composition)
        if existing is not None:
            return existing
        total_area = sum(area_by_size[size] * quantity for size, quantity in composition)
        attainability = compute_attainability(total_area, usable_width_units, max_length_units)
        return CompositionCandidateReport(
            composition=composition, origin="MANUAL", round_number=0,
            garment_count=sum(quantity for _, quantity in composition), distinct_size_count=len(composition),
            area_lower_bound_units=attainability.theoretical_length_100_units, candidate_layers=(1,),
            potential_useful_coverage=0, potential_coverage_percentage=0.0, attainability=attainability,
            production_compatibility_score=0.0, candidate_hash=f"manual-{composition}",
        )

    manual_compositions = [(("M", 1),), (("M", 2),), (("M", 3),), (("M", 4),),
                            (("M", 2), ("L", 1)), (("S", 3), ("XXL", 5)), (("XS", 6), ("XL", 7))]
    selected_reports: dict = {}
    for composition in manual_compositions:
        selected_reports[composition] = report_for(composition)
    ranked = explorer.select_for_geometric_evaluation(reports, top_n=20)
    auto_mixed = next((r for r in ranked if r.distinct_size_count >= 2 and r.composition not in selected_reports), None)
    auto_mixed_composition = auto_mixed.composition if auto_mixed is not None else None
    if auto_mixed is not None:
        selected_reports[auto_mixed.composition] = auto_mixed

    tags = {composition: "+".join(f"{size}{quantity}" for size, quantity in composition) for composition in selected_reports}
    if args.list:
        for composition, report in selected_reports.items():
            print(f"{tags[composition]}: class={report.attainability.attainability_class} "
                  f"L90cm={report.attainability.theoretical_length_90_units / 1000:.2f} "
                  f"L100cm={report.attainability.theoretical_length_100_units / 1000:.2f}")
        return

    only = set(args.only.split(",")) if args.only else None

    compositions_summary = []
    for composition, report in selected_reports.items():
        tag = tags[composition]
        if only is not None and tag not in only:
            continue
        if report.attainability.attainability_class in ("VERY_TIGHT", "AREA_INFEASIBLE") and composition != (("M", 4),):
            result = explorer.prefiltered_result(report)
            compositions_summary.append(_write_composition_artifacts(args.output_root / tag, result, report))
            print(f"[2F.7H-2] {tag}: PREFILTER_REJECTED ({report.attainability.attainability_class})", flush=True)
            continue
        seeds = (1, 2, 3) if composition == auto_mixed_composition else (1, 2)
        print(f"[2F.7H-2] evaluating {tag} (class={report.attainability.attainability_class}, seeds={seeds})", flush=True)
        result = explorer.evaluate_geometrically(
            report, build_request=lambda comp, f=fixture: build_request(f, comp), cold_start=cold_start,
            seeds=seeds, max_iterations=args.max_iterations, max_runtime_s=args.time_budget,
            candidate_budget=args.candidate_budget, top_k=args.top_k, beam_width=args.beam_width,
        )
        summary = _write_composition_artifacts(args.output_root / tag, result, report)
        compositions_summary.append(summary)
        print(json.dumps(summary, indent=2), flush=True)

    args.output_root.mkdir(parents=True, exist_ok=True)
    combined_path = args.output_root / "compositions-summary.json"
    existing = json.loads(combined_path.read_text(encoding="utf-8")) if combined_path.exists() else []
    existing_by_id = {row["composition_id"]: row for row in existing}
    for row in compositions_summary:
        existing_by_id[row["composition_id"]] = row
    combined_path.write_text(json.dumps(list(existing_by_id.values()), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
