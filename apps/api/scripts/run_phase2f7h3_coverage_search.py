"""Phase 2F.7H-3: coverage-aware composition search + Pareto marker catalog.

Staged pipeline (phase spec Section 11) over the existing, unmodified
CandidateCompositionGenerator / GlobalNestingSearch / ProductionPlanner
stack, targeting the diagnosed gap from 2F.7H-2: the evaluated catalog
(M1-4, M2+L1, S3+XXL5) never produces XS or XL, so exact production for the
216-benchmark was INFEASIBLE.

Modes:
  --mode list     Stage A only: generate + screen candidates, print
                   attainability, no DB/geometry. Cheap, instant.
  --mode stageb   Stage B: cold-start-only (skip_search=True) geometric
                   evaluation for selected/candidate compositions. Cheap
                   (single legacy nest per composition, no ALNS).
  --mode stagec   Stage C: full ALNS (light budget) for finalists.
  --mode stagedd  Stage D: multi-seed confirmation for markers used in the
                   winning exact-production solution.
  --mode oracle   Run the feasibility oracle (ProductionPlanner, exact mode)
                   against the accumulated catalog (2F.7H-2 + phase2f7h3),
                   apply pareto_filter, print diagnosis.
"""
from __future__ import annotations

import json
from argparse import ArgumentParser
from pathlib import Path

from costura_optima.domain.composition_explorer import CompositionCandidateReport, CompositionExplorer, compute_attainability
from costura_optima.domain.coverage_catalog import coverage_priority_score, diagnose_infeasibility, pareto_filter
from costura_optima.domain.production_models import CandidateGenerationConfig, PlanningConfig, ValidatedMarkerCandidate
from costura_optima.domain.production_planner import ProductionPlanner
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator

from run_phase2f7h2_composition_search import (  # noqa: E402
    DEMAND_216, ZERO_OVERPRODUCTION, _SessionFixture, build_request, cold_start, size_area_units2, size_piece_fits,
)
from run_phase2f7h2_exact_production import _catalog_from_summary  # noqa: E402

OUTPUT_ROOT = Path("artifacts/phase2f7h3")
PREVIOUS_CATALOG_SUMMARY = Path("artifacts/phase2f7h2/compositions-summary.json")
MAX_LAYERS = 55

# Section 9: explicit XS/XL family.
XS_XL_FAMILY = [
    (("XS", 1), ("XL", 1)), (("XS", 2), ("XL", 1)), (("XS", 1), ("XL", 2)),
    (("XS", 2), ("XL", 2)), (("XS", 3), ("XL", 1)), (("XS", 1), ("XL", 3)),
    (("XS", 2), ("XL", 3)), (("XS", 3), ("XL", 2)), (("XS", 3), ("XL", 3)),
]
# Section 10: cross-size synergy (XS/XL paired with a size other than each other).
CROSS_SYNERGY = [
    (("S", 1), ("XS", 1)), (("M", 1), ("XS", 1)), (("L", 1), ("XS", 1)), (("XS", 1), ("XXL", 1)),
    (("S", 1), ("XL", 1)), (("M", 1), ("XL", 1)),
]
# Section 15: repeated-garment monotonicity check for XS/XL specifically.
SINGLE_SIZE_REPEATS = [(("XS", n),) for n in (1, 2, 3)] + [(("XL", n),) for n in (1, 2, 3)]

SIZE_ORDER = ("XS", "S", "M", "L", "XL", "XXL", "XXXL")


def _tag(composition) -> str:
    ordered = sorted(composition, key=lambda item: SIZE_ORDER.index(item[0]))
    return "+".join(f"{size}{quantity}" for size, quantity in ordered)


def _canonical(composition) -> tuple:
    return tuple(sorted(composition, key=lambda item: SIZE_ORDER.index(item[0])))


