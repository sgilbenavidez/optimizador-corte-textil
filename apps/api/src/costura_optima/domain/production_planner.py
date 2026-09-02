from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Callable

from ortools.sat.python import cp_model

from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.domain.production_models import (
    PlannedSpread,
    PlanningConfig,
    PlanningSolution,
    ValidatedMarkerCandidate,
)


PROFILES = {
    "MIN_FABRIC": ("total_overproduction", "fabric", "waste", "spreads"),
    "MIN_SPREADS": ("total_overproduction", "spreads", "fabric", "waste"),
    "BALANCED": ("max_overproduction", "total_overproduction", "fabric", "spreads", "waste"),
}


class ProductionPlanner:
    def __init__(self, config: PlanningConfig):
        self.config = config

    def solve_profiles(
        self,
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        markers: tuple[ValidatedMarkerCandidate, ...],
        cancellation_checkpoint: Callable[[], None] | None = None,
    ) -> tuple[PlanningSolution, ...]:
        raw = []
        for profile in PROFILES:
            if cancellation_checkpoint:
                cancellation_checkpoint()
            raw.append(self._solve_profile(
                profile, demand, maximum_overproduction, markers, cancellation_checkpoint,
            ))
        unique: dict[str, PlanningSolution] = {}
        for solution in raw:
            prior = unique.get(solution.fingerprint)
            if prior:
                unique[solution.fingerprint] = replace(prior, profiles=tuple(sorted(set(prior.profiles + solution.profiles))))
            else:
                unique[solution.fingerprint] = solution
        return tuple(unique.values())

    def _build_model(self, demand, maximum_overproduction, markers, fixed):
        model = cp_model.CpModel()
        sizes = tuple(sorted(demand))
        allowed_total = sum(demand[size] + maximum_overproduction[size] for size in sizes)
        variables: dict[tuple[int, int], cp_model.IntVar] = {}
        production_terms = {size: [] for size in sizes}
        fabric_terms, waste_terms, spread_terms = [], [], []
        for marker_index, marker in enumerate(markers):
            composition = dict(marker.composition)
            for layers in range(1, self.config.max_layers + 1):
                upper = max(1, allowed_total // max(1, sum(composition.values()) * layers) + 1)
                variable = model.new_int_var(0, upper, f"x_{marker_index}_{layers}")
                variables[(marker_index, layers)] = variable
                for size in sizes:
                    production_terms[size].append(variable * composition.get(size, 0) * layers)
                fabric_terms.append(variable * marker.marker_length_units * layers)
                waste_terms.append(variable * marker.waste_area_units2 * layers)
                spread_terms.append(variable)
        produced = {size: sum(production_terms[size]) for size in sizes}
        for size in sizes:
            model.add(produced[size] >= demand[size])
            model.add(produced[size] <= demand[size] + maximum_overproduction[size])
        total_overproduction = sum(produced[size] - demand[size] for size in sizes)
        max_overproduction = model.new_int_var(0, max(maximum_overproduction.values(), default=0), "max_overproduction")
        for size in sizes:
            model.add(max_overproduction >= produced[size] - demand[size])
        expressions = {
            "total_overproduction": total_overproduction,
            "max_overproduction": max_overproduction,
            "fabric": sum(fabric_terms),
            "waste": sum(waste_terms),
            "spreads": sum(spread_terms),
        }
        for name, value in fixed.items():
            model.add(expressions[name] == value)
        return model, variables, produced, expressions

    def _solve_profile(self, profile, demand, maximum_overproduction, markers, cancellation_checkpoint=None):
        started = perf_counter()
        fixed: dict[str, int] = {}
        stages = []
        final = None
        stage_limit = max(0.1, self.config.time_limit_seconds / max(1, len(PROFILES[profile])))
        for objective in PROFILES[profile]:
            if cancellation_checkpoint:
                cancellation_checkpoint()
            model, variables, produced, expressions = self._build_model(demand, maximum_overproduction, markers, fixed)
            model.minimize(expressions[objective])
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = stage_limit
            solver.parameters.num_search_workers = 1 if self.config.deterministic else 0
            solver.parameters.random_seed = self.config.seed
            status = solver.solve(model)
            status_name = solver.status_name(status)
            stage = {"objective": objective, "status": status_name, "elapsed_ms": round(solver.wall_time * 1000, 3)}
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                stages.append(stage)
                if final is None:
                    return self._infeasible(profile, status_name, stages, started, len(variables), len(model.proto.constraints))
                break
            value = int(solver.value(expressions[objective]))
            stage["value"] = value
            stages.append(stage)
            final = (solver, variables, produced, expressions, status, len(model.proto.constraints))
            if status != cp_model.OPTIMAL:
                break
            fixed[objective] = value
        assert final is not None
        solver, variables, produced, expressions, status, constraint_count = final
        spread_rows = []
        produced_values = {size: int(solver.value(expression)) for size, expression in produced.items()}
        for (marker_index, layers), variable in sorted(variables.items()):
            repeats = int(solver.value(variable))
            if repeats == 0:
                continue
            marker = markers[marker_index]
            composition = dict(marker.composition)
            production = {size: quantity * layers * repeats for size, quantity in composition.items()}
            spread_payload = {"marker": marker.marker_hash, "layers": layers, "repeats": repeats, "production": production}
            spread_rows.append(PlannedSpread(
                marker_hash=marker.marker_hash,
                composition=marker.composition,
                layers=layers,
                repeats=repeats,
                marker_length_units=marker.marker_length_units,
                fabric_consumption_units=marker.marker_length_units * layers * repeats,
                production_by_size=production,
                marker_efficiency_percentage=marker.efficiency_percentage,
                marker_search_status=marker.marker_search_status,
                spread_hash=canonical_json_hash(spread_payload),
            ))
        over = {size: produced_values[size] - demand[size] for size in demand}
        total_fabric = sum(item.fabric_consumption_units for item in spread_rows)
        total_waste = sum(markers_by_hash(markers)[item.marker_hash].waste_area_units2 * item.layers * item.repeats for item in spread_rows)
        used_area = sum(markers_by_hash(markers)[item.marker_hash].piece_area_units2 * item.layers * item.repeats for item in spread_rows)
        total_area = used_area + total_waste
        fingerprint_payload = {
            "production": produced_values,
            "spreads": [(row.marker_hash, row.layers, row.repeats) for row in spread_rows],
            "fabric": total_fabric,
        }
        fingerprint = canonical_json_hash(fingerprint_payload)
        solution_hash = canonical_json_hash({"solution_fingerprint": fingerprint})
        return PlanningSolution(
            profiles=(profile,), planning_status=solver.status_name(status),
            planning_optimality="SOLVER_OPTIMAL" if all(stage["status"] == "OPTIMAL" for stage in stages) else "SOLVER_FEASIBLE",
            solution_origin="CP_SAT_VALIDATED_CATALOG", spreads=tuple(spread_rows), requested_by_size=dict(demand),
            produced_by_size=produced_values, overproduction_by_size=over, total_overproduction=sum(over.values()),
            max_overproduction=max(over.values(), default=0), total_fabric_units=total_fabric,
            total_waste_units2=total_waste, spread_count=sum(row.repeats for row in spread_rows),
            global_efficiency_percentage=round(used_area / total_area * 100, 6) if total_area else 0.0,
            objective_stages=tuple(stages), variable_count=len(variables), constraint_count=constraint_count,
            solver_time_ms=round((perf_counter() - started) * 1000, 3), fingerprint=fingerprint,
            solution_hash=solution_hash,
        )

    @staticmethod
    def _infeasible(profile, status, stages, started, variables, constraints):
        payload = {"profile": profile, "status": status, "stages": stages}
        return PlanningSolution(
            profiles=(profile,), planning_status=status, planning_optimality=status, solution_origin="NONE", spreads=(),
            requested_by_size={}, produced_by_size={}, overproduction_by_size={}, total_overproduction=0,
            max_overproduction=0, total_fabric_units=0, total_waste_units2=0, spread_count=0,
            global_efficiency_percentage=0, objective_stages=tuple(stages), variable_count=variables,
            constraint_count=constraints, solver_time_ms=round((perf_counter() - started) * 1000, 3),
            fingerprint=canonical_json_hash(payload), solution_hash=canonical_json_hash(payload),
        )


def markers_by_hash(markers):
    return {marker.marker_hash: marker for marker in markers}
