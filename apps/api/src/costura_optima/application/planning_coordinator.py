from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from uuid import uuid4
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService, build_marker_request
from costura_optima.domain.candidate_generator import (
    CandidateCompositionGenerator, SIZE_ORDER, candidate_category, select_balanced_budget,
)
from costura_optima.domain.candidate_funnel import select_geometry_top_k
from costura_optima.domain.global_nesting_search import GlobalNestingSearch, make_state
from costura_optima.domain.integer_kernel import canonical_json_hash, canonical_path, close_path, path_bbox, signed_area2
from costura_optima.domain.nesting_models import Placement
from costura_optima.domain.production_models import (
    CandidateGenerationConfig, PlanningConfig, PlanningSolution, ValidatedMarkerCandidate,
)
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.operational_heuristic_solver import OperationalHeuristicSolver
from costura_optima.domain.planning_profiles import PROFILES
from costura_optima.domain.production_solver import CpSatRefinementSolver
from costura_optima.infrastructure.db_models import (
    MarkerArtifactORM, OptimizationCandidateORM, OptimizationRunORM, OptimizationSolutionORM,
    OptimizationSolutionProfileORM, PatternSetVersionORM, ProductionOrderORM, SizeResultORM, SpreadORM,
)
from costura_optima.infrastructure.repositories import CatalogRepository
from costura_optima.operational import metrics


SIZE_CODES = ("XS", "S", "M", "L", "XL", "XXL", "XXXL")
logger = logging.getLogger(__name__)


class RunCancelled(Exception):
    pass


class RunTimedOut(Exception):
    pass


class UseCurrentPlan(Exception):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def _marker_from_payload(artifact: MarkerArtifactORM) -> ValidatedMarkerCandidate:
    payload = artifact.marker_payload
    units = payload["geometry_units_per_cm"]
    return ValidatedMarkerCandidate(
        marker_hash=artifact.marker_hash, content_key=artifact.content_key,
        composition=tuple((size, int(quantity)) for size, quantity in sorted(artifact.composition.items(), key=lambda item: SIZE_ORDER[item[0]])),
        marker_length_units=round(payload["marker_length_cm"] * units), usable_width_units=round(payload["usable_width_cm"] * units),
        piece_area_units2=round(payload["piece_area_total_cm2"] * units * units),
        marker_area_units2=round(payload["marker_area_cm2"] * units * units),
        waste_area_units2=round(payload["waste_area_cm2"] * units * units),
        efficiency_percentage=payload["efficiency_percentage"], placements=tuple(payload["placements"]),
        validation_certificate=payload["validation"], geometry_engine_version=payload["algorithm_version"],
        marker_search_status=payload["search_status"], input_hash=payload["input_hash"],
        lower_bound_length_units=round(payload["lower_bound_length_cm"] * units),
    )


def _placement_from_marker_payload_row(row: dict) -> Placement:
    """Reconstructs a Placement from a ValidatedMarkerCandidate.placements
    row (the rich dict shape services.py::MarkerPreviewService.generate
    builds, e.g. via `_marker_from_payload`). Used only by 2F.8's marker
    refinement to seed GlobalNestingSearch from an already-validated
    marker's own geometry -- never to reinterpret arbitrary/untrusted input.
    """
    ring = row["transformed_polygon"]["coordinates"][0]
    polygon = tuple(tuple(point) for point in ring[:-1])  # drop close_path's repeated closing vertex
    grainline = (tuple(row["grainline"]["start"]), tuple(row["grainline"]["end"]))
    translation = (row["translation"]["x_units"], row["translation"]["y_units"])
    return Placement(
        row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"],
        row["transform"]["rotation"], row["transform"]["mirrored"], translation, polygon, grainline,
        tuple(row["bbox"]["units"]), row["geometry_hash"], row["sequence"],
    )


def _serialize_placement_for_payload(placement: Placement, units: int) -> dict:
    """Mirrors services.py::MarkerPreviewService.generate's placement dict
    construction exactly, so a refined marker's payload stays compatible
    with every existing consumer (SVG/cut-map rendering, _marker_from_payload).
    """
    return {
        "piece_instance_id": placement.piece_instance_id, "pattern_piece_id": placement.pattern_piece_id,
        "size_code": placement.size_code, "piece_code": placement.piece_code,
        "transform": {"rotation": placement.rotation, "mirrored": placement.mirrored},
        "translation": {"x_units": placement.translation[0], "y_units": placement.translation[1],
                        "x_cm": placement.translation[0] / units, "y_cm": placement.translation[1] / units},
        "transformed_polygon": {"unit": "geometry_unit", "coordinates": [close_path(placement.transformed_polygon)]},
        "grainline": {"unit": "geometry_unit", "start": placement.transformed_grainline[0], "end": placement.transformed_grainline[1]},
        "bbox": {"units": placement.bbox, "cm": [round(value / units, 4) for value in placement.bbox]},
        "geometry_hash": placement.geometry_hash, "sequence": placement.sequence,
    }


def remove_dominated(markers: tuple[ValidatedMarkerCandidate, ...]) -> tuple[ValidatedMarkerCandidate, ...]:
    retained = []
    for candidate in markers:
        dominated = any(
            other.composition == candidate.composition
            and other.marker_length_units <= candidate.marker_length_units
            and other.waste_area_units2 <= candidate.waste_area_units2
            and (other.marker_length_units < candidate.marker_length_units
                 or other.waste_area_units2 < candidate.waste_area_units2)
            for other in markers if other is not candidate
        )
        if not dominated:
            retained.append(candidate)
    return tuple(sorted(retained, key=lambda item: (item.composition, item.marker_length_units, item.waste_area_units2, item.marker_hash)))