def _manual_report(composition, area_by_size, usable_width_units, max_length_units):
    total_area = sum(area_by_size[size] * quantity for size, quantity in composition)
    attainability = compute_attainability(total_area, usable_width_units, max_length_units)
    return CompositionCandidateReport(
        composition=composition, origin="MANUAL_COVERAGE", round_number=0,
        garment_count=sum(quantity for _, quantity in composition), distinct_size_count=len(composition),
        area_lower_bound_units=attainability.theoretical_length_100_units, candidate_layers=(1,),
        potential_useful_coverage=0, potential_coverage_percentage=0.0, attainability=attainability,
        production_compatibility_score=0.0, candidate_hash=f"manual-{composition}",
    )


def _existing_catalog() -> tuple[ValidatedMarkerCandidate, ...]:
    rows = json.loads(PREVIOUS_CATALOG_SUMMARY.read_text(encoding="utf-8"))
    for row in rows:
        row.setdefault("_artifact_dir", str(Path("artifacts/phase2f7h2") / _tag(tuple(sorted(
            row["size_counts"].items(), key=lambda item: SIZE_ORDER.index(item[0]))))))
    return _catalog_from_summary(rows)


def _phase3_catalog() -> tuple[ValidatedMarkerCandidate, ...]:
    summary_path = OUTPUT_ROOT / "compositions-summary.json"
    if not summary_path.exists():
        return ()
    rows = json.loads(summary_path.read_text(encoding="utf-8"))
    for row in rows:
        row.setdefault("_artifact_dir", str(OUTPUT_ROOT / _tag(tuple(row["size_counts"].items()))))
    return _catalog_from_summary(rows)


def _build_candidate_set(fixture, area_by_size, fits_by_size, usable_width_units, max_length_units, explorer):
    manual = [_manual_report(c, area_by_size, usable_width_units, max_length_units)
              for c in XS_XL_FAMILY + CROSS_SYNERGY + SINGLE_SIZE_REPEATS]
    catalog_now = list(_existing_catalog())
    diagnosis = diagnose_infeasibility(tuple(catalog_now), DEMAND_216, MAX_LAYERS)
    residual = diagnosis.residual_by_size
    generated = explorer.generate_candidates(
        DEMAND_216, ZERO_OVERPRODUCTION, area_by_size, fits_by_size, usable_width_units, max_length_units,
        residual=residual, round_number=2,
    )
    covered = frozenset(diagnosis.sizes_covered)
    seen_size_sets: set[frozenset] = set()
    scored: list[tuple[float, CompositionCandidateReport]] = []
    by_composition: dict[tuple, CompositionCandidateReport] = {}
    for report in manual:
        by_composition[_canonical(report.composition)] = report
    for report in generated:
        key = _canonical(report.composition)
        by_composition.setdefault(key, report)
    for report in by_composition.values():
        size_set = frozenset(size for size, _q in report.composition)
        # CompositionCandidateReport shares .composition/.potential_coverage_percentage
        # with CandidateComposition, so it satisfies coverage_priority_score's shape directly.
        score = coverage_priority_score(report, covered, seen_size_sets=frozenset(seen_size_sets))
        seen_size_sets.add(size_set)
        scored.append((score, report))
    scored.sort(key=lambda item: -item[0])
    return diagnosis, scored


def cmd_list(args):
    fixture = _SessionFixture()
    usable_width_units = round(float(fixture.fabric.usable_width_cm) * fixture.units)
    max_length_units = round(float(fixture.table.usable_length_cm) * fixture.units)
    area_by_size = size_area_units2(fixture)
    fits_by_size = size_piece_fits(fixture, usable_width_units)
    explorer = CompositionExplorer(CandidateGenerationConfig(
        max_garments_per_marker=15, max_distinct_sizes_per_marker=4, max_candidate_compositions=48,
    ))
    diagnosis, scored = _build_candidate_set(fixture, area_by_size, fits_by_size, usable_width_units, max_length_units, explorer)
    print(f"Uncovered sizes: {diagnosis.sizes_with_no_catalog_coverage}")
    print(f"Residual by size: {diagnosis.residual_by_size}")
    print(f"{'tag':<16}{'origin':<28}{'class':<16}{'score':>8}  L90cm   L100cm")
    for score, report in scored:
        tag = _tag(report.composition)
        print(f"{tag:<16}{report.origin:<28}{report.attainability.attainability_class:<16}{score:>8.2f}  "
              f"{report.attainability.theoretical_length_90_units/1000:6.2f}  {report.attainability.theoretical_length_100_units/1000:6.2f}")


