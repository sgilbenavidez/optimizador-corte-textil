from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.candidate_generator import CandidateCompositionGenerator, SIZE_ORDER
from costura_optima.domain.integer_kernel import canonical_json_hash, canonical_path, path_bbox, signed_area2
from costura_optima.domain.production_models import (
    CandidateGenerationConfig, PlanningConfig, PlanningSolution, ValidatedMarkerCandidate,
)
from costura_optima.domain.production_plan_validator import IndependentProductionPlanValidator
from costura_optima.domain.production_planner import ProductionPlanner
from costura_optima.infrastructure.db_models import (
    MarkerArtifactORM, OptimizationCandidateORM, OptimizationRunORM, OptimizationSolutionORM,
    OptimizationSolutionProfileORM, PatternSetVersionORM, ProductionOrderORM, SizeResultORM, SpreadORM,
)


SIZE_CODES = ("XS", "S", "M", "L", "XL", "XXL", "XXXL")


class RunCancelled(Exception):
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


def remove_dominated(markers: tuple[ValidatedMarkerCandidate, ...]) -> tuple[ValidatedMarkerCandidate, ...]:
    best = {}
    for marker in markers:
        key = marker.composition
        current = best.get(key)
        ranking = (marker.marker_length_units, marker.waste_area_units2, marker.marker_hash)
        if current is None or ranking < (current.marker_length_units, current.waste_area_units2, current.marker_hash):
            best[key] = marker
    return tuple(sorted(best.values(), key=lambda item: item.composition))