class PlanningCoordinator:
    def __init__(self, session: Session):
        self.session = session

    def execute(self, run_id: str) -> None:
        total_started = perf_counter()
        run = self.session.get(OptimizationRunORM, run_id)
        if run is None:
            raise RuntimeError(f"Optimization run not found: {run_id}")
        if run.status in {"CANCELLED", "SUCCEEDED", "SUCCEEDED_EARLY", "FAILED", "TIMED_OUT", "INFEASIBLE"} or run.cancel_requested:
            return
        try:
            run.status = "RUNNING"; run.phase = "GENERATING_CANDIDATES"; run.started_at = utcnow()
            logger.info("optimization_started", extra={"request_id": run.request_id, "order_id": run.production_order_id,
                        "run_id": run.id, "job_id": run.worker_job_id, "phase": run.phase})
            deadline = total_started + float(run.configuration.get("total_budget_seconds", run.configuration["time_limit_seconds"]))
            self._heartbeat(run)
            order = self.session.scalar(
                select(ProductionOrderORM).where(ProductionOrderORM.id == run.production_order_id)
                .options(selectinload(ProductionOrderORM.demands))
            )
            if order is None or not order.pattern_set_version_id:
                raise RuntimeError("Order has no immutable pattern set snapshot.")
            pattern_set = self.session.scalar(
                select(PatternSetVersionORM).where(PatternSetVersionORM.id == order.pattern_set_version_id)
                .options(selectinload(PatternSetVersionORM.pieces))
            )
            if pattern_set is None:
                raise RuntimeError("Pattern set not found.")
            demand = {line.size_code: line.quantity for line in order.demands}
            config = run.configuration
            maximum_overproduction = {
                size: (max(2, ceil(quantity * config["overproduction_rate"])) if config["allow_overproduction"] else 0)
                for size, quantity in demand.items()
            }
            units = pattern_set.geometry_units_per_cm
            fabric_snapshot = order.catalog_snapshot["fabric_configuration"]
            usable_width = round(float(fabric_snapshot["usable_width_cm"]) * units)
            max_length = round(float(order.catalog_snapshot["cutting_table_configuration"]["usable_length_cm"]) * units)
            length_margins = round(
                (float(fabric_snapshot.get("start_margin_cm", 0)) +
                 float(fabric_snapshot.get("end_margin_cm", 0))) * units
            )
            size_area, size_fits = self._size_statistics(
                pattern_set, usable_width, max(0, max_length - length_margins),
            )
            generator = CandidateCompositionGenerator(CandidateGenerationConfig(**config["candidate_generation"]))
            max_layers = int(order.catalog_snapshot["cutting_table_configuration"]["max_layers"])
            heuristic = OperationalHeuristicSolver(max_layers=max_layers, beam_width=int(config.get("beam_width", 10)))
            refinement = CpSatRefinementSolver(PlanningConfig(
                max_layers=max_layers,
                time_limit_seconds=float(config.get("planning_budget_seconds", config["planner_time_limit_seconds"])),
                deterministic=True, seed=config["seed"],
            ))
            validator = IndependentProductionPlanValidator()
            markers: list[ValidatedMarkerCandidate] = []
            all_compositions: set[tuple[tuple[str, int], ...]] = set()
            rounds, pruning, excluded = [], [], []
            best_valid = []
            best_key = None
            generation_ms = geometry_ms = planning_ms = validation_ms = 0.0
            evaluated_limit = int(config["max_marker_candidates"])
            funnel_audit = []
            no_improvement_streak = 0
            candidate_generation_budget = float(config.get("candidate_generation_budget_seconds", 5))

            if config.get("persistent_catalog_bootstrap_enabled"):
                self._check_cancel(run, deadline)
                bootstrap_markers = self._bootstrap_compatible_markers(
                    pattern_set.content_hash, fabric_snapshot["content_hash"],
                    order.catalog_snapshot["cutting_table_configuration"]["content_hash"],
                )
                early_solution_found = False
                if bootstrap_markers:
                    markers.extend(bootstrap_markers)
                    bootstrap_catalog = remove_dominated(tuple(markers))
                    bootstrap_solution = heuristic.solve(demand, maximum_overproduction, bootstrap_catalog)
                    if bootstrap_solution.spreads:
                        evidence = self._audit_evidence(run, order, pattern_set, config, bootstrap_catalog, bootstrap_solution)
                        report = validator.validate(
                            bootstrap_solution, bootstrap_catalog, demand, maximum_overproduction, max_layers,
                            usable_table_length_units=max_length, audit_evidence=evidence,
                        )
                        if report.status == "VALIDATED_PLAN":
                            best_valid = self._merge_valid_solutions(best_valid, [(bootstrap_solution, report)])
                            best_key = self._best_key(best_valid)
                            early_solution_found = True
                            elapsed = round((perf_counter() - total_started) * 1000, 3)
                            self._persist_solution(
                                run, bootstrap_solution, report,
                                "BEST_VALIDATED_SO_FAR · plan a partir del catálogo persistente de markers validados.", 999,
                            )
                            if run.first_solution_elapsed_ms is None:
                                run.first_solution_elapsed_ms = elapsed
                                run.first_solution_spreads = bootstrap_solution.spread_count
                                run.first_solution_fabric_units = bootstrap_solution.total_fabric_units
                run.audit = {**(run.audit or {}), "catalog_bootstrap": {
                    "markers_loaded": len(bootstrap_markers),
                    "composition_hashes": sorted({marker.marker_hash for marker in bootstrap_markers}),
                    "early_solution_found": early_solution_found,
                }}
                self._heartbeat(run)

            for round_number in range(1, int(config["candidate_generation"]["max_rounds"]) + 1):
                self._check_cancel(run, deadline)
                if round_number > 1 and generation_ms / 1000 >= candidate_generation_budget:
                    if rounds:
                        rounds[-1]["stop_reason"] = "CANDIDATE_GENERATION_BUDGET"
                    break
                run.round_current = round_number
                round_started = perf_counter()
                residual = None if round_number == 1 else self._planning_residual(demand, best_valid)
                generated = generator.generate(
                    demand, maximum_overproduction, size_area, size_fits, usable_width, max_length,
                    residual=residual, round_number=round_number,
                    max_layers=max_layers,
                    length_margins_units=length_margins,
                )
                generation_ms += generated.elapsed_ms
                pruning.extend(generated.pruned)
                novel = tuple(item for item in generated.candidates if item.composition not in all_compositions)
                deduplicated = len(generated.candidates) - len(novel)
                all_compositions.update(item.composition for item in novel)
                remaining_budget = max(0, evaluated_limit - run.candidates_evaluated)
                if round_number == 1 and int(config["candidate_generation"]["max_rounds"]) > 1:
                    residual_reserve = min(evaluated_limit - 1, max(8, evaluated_limit // 2))
                    remaining_budget = max(0, remaining_budget - residual_reserve)
                top_k = int(config.get("geometry_top_k_initial", 20) if round_number == 1 else config.get("geometry_top_k_per_round", 10))
                funnel = select_geometry_top_k(novel, min(remaining_budget, top_k))
                selected, outside = funnel.selected, funnel.excluded
                funnel_audit.append({
                    "round": round_number,
                    "raw_compositions": len(generated.candidates) + len(generated.pruned),
                    "mathematical_pruned": len(generated.pruned),
                    "after_mathematical_pruning": len(generated.candidates),
                    "deduplicated": deduplicated,
                    **{key: value for key, value in funnel.counts.items() if key != "raw_compositions"},
                })
                selected_hashes = {item.candidate_hash for item in selected}
                generated_by_hash = {item.candidate_hash: item for item in generated.candidates}
                round_rows = []
                for candidate in novel:
                    is_selected = candidate.candidate_hash in selected_hashes
                    row = OptimizationCandidateORM(
                        id=str(uuid4()), optimization_run_id=run.id, candidate_hash=candidate.candidate_hash,
                        composition=dict(candidate.composition), round_number=round_number, origin=candidate.origin,
                        status="PENDING" if is_selected else "NOT_EVALUATED",
                        diagnostics=[] if is_selected else ["candidate_funnel_top_k"], cache_hit=False, geometry_elapsed_ms=0,
                    )
                    self.session.add(row); round_rows.append(row)
                    if not is_selected:
                        run.candidates_not_evaluated += 1
                        excluded.append({"round": round_number, "hash": candidate.candidate_hash,
                                         "composition": dict(candidate.composition),
                                         "category": candidate_category(candidate.origin, candidate.composition),
                                         "reason": "candidate_funnel_top_k"})
                self.session.flush()
                run.candidates_generated += len(generated.candidates) + len(generated.pruned)
                run.candidates_pending = sum(1 for row in round_rows if row.status == "PENDING")
                before = self._best_summary(best_valid)
                feasible_before = run.candidates_feasible
                infeasible_before = run.candidates_infeasible
                cache_before = sum(1 for row in round_rows if row.cache_hit)
                run.phase = "NESTING"; self._heartbeat(run)
                rows_by_hash = {row.candidate_hash: row for row in round_rows}
                geometry_rows = [rows_by_hash[item.candidate_hash] for item in selected]
                for row in geometry_rows:
                    if row.status != "PENDING":
                        continue
                    self._check_cancel(run, deadline)
                    if (run.first_solution_elapsed_ms is not None and
                            geometry_ms / 1000 >= float(config.get("geometry_budget_seconds", config["global_geometry_budget_seconds"]))):
                        row.status = "NOT_EVALUATED"; row.diagnostics = ["global_geometry_time_budget"]
                        run.candidates_not_evaluated += 1; continue
                    marker, cache_hit, marker_elapsed, diagnostics = self._evaluate_candidate(run, order, pattern_set, row.composition)
                    geometry_ms += marker_elapsed
                    row.cache_hit = cache_hit; row.geometry_elapsed_ms = marker_elapsed; row.diagnostics = diagnostics
                    run.candidates_evaluated += 1
                    if marker:
                        source_candidate = generated_by_hash[row.candidate_hash]
                        marker = replace(marker, origin=row.origin, candidate_layers=source_candidate.candidate_layers)
                        row.status = "VALIDATED_FEASIBLE"; row.marker_hash = marker.marker_hash
                        run.candidates_feasible += 1; markers.append(marker); run.best_feasible_found = True
                        if run.first_solution_elapsed_ms is None:
                            early_catalog = remove_dominated(tuple(markers))
                            incumbent = heuristic.solve(demand, maximum_overproduction, early_catalog)
                            if incumbent.spreads:
                                evidence = self._audit_evidence(run, order, pattern_set, config, early_catalog, incumbent)
                                report = validator.validate(incumbent, early_catalog, demand, maximum_overproduction,
                                                            max_layers, usable_table_length_units=max_length,
                                                            audit_evidence=evidence)
                                if report.status == "VALIDATED_PLAN":
                                    elapsed = round((perf_counter() - total_started) * 1000, 3)
                                    self._persist_solution(run, incumbent, report,
                                        "BEST_VALIDATED_SO_FAR · plan operacional heurístico local.", 999)
                                    run.first_solution_elapsed_ms = elapsed
                                    run.first_solution_spreads = incumbent.spread_count
                                    run.first_solution_fabric_units = incumbent.total_fabric_units
                                    fast_budget_ms = float(config.get("fast_plan_budget_seconds", 5)) * 1000
                                    run.audit = {**(run.audit or {}), "anytime": {
                                        "state": "BEST_VALIDATED_SO_FAR", "first_solution_elapsed_ms": elapsed,
                                        "first_solution_spreads": incumbent.spread_count,
                                        "first_solution_fabric_units": incumbent.total_fabric_units,
                                        "fast_plan_budget_ms": fast_budget_ms,
                                        "fast_plan_budget_exceeded": elapsed > fast_budget_ms,
                                    }}
                    else:
                        row.status = "INFEASIBLE"; run.candidates_infeasible += 1
                    run.candidates_pending = sum(1 for item in round_rows if item.status == "PENDING")
                    self._heartbeat(run)
                marker_catalog = remove_dominated(tuple(markers))
                if not marker_catalog:
                    raise RuntimeError("No validated marker candidate is available for planning.")
                self._check_cancel(run, deadline); run.phase = "PLANNING_HEURISTIC"; self._heartbeat(run)
                planner_started = perf_counter()
                heuristic_solution = heuristic.solve(demand, maximum_overproduction, marker_catalog)
                solutions = (heuristic_solution,)
                engine = config.get("planner_refinement_engine", "hybrid")
                if engine in {"hybrid", "cp_sat"}:
                    run.phase = "PLANNING_REFINEMENT"; self._heartbeat(run)
                    solutions = solutions + refinement.solve_profiles(
                        demand, maximum_overproduction, marker_catalog,
                        cancellation_checkpoint=lambda: self._check_cancel(run, deadline),
                        incumbent=heuristic_solution if heuristic_solution.spreads else None,
                    )
                planning_ms += (perf_counter() - planner_started) * 1000
                self._check_cancel(run, deadline); run.phase = "VALIDATING"; self._heartbeat(run)
                validation_started = perf_counter()
                valid_solutions = []
                for solution in solutions:
                    if not solution.spreads:
                        continue
                    evidence = self._audit_evidence(run, order, pattern_set, config, marker_catalog, solution)
                    report = validator.validate(
                        solution, marker_catalog, demand, maximum_overproduction, max_layers,
                        usable_table_length_units=max_length, audit_evidence=evidence,
                    )
                    if report.status == "VALIDATED_PLAN":
                        valid_solutions.append((solution, report))
                validation_ms += (perf_counter() - validation_started) * 1000
                if valid_solutions:
                    best_valid = self._merge_valid_solutions(best_valid, valid_solutions)
                after = self._best_summary(best_valid)
                current_key = self._best_key(best_valid)
                improvement = best_key is not None and current_key is not None and current_key < best_key
                if best_key is not None:
                    no_improvement_streak = 0 if improvement else no_improvement_streak + 1
                round_stop = None
                if not novel: round_stop = "NO_NEW_CANDIDATES"
                elif not selected: round_stop = "GLOBAL_CANDIDATE_BUDGET"
                elif round_number >= int(config["candidate_generation"]["max_rounds"]): round_stop = "MAX_ROUNDS"
                elif no_improvement_streak >= int(config.get("no_improvement_rounds", 1)):
                    round_stop = "NO_MATERIAL_IMPROVEMENT"
                rounds.append({
                    "round_number": round_number, "input_residual": residual or dict(demand),
                    "generated": len(generated.candidates), "distribution": generated.distribution,
                    "deduplicated": deduplicated, "pruned": len(generated.pruned),
                    "evaluated": sum(1 for row in round_rows if row.status in {"VALIDATED_FEASIBLE", "INFEASIBLE"}),
                    "feasible": run.candidates_feasible - feasible_before,
                    "infeasible": run.candidates_infeasible - infeasible_before,
                    "not_evaluated": sum(1 for row in round_rows if row.status == "NOT_EVALUATED"),
                    "cache_hits": sum(1 for row in round_rows if row.cache_hit) - cache_before,
                    "new_marker_artifacts": sum(1 for row in round_rows if row.status == "VALIDATED_FEASIBLE" and not row.cache_hit),
                    "evaluated_candidates": [{"hash": row.candidate_hash, "composition": row.composition, "origin": row.origin}
                                             for row in round_rows if row.status in {"VALIDATED_FEASIBLE", "INFEASIBLE"}],
                    "planning_status": sorted({solution.planning_status for solution in solutions}),
                    "best_solution_before": before, "best_solution_after": after,
                    "improvement": improvement, "no_improvement_streak": no_improvement_streak,
                    "elapsed_ms": round((perf_counter() - round_started) * 1000, 3),
                    "stop_reason": round_stop,
                })
                run.audit = {**run.audit, "rounds": rounds, "candidate_pruning": pruning,
                             "candidate_funnel": funnel_audit,
                             "candidates_outside_budget": excluded, "candidate_distribution": rounds[0]["distribution"]}
                self._heartbeat(run)
                best_key = current_key
                lower_bound = ceil(sum(demand.values()) / max(1, config["candidate_generation"]["max_garments_per_marker"] * max_layers))
                if current_key is not None and current_key[1] <= lower_bound:
                    rounds[-1]["stop_reason"] = "PHYSICAL_SPREAD_LOWER_BOUND"
                    break
                if round_stop in {"NO_NEW_CANDIDATES", "GLOBAL_CANDIDATE_BUDGET", "NO_MATERIAL_IMPROVEMENT"}:
                    break

            run.elapsed_candidate_generation_ms = round(generation_ms, 3)
            run.elapsed_geometry_ms = round(geometry_ms, 3)
            run.elapsed_planner_ms = round(planning_ms, 3)
            valid_solutions = best_valid
            if not valid_solutions:
                solver_statuses = {solution.planning_status for solution in solutions}
                run.status = "INFEASIBLE" if solver_statuses == {"INFEASIBLE"} else "FAILED"
                run.error_code = "NO_FEASIBLE_PLAN" if run.status == "INFEASIBLE" else "VALIDATION_FAILED"
                run.failure_phase = run.phase
                run.phase = "FINALIZING"
                run.error_detail = (
                    "No exact production plan exists under the configured constraints."
                    if run.status == "INFEASIBLE"
                    else "No solution passed independent validation."
                )
                run.finished_at = utcnow(); self.session.commit(); return

            valid_solutions, markers = self._refine_plan_with_geometry(
                run, order, pattern_set, demand, maximum_overproduction, max_layers, max_length,
                markers, valid_solutions, heuristic, refinement, validator, config, deadline,
            )

            serialization_started = perf_counter()
            explained = self._explain(valid_solutions)
            self._check_cancel(run, deadline)
            for rank, (solution, report, explanation) in enumerate(explained, start=1):
                self._persist_solution(run, solution, report, explanation, rank)
            final_solution = explained[0][0]
            run.final_solution_elapsed_ms = round((perf_counter() - total_started) * 1000, 3)
            run.final_solution_spreads = final_solution.spread_count
            run.final_solution_fabric_units = final_solution.total_fabric_units
            serialization_ms = (perf_counter() - serialization_started) * 1000
            run.phase = "FINALIZING"
            candidate_rows = list(self.session.scalars(select(OptimizationCandidateORM).where(OptimizationCandidateORM.optimization_run_id == run.id)))
            final_catalog = remove_dominated(tuple(markers))
            run.audit = {
                **run.audit,
                "input_hash": run.input_hash,
                "pattern_hash": pattern_set.content_hash,
                "fabric_snapshot": order.catalog_snapshot["fabric_configuration"],
                "table_snapshot": order.catalog_snapshot["cutting_table_configuration"],
                "policy": {"allow_overproduction": config["allow_overproduction"], "overproduction_rate": config["overproduction_rate"]},
                "candidate_generation_config": config["candidate_generation"],
                "geometry_config": {"evaluation_budget_per_candidate": config["geometry_evaluation_budget_per_candidate"],
                                    "global_budget_seconds": config.get("geometry_budget_seconds", config["global_geometry_budget_seconds"]),
                                    "top_k_initial": config.get("geometry_top_k_initial", 20),
                                    "top_k_per_round": config.get("geometry_top_k_per_round", 10)},
                "geometry_version": final_catalog[0].geometry_engine_version,
                "planner_config": asdict(refinement.config), "planner_version": "AUTONOMOUS_ANYTIME_V1",
                "planner_refinement_engine": config.get("planner_refinement_engine", "hybrid"),
                "phase_budgets_seconds": {
                    "fast_plan": config.get("fast_plan_budget_seconds", 5),
                    "candidate_generation": config.get("candidate_generation_budget_seconds", 5),
                    "geometry": config.get("geometry_budget_seconds", config["global_geometry_budget_seconds"]),
                    "planning": config.get("planning_budget_seconds", config["planner_time_limit_seconds"]),
                    "total": config.get("total_budget_seconds", config["time_limit_seconds"]),
                },
                "marker_hashes": sorted(item.marker_hash for item in final_catalog),
                "marker_cache_hits": sum(1 for row in candidate_rows if row.cache_hit),
                "planning_variables": max(solution.variable_count for solution, _ in valid_solutions),
                "planning_constraints": max(solution.constraint_count for solution, _ in valid_solutions),
                "objective_profiles": list(PROFILES),
                "solver_status": sorted({solution.planning_status for solution, _ in valid_solutions}),
                "objective_stages": [stage for solution, _ in valid_solutions for stage in solution.objective_stages],
                "validation_certificate": [asdict(report) for _, report in valid_solutions],
                "candidate_distribution": rounds[0]["distribution"] if rounds else {},
                "candidates_outside_budget": excluded, "candidate_pruning": pruning,
                "candidate_funnel": funnel_audit, "rounds": rounds,
                "evaluated_multi_size_candidates": [
                    {"hash": row.candidate_hash, "composition": row.composition, "round": row.round_number}
                    for row in candidate_rows if row.status in {"VALIDATED_FEASIBLE", "INFEASIBLE"} and len(row.composition) > 1
                ],
                "global_geometric_optimality_claimed": False,
                "anytime": {**(run.audit or {}).get("anytime", {}),
                            "state": "BEST_VALIDATED_PLAN", "final_solution_elapsed_ms": run.final_solution_elapsed_ms,
                            "final_solution_spreads": run.final_solution_spreads,
                            "final_solution_fabric_units": run.final_solution_fabric_units},
                "performance": {"candidate_generation_ms": round(generation_ms, 3),
                                "geometry_ms": run.elapsed_geometry_ms, "planning_ms": round(planning_ms, 3),
                                "validation_ms": round(validation_ms, 3), "serialization_ms": round(serialization_ms, 3),
                                "database_ms": None, "database_measurement_note": "Commit latency is not isolated from ORM flushes."},
            }
            self._check_cancel(run)
            run.status = "TIMED_OUT" if perf_counter() >= deadline else "SUCCEEDED"
            if run.status == "TIMED_OUT":
                run.error_detail = "global_run_time_limit_exceeded; validated_solution_preserved"
                run.error_code = "OPTIMIZATION_TIMEOUT"
            run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3)
            run.finished_at = utcnow()
            self.session.commit()
            metrics.inc("optimization_runs_timed_out" if run.status == "TIMED_OUT" else "optimization_runs_succeeded")
            metrics.observe("optimization_duration_seconds", run.elapsed_total_ms / 1000)
            metrics.observe("geometry_duration_seconds", run.elapsed_geometry_ms / 1000)
            metrics.observe("planning_duration_seconds", run.elapsed_planner_ms / 1000)
            logger.info("optimization_finished", extra={"request_id": run.request_id, "order_id": run.production_order_id,
                        "run_id": run.id, "job_id": run.worker_job_id, "phase": run.phase})
        except RunCancelled:
            run.status = "CANCELLED"; run.phase = "FINALIZING"; run.finished_at = utcnow()
            run.error_code = "RUN_CANCELLED"
            run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3); self.session.commit()
            metrics.inc("optimization_runs_cancelled")
        except UseCurrentPlan:
            run.status = "SUCCEEDED_EARLY"; run.phase = "FINALIZING"; run.finished_at = utcnow()
            run.error_code = None; run.error_detail = None
            run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3)
            run.final_solution_elapsed_ms = run.elapsed_total_ms
            run.final_solution_spreads = run.first_solution_spreads
            run.final_solution_fabric_units = run.first_solution_fabric_units
            run.audit = {**(run.audit or {}), "early_stop_reason": "USE_CURRENT_PLAN"}
            self.session.commit(); metrics.inc("optimization_runs_succeeded_early")
        except RunTimedOut:
            failure_phase = run.phase
            incumbent_row = self.session.scalar(select(OptimizationSolutionORM).where(
                OptimizationSolutionORM.optimization_run_id == run.id
            ).order_by(OptimizationSolutionORM.rank).limit(1))
            run.status = "TIMED_OUT"; run.phase = "FINALIZING"; run.finished_at = utcnow()
            run.error_detail = ("global_run_time_limit_exceeded; validated_solution_preserved"
                                if incumbent_row else "global_run_time_limit_exceeded")
            run.error_code = "OPTIMIZATION_TIMEOUT"; run.failure_phase = failure_phase
            run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3)
            if incumbent_row is not None:
                run.final_solution_elapsed_ms = run.elapsed_total_ms
                run.final_solution_spreads = int(incumbent_row.metrics.get("spread_count", 0))
                run.final_solution_fabric_units = int(incumbent_row.metrics.get("total_fabric_units", 0))
                run.audit = {**(run.audit or {}), "anytime": {
                    **(run.audit or {}).get("anytime", {}), "state": "TIMED_OUT_BEST_VALIDATED",
                    "final_solution_elapsed_ms": run.final_solution_elapsed_ms,
                    "final_solution_spreads": run.final_solution_spreads,
                    "final_solution_fabric_units": run.final_solution_fabric_units,
                }}
            self.session.commit()
            metrics.inc("optimization_runs_timed_out")
        except Exception as error:
            self.session.rollback()
            run = self.session.get(OptimizationRunORM, run_id)
            if run:
                failure_phase = run.phase
                run.status = "FAILED"; run.phase = "FINALIZING"; run.error_detail = "worker_execution_failed"
                run.error_code = "OPTIMIZATION_FAILED"; run.failure_phase = failure_phase
                run.audit = {**(run.audit or {}), "failure": {"exception_class": type(error).__name__, "phase": failure_phase}}
                run.finished_at = utcnow(); run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3)
                self.session.commit()
                metrics.inc("optimization_runs_failed")
                logger.exception("optimization_failed", extra={"request_id": run.request_id, "order_id": run.production_order_id,
                                 "run_id": run.id, "job_id": run.worker_job_id, "phase": failure_phase})
            raise

    def _evaluate_candidate(self, run, order, pattern_set, composition):
        config = run.configuration
        signature = {
            "pattern": pattern_set.content_hash, "fabric": order.catalog_snapshot["fabric_configuration"]["content_hash"],
            "table": order.catalog_snapshot["cutting_table_configuration"]["content_hash"],
            "composition": composition, "geometry_budget": config["geometry_evaluation_budget_per_candidate"],
            "seed": config["seed"], "engine": "blf-exact-collision-local-search-v1",
        }
        content_key = canonical_json_hash(signature)
        cached = self.session.scalar(select(MarkerArtifactORM).where(MarkerArtifactORM.content_key == content_key))
        if cached:
            metrics.inc("marker_cache_hits")
            return _marker_from_payload(cached), True, 0.0, []
        started = perf_counter()
        response = MarkerPreviewService(self.session).generate(MarkerPreviewRequest(
            pattern_set_version_id=pattern_set.id,
            composition=[{"size_code": size, "quantity": quantity} for size, quantity in composition.items()],
            fabric_configuration_id=order.fabric_configuration_id,
            cutting_table_configuration_id=order.cutting_table_configuration_id,
            deterministic=True, seed=config["seed"], evaluation_budget=config["geometry_evaluation_budget_per_candidate"], debug=False,
        )).model_dump(mode="json")
        elapsed = round((perf_counter() - started) * 1000, 3)
        if response["status"] != "VALIDATED_FEASIBLE" or response["validation"]["status"] != "VALIDATED":
            return None, False, elapsed, response["diagnostics"]
        response["geometry_units_per_cm"] = pattern_set.geometry_units_per_cm
        artifact, cache_hit = self._persist_marker_artifact(content_key, pattern_set, order, composition, response)
        if cache_hit:
            metrics.inc("marker_cache_hits")
            return _marker_from_payload(artifact), True, elapsed, response["diagnostics"]
        metrics.inc("marker_cache_misses")
        return _marker_from_payload(artifact), False, elapsed, response["diagnostics"]

    def _persist_marker_artifact(self, content_key, pattern_set, order, composition, payload):
        """Race-safe cache insert for a validated marker artifact, keyed by
        content_key. Shared by `_evaluate_candidate` (legacy engine) and
        `_refine_plan_with_geometry` (2F.8 ALNS refinement) -- both build a
        `payload` dict shaped like `MarkerPreviewResponse.model_dump(mode="json")`
        (only the subset `_marker_from_payload` actually reads is required)
        and call this to persist/dedupe it identically.

        Returns (artifact, was_cache_hit).
        """
        artifact = MarkerArtifactORM(
            marker_hash=payload["result_hash"], content_key=content_key, pattern_hash=pattern_set.content_hash,
            fabric_hash=order.catalog_snapshot["fabric_configuration"]["content_hash"],
            table_hash=order.catalog_snapshot["cutting_table_configuration"]["content_hash"], composition=composition,
            marker_payload=payload, geometry_engine_version=payload["algorithm_version"],
            marker_search_status=payload["search_status"], validation_status=payload["validation"]["status"],
        )
        try:
            with self.session.begin_nested():
                self.session.add(artifact); self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(select(MarkerArtifactORM).where(MarkerArtifactORM.content_key == content_key))
            if existing is None:
                raise
            return existing, True
        return artifact, False

    def _bootstrap_compatible_markers(self, pattern_hash, fabric_hash, table_hash):
        """Phase 2F.8.1 Sections 3/4/6: cross-order validated-marker reuse.

        Fail-closed compatibility: only markers whose pattern/fabric/table
        content hashes ALL match exactly are returned.
        `fabric.content_hash` already bakes in usable_width/clearance/
        margins/fabric_directionality/marker_direction_policy/lay_face_mode
        (`infrastructure/seed_data.py`), and `table.content_hash` bakes in
        usable_length/max_layers -- so this 3-hash match is already a
        sufficient geometric-compatibility contract, not a bare
        composition-string match (Section 4's explicit warning against
        that). Unlike `_evaluate_candidate`'s `content_key` (which also
        hashes in seed/geometry_budget/engine -- appropriate for same-run
        dedup, wrong for cross-order reuse: a validated marker's geometry
        doesn't depend on which seed found it), this query intentionally
        ignores those fields. Ambiguous/partial matches are never returned
        (fail closed, per Section 4).
        """
        rows = self.session.scalars(
            select(MarkerArtifactORM).where(
                MarkerArtifactORM.pattern_hash == pattern_hash,
                MarkerArtifactORM.fabric_hash == fabric_hash,
                MarkerArtifactORM.table_hash == table_hash,
                MarkerArtifactORM.validation_status == "VALIDATED",
            )
        ).all()
        return remove_dominated(tuple(_marker_from_payload(row) for row in rows))

    def _refine_marker_geometry(self, run, order, pattern_set, fabric, table, config, original, budget_s):
        """Phase 2F.8 Section 12: attempts to shorten one already-validated
        marker via GlobalNestingSearch (unmodified), seeded from its OWN
        cached placements -- never re-runs the legacy engine. Returns a new,
        strictly-shorter, independently-VALIDATED ValidatedMarkerCandidate
        (persisted under a distinct content_key) on success, else None. The
        original cached artifact is never touched or overwritten.
        """
        units = pattern_set.geometry_units_per_cm
        composition = list(original.composition)
        max_iterations = 3
        signature = {
            "pattern": pattern_set.content_hash, "fabric": fabric.content_hash, "table": table.content_hash,
            "composition": composition, "seed": config["seed"], "engine": "joint-optimization-alns-v1",
            "max_iterations": max_iterations,
        }
        content_key = canonical_json_hash(signature)
        cached = self.session.scalar(select(MarkerArtifactORM).where(MarkerArtifactORM.content_key == content_key))
        if cached:
            metrics.inc("marker_cache_hits")
            candidate = _marker_from_payload(cached)
            return candidate if candidate.marker_length_units < original.marker_length_units else None

        request = build_marker_request(
            pattern_set, fabric, table, composition, deterministic=True, seed=config["seed"],
            evaluation_budget=config["geometry_evaluation_budget_per_candidate"],
        )
        by_id = {item.instance_id: item for item in request.piece_instances}
        seed_placements = tuple(_placement_from_marker_payload_row(row) for row in original.placements)
        initial_state = make_state(seed_placements, 0, None, None)
        # piece_time_budget_ms bounds each reinserted piece's candidate search
        # inside one ALNS iteration; max_runtime_s is only checked BETWEEN
        # iterations, so a single iteration can still cost up to
        # k_pieces x piece_time_budget_ms in the worst case (k in {1..4}).
        # Both are kept small here specifically so that worst case stays
        # bounded well under a realistic worker refinement budget.
        search = GlobalNestingSearch(
            request, by_id, seed=config["seed"], candidate_budget=1000, top_k=1, beam_width=1,
            piece_time_budget_ms=3_000, heartbeat=lambda _stats: self._heartbeat(run),
        )
        result = search.run(initial_state, max_iterations=max_iterations, max_runtime_s=max(1.0, budget_s))
        best = result["best_state"]
        if best.marker_length_units >= initial_state.marker_length_units:
            return None
        full_report = search.full_validator.validate(request, best.placements, request.max_length)
        if full_report.status != "VALIDATED":
            return None

        piece_area_units2 = sum(search.kernel.area_units2(item.piece.cut_polygon) for item in request.piece_instances)
        marker_area_units2 = request.usable_width * best.marker_length_units
        waste_area_units2 = marker_area_units2 - piece_area_units2
        efficiency_pct = round(piece_area_units2 / marker_area_units2 * 100, 6) if marker_area_units2 else 0.0
        payload = {
            "geometry_units_per_cm": units,
            "marker_length_cm": best.marker_length_units / units,
            "usable_width_cm": request.usable_width / units,
            "piece_area_total_cm2": piece_area_units2 / (units * units),
            "marker_area_cm2": marker_area_units2 / (units * units),
            "waste_area_cm2": waste_area_units2 / (units * units),
            "efficiency_percentage": efficiency_pct,
            "placements": [_serialize_placement_for_payload(p, units) for p in best.placements],
            "validation": {"status": full_report.status, "checks": full_report.checks,
                           "errors": list(full_report.errors), "pair_checks": full_report.pair_checks},
            "algorithm_version": "joint-optimization-alns-v1",
            "search_status": "REFINED_FEASIBLE",
            "input_hash": content_key,
            "result_hash": best.layout_hash,
            "lower_bound_length_cm": original.lower_bound_length_units / units,
        }
        artifact, cache_hit = self._persist_marker_artifact(content_key, pattern_set, order, dict(original.composition), payload)
        metrics.inc("marker_cache_hits" if cache_hit else "marker_cache_misses")
        candidate = _marker_from_payload(artifact)
        return candidate if candidate.marker_length_units < original.marker_length_units else None

    def _refine_plan_with_geometry(self, run, order, pattern_set, demand, maximum_overproduction, max_layers,
                                    max_length, markers, best_valid, heuristic, refinement, validator, config, deadline):
        """Phase 2F.8 Section 12: PLAN -> REFINE USED MARKERS -> REPLAN ->
        COMPARE -> ACCEPT ONLY IF SYSTEM OBJECTIVE IMPROVES. Only runs when
        `joint_optimization_enabled` is set and a validated plan already
        exists. Returns (best_valid, markers), both possibly extended --
        never smaller/worse: `_merge_valid_solutions` never discards a
        previously-validated distinct solution, so this can only add options
        for `_best_key`/`_explain` to choose from downstream, matching
        Section 13's per-incumbent independent-validity contract.
        """
        if not config.get("joint_optimization_enabled") or not best_valid:
            return best_valid, markers
        self._check_cancel(run, deadline)
        refinement_budget_s = float(config.get("joint_optimization_refinement_budget_seconds", 60.0))
        max_markers = int(config.get("joint_optimization_max_markers_to_refine", 3))
        refinement_deadline = perf_counter() + refinement_budget_s
        if deadline is not None:
            refinement_deadline = min(refinement_deadline, deadline)
        run.phase = "REFINING_MARKERS"; self._heartbeat(run)

        best_solution, _best_report = min(best_valid, key=lambda item: self._best_key([item]))
        by_hash = {marker.marker_hash: marker for marker in markers}
        contribution: dict[str, int] = {}
        for spread in best_solution.spreads:
            contribution[spread.marker_hash] = contribution.get(spread.marker_hash, 0) + spread.layers * spread.repeats * spread.marker_length_units
        used_markers = [by_hash[marker_hash] for marker_hash in contribution if marker_hash in by_hash]
        used_markers.sort(key=lambda marker: -contribution.get(marker.marker_hash, 0))
        used_markers = used_markers[:max_markers]

        fabric = CatalogRepository(self.session).get_fabric(order.fabric_configuration_id)
        table = CatalogRepository(self.session).get_table(order.cutting_table_configuration_id)
        refined_by_original_hash: dict[str, ValidatedMarkerCandidate] = {}
        for original in used_markers:
            if perf_counter() >= refinement_deadline:
                break
            self._check_cancel(run, deadline)
            per_marker_budget = max(1.0, refinement_deadline - perf_counter())
            candidate = self._refine_marker_geometry(run, order, pattern_set, fabric, table, config, original, per_marker_budget)
            if candidate is not None:
                refined_by_original_hash[original.marker_hash] = candidate
            self._heartbeat(run)

        plan_improved = False
        if refined_by_original_hash:
            substituted = tuple(refined_by_original_hash.get(marker.marker_hash, marker) for marker in markers)
            refined_catalog = remove_dominated(substituted)
            heuristic_solution = heuristic.solve(demand, maximum_overproduction, refined_catalog)
            candidate_solutions = (heuristic_solution,)
            engine = config.get("planner_refinement_engine", "hybrid")
            if engine in {"hybrid", "cp_sat"}:
                candidate_solutions = candidate_solutions + refinement.solve_profiles(
                    demand, maximum_overproduction, refined_catalog,
                    cancellation_checkpoint=lambda: self._check_cancel(run, deadline),
                    incumbent=heuristic_solution if heuristic_solution.spreads else None,
                )
            candidate_valid = []
            for solution in candidate_solutions:
                if not solution.spreads:
                    continue
                evidence = self._audit_evidence(run, order, pattern_set, config, refined_catalog, solution)
                report = validator.validate(solution, refined_catalog, demand, maximum_overproduction, max_layers,
                                             usable_table_length_units=max_length, audit_evidence=evidence)
                if report.status == "VALIDATED_PLAN":
                    candidate_valid.append((solution, report))
            if candidate_valid:
                key_before = self._best_key(best_valid)
                best_valid = self._merge_valid_solutions(best_valid, candidate_valid)
                markers = list(substituted)
                plan_improved = self._best_key(best_valid) < key_before

        run.audit = {**(run.audit or {}), "joint_optimization": {
            "markers_considered": len(used_markers), "markers_refined": len(refined_by_original_hash),
            "plan_improved": plan_improved,
        }}
        self._heartbeat(run)
        return best_valid, markers

    @staticmethod
    def _size_statistics(pattern_set, usable_width, available_length=None):
        areas, fits = {}, {}
        for piece in pattern_set.pieces:
            path = canonical_path(piece.operational_geometry["coordinates"][0])
            areas[piece.size_code] = areas.get(piece.size_code, 0) + abs(signed_area2(path)) // 2 * piece.quantity
            min_x, min_y, max_x, max_y = path_bbox(path)
            piece_fits = max_y - min_y <= usable_width
            if available_length is not None:
                piece_fits = piece_fits and max_x - min_x <= available_length
            fits[piece.size_code] = fits.get(piece.size_code, True) and piece_fits
        return areas, fits

    @staticmethod
    def _merge_valid_solutions(existing, additions):
        """Retain every distinct validated incumbent; later rounds cannot regress it."""
        merged = {solution.fingerprint: (solution, report) for solution, report in existing}
        for solution, report in additions:
            merged[solution.fingerprint] = (solution, report)
        return list(merged.values())

    @staticmethod
    def _best_key(valid_solutions):
        if not valid_solutions:
            return None
        solution = min((item[0] for item in valid_solutions), key=lambda item: (
            item.spread_count, -item.primary_spread_covered_garments,
            item.marker_design_count, item.marker_change_count, item.total_fabric_units,
            item.total_waste_units2, item.total_overproduction, item.max_overproduction,
        ))
        # Index zero is the (validated) shortage and is therefore always zero.
        # Keeping it explicit documents the required lexicographic contract.
        return (0, solution.spread_count, -solution.primary_spread_covered_garments,
                solution.marker_design_count, solution.marker_change_count, solution.total_fabric_units,
                solution.total_waste_units2, solution.total_overproduction, solution.max_overproduction)

    @classmethod
    def _best_summary(cls, valid_solutions):
        key = cls._best_key(valid_solutions)
        if key is None:
            return None
        return {"shortage": key[0], "spread_count": key[1],
                "primary_spread_covered_garments": -key[2], "marker_design_count": key[3],
                "total_fabric_units": key[5], "waste_units2": key[6],
                "total_overproduction": key[7], "max_overproduction": key[8]}

    @staticmethod
    def _planning_residual(demand, valid_solutions):
        if not valid_solutions:
            return dict(demand)
        solution = min((item[0] for item in valid_solutions), key=lambda item: (
            item.spread_count, -item.primary_spread_covered_garments, item.total_fabric_units,
        ))
        if not solution.spreads:
            return dict(demand)
        return dict(solution.spreads[0].remaining_demand_after)

    @staticmethod
    def _audit_evidence(run, order, pattern_set, config, markers, solution):
        return {
            "order_hash": order.snapshot_hash, "input_hash": run.input_hash,
            "pattern_hash": pattern_set.content_hash,
            "fabric_snapshot": order.catalog_snapshot["fabric_configuration"],
            "table_snapshot": order.catalog_snapshot["cutting_table_configuration"],
            "policy": {"allow_overproduction": config["allow_overproduction"], "overproduction_rate": config["overproduction_rate"]},
            "candidate_generation_config": config["candidate_generation"],
            "geometry_config": {"evaluation_budget_per_candidate": config["geometry_evaluation_budget_per_candidate"]},
            "geometry_version": markers[0].geometry_engine_version,
            "planner_config": {"max_layers": int(order.catalog_snapshot["cutting_table_configuration"]["max_layers"]),
                               "time_limit_seconds": config.get("planning_budget_seconds", config["planner_time_limit_seconds"]),
                               "beam_width": config.get("beam_width", 10), "seed": config["seed"]},
            "planner_version": ("OPERATIONAL_HEURISTIC_BEAM_V1" if solution.solution_origin == "OPERATIONAL_HEURISTIC"
                                else "OR_TOOLS_CP_SAT_LEXICOGRAPHIC_V1"), "seed": config["seed"],
            "marker_hashes": sorted(marker.marker_hash for marker in markers),
            "solver_status": solution.planning_status, "objective_stages": list(solution.objective_stages),
            "validation_certificate": [marker.validation_certificate for marker in markers],
        }

    def _check_cancel(self, run, deadline=None):
        if deadline is not None and perf_counter() >= deadline:
            raise RunTimedOut()
        flags = self.session.execute(select(
            OptimizationRunORM.cancel_requested, OptimizationRunORM.use_current_plan_requested,
        ).where(OptimizationRunORM.id == run.id)).one()
        run.cancel_requested = bool(flags[0])
        run.use_current_plan_requested = bool(flags[1])
        if flags[0]:
            raise RunCancelled()
        if flags[1]:
            raise UseCurrentPlan()

    def _heartbeat(self, run):
        run.heartbeat_at = utcnow(); self.session.commit()

    @staticmethod
    def _explain(valid_solutions):
        rows = []
        min_spreads = min((solution for solution, _ in valid_solutions), key=lambda item: (item.spread_count, item.total_fabric_units))
        for solution, report in valid_solutions:
            meters = solution.total_fabric_units / 1000 / 100
            text = (
                f"Utiliza {solution.marker_design_count} diseños de marker y {solution.spread_count} tendidos físicos; "
                f"el primer corte cubre {solution.primary_spread_coverage_percentage:.1f}% del pedido, "
                f"consume {meters:.2f} m lineales y produce {solution.total_overproduction} prendas adicionales."
            )
            if solution.fingerprint != min_spreads.fingerprint:
                saved = (min_spreads.total_fabric_units - solution.total_fabric_units) / 1000 / 100
                spread_delta = solution.spread_count - min_spreads.spread_count
                text += f" Frente a la alternativa de menos tendidos usa {spread_delta:+d} tendidos y cambia el consumo en {-saved:+.2f} m."
            rows.append((solution, report, text))
        # A FEASIBLE profile label is not a proof that it beat another profile's incumbent.
        rows.sort(key=lambda item: (
            0 if "MAX_ORDER_PER_CUT" in item[0].profiles else 1,
            item[0].spread_count, -item[0].primary_spread_covered_garments,
            item[0].marker_design_count, item[0].marker_change_count,
            item[0].total_fabric_units, item[0].total_waste_units2,
            item[0].total_overproduction, item[0].max_overproduction,
        ))
        return rows

    def _persist_solution(self, run, solution, report, explanation, rank):
        existing = self.session.scalar(select(OptimizationSolutionORM).where(
            OptimizationSolutionORM.optimization_run_id == run.id,
            OptimizationSolutionORM.fingerprint == solution.fingerprint,
        ).options(selectinload(OptimizationSolutionORM.profiles)))
        if existing is not None:
            existing.rank = min(existing.rank, rank)
            existing.explanation = explanation
            known = {item.profile for item in existing.profiles}
            for profile in solution.profiles:
                if profile not in known:
                    existing.profiles.append(OptimizationSolutionProfileORM(
                        id=str(uuid4()), profile=profile,
                        objective_stages=[stage for stage in solution.objective_stages if stage.get("profile") == profile],
                    ))
            self.session.flush()
            return existing
        row = OptimizationSolutionORM(
            id=str(uuid4()), optimization_run_id=run.id, solution_hash=solution.solution_hash,
            fingerprint=solution.fingerprint, rank=rank, planning_status=solution.planning_status,
            planning_optimality=solution.planning_optimality, solution_origin=solution.solution_origin,
            metrics={
                "requested_by_size": solution.requested_by_size, "produced_by_size": solution.produced_by_size,
                "overproduction_by_size": solution.overproduction_by_size, "total_overproduction": solution.total_overproduction,
                "max_overproduction": solution.max_overproduction, "total_fabric_units": solution.total_fabric_units,
                "total_linear_consumption_cm": solution.total_fabric_units / 1000,
                "total_linear_consumption_m": solution.total_fabric_units / 1000 / 100,
                "total_waste_units2": solution.total_waste_units2, "spread_count": solution.spread_count,
                "marker_design_count": solution.marker_design_count,
                "marker_change_count": solution.marker_change_count,
                "max_pieces_per_marker": solution.max_pieces_per_marker,
                "average_pieces_per_marker": solution.average_pieces_per_marker,
                "physical_spreads": solution.spread_count,
                "distinct_marker_designs": solution.marker_design_count,
                "marker_changeovers": solution.marker_change_count,
                "primary_spread_covered_garments": solution.primary_spread_covered_garments,
                "primary_spread_coverage_percentage": solution.primary_spread_coverage_percentage,
                "global_efficiency_percentage": solution.global_efficiency_percentage,
                "variable_count": solution.variable_count, "constraint_count": solution.constraint_count,
                "solver_time_ms": solution.solver_time_ms,
            }, validation_certificate=asdict(report), explanation=explanation,
        )
        for profile in solution.profiles:
            row.profiles.append(OptimizationSolutionProfileORM(
                id=str(uuid4()), profile=profile,
                objective_stages=[stage for stage in solution.objective_stages if stage.get("profile") == profile],
            ))
        for sequence, spread in enumerate(solution.spreads, start=1):
            row.spreads.append(SpreadORM(
                id=str(uuid4()), marker_hash=spread.marker_hash, spread_hash=spread.spread_hash, sequence=sequence,
                layers=spread.layers, repeats=spread.repeats, composition=dict(spread.composition),
                production_by_size=spread.production_by_size, marker_length_units=spread.marker_length_units,
                fabric_consumption_units=spread.fabric_consumption_units,
                marker_efficiency_percentage=spread.marker_efficiency_percentage,
                marker_search_status=spread.marker_search_status, validation_status="VALIDATED",
                useful_garments=spread.useful_garments,
                order_coverage_percentage=spread.order_coverage_percentage,
                remaining_demand_after=spread.remaining_demand_after,
                is_primary=spread.is_primary,
            ))
        for size in SIZE_CODES:
            requested = solution.requested_by_size.get(size, 0); produced = solution.produced_by_size.get(size, 0)
            row.size_results.append(SizeResultORM(id=str(uuid4()), size_code=size, requested=requested, produced=produced, overproduction=produced-requested))
        self.session.add(row); self.session.flush()
        return row