def _write_composition_artifacts(output_dir: Path, result):
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


def _merge_summary(rows: list[dict]):
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    combined_path = OUTPUT_ROOT / "compositions-summary.json"
    existing = json.loads(combined_path.read_text(encoding="utf-8")) if combined_path.exists() else []
    existing_by_id = {row["composition_id"]: row for row in existing}
    for row in rows:
        existing_by_id[row["composition_id"]] = row
    combined_path.write_text(json.dumps(list(existing_by_id.values()), indent=2), encoding="utf-8")


def cmd_stageb(args):
    fixture = _SessionFixture()
    usable_width_units = round(float(fixture.fabric.usable_width_cm) * fixture.units)
    max_length_units = round(float(fixture.table.usable_length_cm) * fixture.units)
    area_by_size = size_area_units2(fixture)
    fits_by_size = size_piece_fits(fixture, usable_width_units)
    explorer = CompositionExplorer(CandidateGenerationConfig(
        max_garments_per_marker=15, max_distinct_sizes_per_marker=4, max_candidate_compositions=48,
    ))
    _diagnosis, scored = _build_candidate_set(fixture, area_by_size, fits_by_size, usable_width_units, max_length_units, explorer)
    only = set(args.only.split(",")) if args.only else None
    rows = []
    for _score, report in scored:
        tag = _tag(report.composition)
        if only is not None and tag not in only:
            continue
        if report.attainability.attainability_class in ("VERY_TIGHT", "AREA_INFEASIBLE"):
            print(f"[stageB] {tag}: PREFILTER_REJECTED ({report.attainability.attainability_class})")
            continue
        print(f"[stageB] evaluating {tag} (cold-start only)", flush=True)
        result = explorer.evaluate_geometrically(
            report, build_request=lambda comp, f=fixture: build_request(f, comp), cold_start=cold_start,
            seeds=(1,), max_iterations=0, max_runtime_s=1.0, skip_search=True,
        )
        summary = _write_composition_artifacts(OUTPUT_ROOT / tag, result)
        rows.append(summary)
        print(json.dumps(summary, indent=2), flush=True)
    _merge_summary(rows)


def cmd_stagec(args):
    fixture = _SessionFixture()
    usable_width_units = round(float(fixture.fabric.usable_width_cm) * fixture.units)
    max_length_units = round(float(fixture.table.usable_length_cm) * fixture.units)
    area_by_size = size_area_units2(fixture)
    fits_by_size = size_piece_fits(fixture, usable_width_units)
    explorer = CompositionExplorer(CandidateGenerationConfig(
        max_garments_per_marker=15, max_distinct_sizes_per_marker=4, max_candidate_compositions=48,
    ))
    _diagnosis, scored = _build_candidate_set(fixture, area_by_size, fits_by_size, usable_width_units, max_length_units, explorer)
    by_tag = {_tag(report.composition): report for _score, report in scored}
    only = args.only.split(",") if args.only else []
    rows = []
    for tag in only:
        report = by_tag.get(tag)
        if report is None:
            print(f"[stageC] skip {tag}: not in candidate set")
            continue
        seeds = tuple(range(1, args.seeds + 1))
        print(f"[stageC] evaluating {tag} with full ALNS (seeds={seeds}, iterations={args.max_iterations})", flush=True)
        result = explorer.evaluate_geometrically(
            report, build_request=lambda comp, f=fixture: build_request(f, comp), cold_start=cold_start,
            seeds=seeds, max_iterations=args.max_iterations, max_runtime_s=args.time_budget,
            candidate_budget=args.candidate_budget, top_k=1, beam_width=1,
        )
        summary = _write_composition_artifacts(OUTPUT_ROOT / tag, result)
        rows.append(summary)
        print(json.dumps(summary, indent=2), flush=True)
    _merge_summary(rows)