class PlanningCoordinator:
    def __init__(self, session: Session):
        self.session = session

    def execute(self, run_id: str) -> None:
        total_started = perf_counter()
        run = self.session.get(OptimizationRunORM, run_id)
        if run is None:
            raise RuntimeError(f"Optimization run not found: {run_id}")
        try:
            run.status = "RUNNING"; run.phase = "GENERATING_CANDIDATES"; run.started_at = utcnow()
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
            usable_width = round(float(order.catalog_snapshot["fabric_configuration"]["usable_width_cm"]) * units)
            max_length = round(float(order.catalog_snapshot["cutting_table_configuration"]["usable_length_cm"]) * units)
            size_area, size_fits = self._size_statistics(pattern_set, usable_width)
            generator = CandidateCompositionGenerator(CandidateGenerationConfig(**config["candidate_generation"]))
            generated = generator.generate(demand, maximum_overproduction, size_area, size_fits, usable_width, max_length)
            run.elapsed_candidate_generation_ms = generated.elapsed_ms
            for candidate in generated.candidates:
                self.session.add(OptimizationCandidateORM(
                    id=str(uuid4()), optimization_run_id=run.id, candidate_hash=candidate.candidate_hash,
                    composition=dict(candidate.composition), round_number=candidate.round_number, origin=candidate.origin,
                    status="PENDING", diagnostics=[], cache_hit=False, geometry_elapsed_ms=0,
                ))
            self.session.flush()
            run.candidates_generated = len(generated.candidates) + len(generated.pruned)
            run.candidates_pending = len(generated.candidates)
            run.audit = {**run.audit, "candidate_pruning": list(generated.pruned), "rounds": []}
            self._heartbeat(run)

            run.phase = "NESTING"
            geometry_started = perf_counter()
            evaluated_limit = config["max_marker_candidates"]
            global_seconds = config["global_geometry_budget_seconds"]
            candidate_rows = list(self.session.scalars(
                select(OptimizationCandidateORM).where(OptimizationCandidateORM.optimization_run_id == run.id)
                .order_by(OptimizationCandidateORM.round_number, OptimizationCandidateORM.origin, OptimizationCandidateORM.candidate_hash)
            ))
            markers = []
            for index, candidate_row in enumerate(candidate_rows):
                self._check_cancel(run)
                elapsed = perf_counter() - geometry_started
                if index >= evaluated_limit or elapsed >= global_seconds:
                    candidate_row.status = "NOT_EVALUATED"
                    candidate_row.diagnostics = ["global_geometry_budget"]
                    run.candidates_not_evaluated += 1
                    run.candidates_pending = len(candidate_rows) - run.candidates_evaluated - run.candidates_not_evaluated
                    continue
                marker, cache_hit, marker_elapsed, diagnostics = self._evaluate_candidate(
                    run, order, pattern_set, candidate_row.composition
                )
                candidate_row.cache_hit = cache_hit; candidate_row.geometry_elapsed_ms = marker_elapsed
                candidate_row.diagnostics = diagnostics; run.candidates_evaluated += 1
                if marker:
                    candidate_row.status = "VALIDATED_FEASIBLE"; candidate_row.marker_hash = marker.marker_hash
                    run.candidates_feasible += 1; markers.append(marker); run.best_feasible_found = True
                else:
                    candidate_row.status = "INFEASIBLE"; run.candidates_infeasible += 1
                run.candidates_pending = len(candidate_rows) - run.candidates_evaluated - run.candidates_not_evaluated
                self._heartbeat(run)
            run.elapsed_geometry_ms = round((perf_counter() - geometry_started) * 1000, 3)
            marker_catalog = remove_dominated(tuple(markers))
            if not marker_catalog:
                raise RuntimeError("No validated marker candidate is available for planning.")

            self._check_cancel(run); run.phase = "PLANNING"; self._heartbeat(run)
            planner_started = perf_counter()
            planner = ProductionPlanner(PlanningConfig(
                max_layers=int(order.catalog_snapshot["cutting_table_configuration"]["max_layers"]),
                time_limit_seconds=config["planner_time_limit_seconds"] / 3,
                deterministic=True, seed=config["seed"],
            ))
            solutions = planner.solve_profiles(
                demand, maximum_overproduction, marker_catalog,
                cancellation_checkpoint=lambda: self._check_cancel(run),
            )
            run.elapsed_planner_ms = round((perf_counter() - planner_started) * 1000, 3)
            self._check_cancel(run); run.phase = "VALIDATING"; self._heartbeat(run)
            validator = IndependentProductionPlanValidator()
            valid_solutions = []
            for solution in solutions:
                if not solution.spreads:
                    continue
                report = validator.validate(solution, marker_catalog, demand, maximum_overproduction, planner.config.max_layers)
                if report.status == "VALIDATED_PLAN":
                    valid_solutions.append((solution, report))
            if not valid_solutions:
                solver_statuses = {solution.planning_status for solution in solutions}
                run.status = "INFEASIBLE" if solver_statuses == {"INFEASIBLE"} else "FAILED"
                run.phase = "FINALIZING"
                run.error_detail = (
                    "No exact production plan exists under the configured constraints."
                    if run.status == "INFEASIBLE"
                    else "No solution passed independent validation."
                )
                run.finished_at = utcnow(); self.session.commit(); return

            explained = self._explain(valid_solutions)
            for rank, (solution, report, explanation) in enumerate(explained, start=1):
                self._persist_solution(run, solution, report, explanation, rank)
            run.phase = "FINALIZING"
            run.audit = {
                **run.audit,
                "pattern_hash": pattern_set.content_hash,
                "fabric_hash": order.catalog_snapshot["fabric_configuration"]["content_hash"],
                "table_hash": order.catalog_snapshot["cutting_table_configuration"]["content_hash"],
                "geometry_engine": marker_catalog[0].geometry_engine_version,
                "planner": "OR_TOOLS_CP_SAT_LEXICOGRAPHIC_V1",
                "marker_hashes": [item.marker_hash for item in marker_catalog],
                "marker_cache_hits": sum(1 for row in candidate_rows if row.cache_hit),
                "planning_variables": max(solution.variable_count for solution, _ in valid_solutions),
                "planning_constraints": max(solution.constraint_count for solution, _ in valid_solutions),
                "objective_profiles": list(("MIN_FABRIC", "MIN_SPREADS", "BALANCED")),
                "global_geometric_optimality_claimed": False,
            }
            run.status = "SUCCEEDED"; run.finished_at = utcnow()
            run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3)
            self.session.commit()
        except RunCancelled:
            run.status = "CANCELLED"; run.phase = "FINALIZING"; run.finished_at = utcnow()
            run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3); self.session.commit()
        except Exception as error:
            self.session.rollback()
            run = self.session.get(OptimizationRunORM, run_id)
            if run:
                run.status = "FAILED"; run.phase = "FINALIZING"; run.error_detail = f"{type(error).__name__}: {error}"
                run.finished_at = utcnow(); run.elapsed_total_ms = round((perf_counter() - total_started) * 1000, 3)
                self.session.commit()
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
        artifact = MarkerArtifactORM(
            marker_hash=response["result_hash"], content_key=content_key, pattern_hash=pattern_set.content_hash,
            fabric_hash=order.catalog_snapshot["fabric_configuration"]["content_hash"],
            table_hash=order.catalog_snapshot["cutting_table_configuration"]["content_hash"], composition=composition,
            marker_payload=response, geometry_engine_version=response["algorithm_version"],
            marker_search_status=response["search_status"], validation_status=response["validation"]["status"],
        )
        self.session.add(artifact); self.session.flush()
        return _marker_from_payload(artifact), False, elapsed, response["diagnostics"]

    @staticmethod
    def _size_statistics(pattern_set, usable_width):
        areas, fits = {}, {}
        for piece in pattern_set.pieces:
            path = canonical_path(piece.operational_geometry["coordinates"][0])
            areas[piece.size_code] = areas.get(piece.size_code, 0) + abs(signed_area2(path)) // 2 * piece.quantity
            _, min_y, _, max_y = path_bbox(path)
            fits[piece.size_code] = fits.get(piece.size_code, True) and max_y - min_y <= usable_width
        return areas, fits

    def _check_cancel(self, run):
        cancel_requested = self.session.scalar(
            select(OptimizationRunORM.cancel_requested).where(OptimizationRunORM.id == run.id)
        )
        run.cancel_requested = bool(cancel_requested)
        if cancel_requested:
            raise RunCancelled()

    def _heartbeat(self, run):
        run.heartbeat_at = utcnow(); self.session.commit()

    @staticmethod
    def _explain(valid_solutions):
        rows = []
        min_spreads = min((solution for solution, _ in valid_solutions), key=lambda item: (item.spread_count, item.total_fabric_units))
        for solution, report in valid_solutions:
            meters = solution.total_fabric_units / 1000 / 100
            text = f"Utiliza {solution.spread_count} tendidos y consume {meters:.2f} m lineales; produce {solution.total_overproduction} prendas adicionales."
            if solution.fingerprint != min_spreads.fingerprint:
                saved = (min_spreads.total_fabric_units - solution.total_fabric_units) / 1000 / 100
                spread_delta = solution.spread_count - min_spreads.spread_count
                text += f" Frente a la alternativa de menos tendidos usa {spread_delta:+d} tendidos y cambia el consumo en {-saved:+.2f} m."
            rows.append((solution, report, text))
        rows.sort(key=lambda item: ("MIN_FABRIC" not in item[0].profiles, item[0].total_fabric_units, item[0].spread_count))
        return rows

    def _persist_solution(self, run, solution, report, explanation, rank):
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
                "global_efficiency_percentage": solution.global_efficiency_percentage,
                "variable_count": solution.variable_count, "constraint_count": solution.constraint_count,
                "solver_time_ms": solution.solver_time_ms,
            }, validation_certificate=asdict(report), explanation=explanation,
        )
        for profile in solution.profiles:
            row.profiles.append(OptimizationSolutionProfileORM(id=str(uuid4()), profile=profile, objective_stages=list(solution.objective_stages)))
        for sequence, spread in enumerate(solution.spreads, start=1):
            row.spreads.append(SpreadORM(
                id=str(uuid4()), marker_hash=spread.marker_hash, spread_hash=spread.spread_hash, sequence=sequence,
                layers=spread.layers, repeats=spread.repeats, composition=dict(spread.composition),
                production_by_size=spread.production_by_size, marker_length_units=spread.marker_length_units,
                fabric_consumption_units=spread.fabric_consumption_units,
                marker_efficiency_percentage=spread.marker_efficiency_percentage,
                marker_search_status=spread.marker_search_status, validation_status="VALIDATED",
            ))
        for size in SIZE_CODES:
            requested = solution.requested_by_size.get(size, 0); produced = solution.produced_by_size.get(size, 0)
            row.size_results.append(SizeResultORM(id=str(uuid4()), size_code=size, requested=requested, produced=produced, overproduction=produced-requested))
        self.session.add(row); self.session.flush()