def cmd_oracle(args):
    catalog = tuple(_existing_catalog()) + tuple(_phase3_catalog())
    catalog = pareto_filter(catalog)
    planner = ProductionPlanner(PlanningConfig(max_layers=MAX_LAYERS, time_limit_seconds=args.time_limit, deterministic=True, seed=1))
    solutions = planner.solve_profiles(DEMAND_216, ZERO_OVERPRODUCTION, catalog)
    feasible = [s for s in solutions if s.planning_status != "INFEASIBLE" and s.spreads]
    chosen = min(feasible, key=lambda s: (s.spread_count, s.marker_design_count, s.marker_change_count,
                                           s.total_fabric_units, s.total_overproduction)) if feasible else None
    diagnosis = diagnose_infeasibility(catalog, DEMAND_216, MAX_LAYERS)
    print(f"Catalog size after pareto_filter: {len(catalog)}")
    print(f"Uncovered sizes: {diagnosis.sizes_with_no_catalog_coverage}")
    print(f"Residual by size: {diagnosis.residual_by_size}")
    if chosen is None:
        print("EXACT_216_ORDER_SOLUTION_FOUND = NO")
    else:
        print("EXACT_216_ORDER_SOLUTION_FOUND = YES")
        audit_evidence = {
            "order_hash": "phase2f7h3-216-benchmark", "input_hash": "phase2f7h3-216-benchmark-input",
            "pattern_hash": "phase2f7h3-pattern", "fabric_snapshot": {"usable_width_units": catalog[0].usable_width_units},
            "table_snapshot": {"usable_length_units": 700_000}, "policy": {"maximum_overproduction": ZERO_OVERPRODUCTION},
            "candidate_generation_config": {"source": "coverage_catalog"}, "geometry_config": {"engine": "GlobalNestingSearch+legacy_cold_start"},
            "geometry_version": "2F.7H-3-coverage-catalog", "planner_config": {"max_layers": MAX_LAYERS, "time_limit_seconds": args.time_limit},
            "planner_version": "production_planner-2F.7H-3", "seed": 1, "solver_status": chosen.planning_optimality,
            "objective_stages": list(chosen.objective_stages), "validation_certificate": [m.validation_certificate for m in catalog],
            "marker_hashes": sorted(m.marker_hash for m in catalog),
        }
        validation = IndependentProductionPlanValidator().validate(
            chosen, catalog, DEMAND_216, ZERO_OVERPRODUCTION, MAX_LAYERS, 700_000, audit_evidence,
        )
        output = {
            "exact_solution_found": True, "planning_status": chosen.planning_status,
            "spread_count": chosen.spread_count, "marker_design_count": chosen.marker_design_count,
            "produced_by_size": chosen.produced_by_size, "overproduction_by_size": chosen.overproduction_by_size,
            "total_overproduction": chosen.total_overproduction, "total_fabric_units": chosen.total_fabric_units,
            "global_efficiency_percentage": chosen.global_efficiency_percentage,
            "spreads": [{"marker_hash": s.marker_hash, "composition": dict(s.composition), "layers": s.layers,
                         "repeats": s.repeats, "production_by_size": s.production_by_size} for s in chosen.spreads],
            "independent_validation_status": validation.status, "independent_validation_errors": list(validation.errors),
        }
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        (OUTPUT_ROOT / "exact-production-216.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(json.dumps(output, indent=2))


def main():
    parser = ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    p_list = sub.add_parser("list"); p_list.set_defaults(func=cmd_list)
    p_b = sub.add_parser("stageb"); p_b.add_argument("--only", default=None); p_b.set_defaults(func=cmd_stageb)
    p_c = sub.add_parser("stagec")
    p_c.add_argument("--only", default="")
    p_c.add_argument("--max-iterations", type=int, default=4)
    p_c.add_argument("--time-budget", type=float, default=900.0)
    p_c.add_argument("--candidate-budget", type=int, default=5000)
    p_c.add_argument("--seeds", type=int, default=2)
    p_c.set_defaults(func=cmd_stagec)
    p_o = sub.add_parser("oracle"); p_o.add_argument("--time-limit", type=float, default=60.0); p_o.set_defaults(func=cmd_oracle)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
