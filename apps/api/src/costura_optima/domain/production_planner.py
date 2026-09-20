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
from costura_optima.domain.candidate_generator import useful_coverage
from costura_optima.domain.planning_profiles import MAXIMIZE_OBJECTIVES, PROFILES


class ProductionPlanner:
    def __init__(self, config: PlanningConfig):
        self.config = config

    def solve_profiles(
        self,
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        markers: tuple[ValidatedMarkerCandidate, ...],
        cancellation_checkpoint: Callable[[], None] | None = None,
        initial_incumbent: PlanningSolution | None = None,
    ) -> tuple[PlanningSolution, ...]:
        raw = []
        solve_order = tuple(profile for profile in PROFILES if profile != "MAX_ORDER_PER_CUT")
        phase_started = perf_counter()
        profile_count = len(solve_order) + 1
        for profile_index, profile in enumerate(solve_order):
            if cancellation_checkpoint:
                cancellation_checkpoint()
            remaining_budget = max(0.1, self.config.time_limit_seconds - (perf_counter() - phase_started))
            remaining_profiles = profile_count - profile_index
            raw.append(self._solve_profile(
                profile, demand, maximum_overproduction, markers, cancellation_checkpoint,
                profile_time_limit=remaining_budget / remaining_profiles,
            ))
        operational_incumbent = min(
            (solution for solution in (*raw, initial_incumbent) if solution is not None and solution.spreads),
            key=lambda item: (item.spread_count, -item.primary_spread_covered_garments,
                              item.marker_design_count, item.marker_change_count,
                              item.total_fabric_units, item.total_overproduction, item.total_waste_units2),
            default=None,
        )
        fallback_hint = self._single_size_fallback_hint(demand, markers)
        fallback_spreads = sum(fallback_hint.values()) if fallback_hint else None
        incumbent_hint = self._hint_from_solution(markers, operational_incumbent)
        incumbent_spreads = operational_incumbent.spread_count if operational_incumbent else None
        if fallback_spreads is not None and (incumbent_spreads is None or fallback_spreads < incumbent_spreads):
            incumbent_hint = fallback_hint
            spread_upper_bound = fallback_spreads
        else:
            spread_upper_bound = incumbent_spreads
        remaining_budget = max(0.1, self.config.time_limit_seconds - (perf_counter() - phase_started))
        raw.append(self._solve_profile(
            "MAX_ORDER_PER_CUT", demand, maximum_overproduction, markers,
            cancellation_checkpoint, incumbent_hint, operational_incumbent, spread_upper_bound,
            profile_time_limit=remaining_budget,
        ))
        unique: dict[str, PlanningSolution] = {}
        for solution in raw:
            prior = unique.get(solution.fingerprint)
            if prior:
                merged_profiles = set(prior.profiles + solution.profiles)
                unique[solution.fingerprint] = replace(
                    prior,
                    profiles=tuple(name for name in PROFILES if name in merged_profiles),
                    objective_stages=prior.objective_stages + solution.objective_stages,
                )
            else:
                unique[solution.fingerprint] = solution
        return tuple(unique.values())

    def _build_model(self, demand, maximum_overproduction, markers, fixed, greedy_hint=None, incumbent=None, spread_upper_bound=None):
        model = cp_model.CpModel()
        sizes = tuple(sorted(demand))
        allowed_total = sum(demand[size] + maximum_overproduction[size] for size in sizes)
        variables: dict[tuple[int, int], cp_model.IntVar] = {}
        marker_used: dict[int, cp_model.IntVar] = {}
        primary_choice: dict[tuple[int, int], cp_model.IntVar] = {}
        production_terms = {size: [] for size in sizes}
        fabric_terms, waste_terms, spread_terms = [], [], []
        for marker_index, marker in enumerate(markers):
            composition = dict(marker.composition)
            used = model.new_bool_var(f"marker_used_{marker_index}")
            marker_used[marker_index] = used
            marker_variables = []
            directed = marker.candidate_layers or ()
            layer_order = tuple(dict.fromkeys((*directed, *range(1, self.config.max_layers + 1))))
            for layers in layer_order:
                upper = max(1, allowed_total // max(1, sum(composition.values()) * layers) + 1)
                variable = model.new_int_var(0, upper, f"x_{marker_index}_{layers}")
                variables[(marker_index, layers)] = variable
                marker_variables.append(variable)
                model.add(variable <= upper * used)
                primary = model.new_bool_var(f"primary_{marker_index}_{layers}")
                primary_choice[(marker_index, layers)] = primary
                model.add(variable >= primary)
                for size in sizes:
                    production_terms[size].append(variable * composition.get(size, 0) * layers)
                fabric_terms.append(variable * marker.marker_length_units * layers)
                waste_terms.append(variable * marker.waste_area_units2 * layers)
                spread_terms.append(variable)
            model.add(sum(marker_variables) >= used)
        model.add(sum(primary_choice.values()) == 1)
        produced = {size: sum(production_terms[size]) for size in sizes}
        for size in sizes:
            model.add(produced[size] >= demand[size])
            model.add(produced[size] <= demand[size] + maximum_overproduction[size])
        total_overproduction = sum(produced[size] - demand[size] for size in sizes)
        max_overproduction = model.new_int_var(0, max(maximum_overproduction.values(), default=0), "max_overproduction")
        for size in sizes:
            model.add(max_overproduction >= produced[size] - demand[size])
        marker_designs = sum(marker_used.values())
        marker_changeovers = model.new_int_var(0, max(0, len(markers) - 1), "marker_changeovers")
        model.add(marker_changeovers == marker_designs - 1)
        primary_coverage = sum(
            primary_choice[(marker_index, layers)]
            * useful_coverage(demand, markers[marker_index].composition, layers)
            for marker_index, layers in primary_choice
        )
        expressions = {
            "total_overproduction": total_overproduction,
            "max_overproduction": max_overproduction,
            "fabric": sum(fabric_terms),
            "waste": sum(waste_terms),
            "spreads": sum(spread_terms),
            "marker_designs": marker_designs,
            "marker_changeovers": marker_changeovers,
            "primary_coverage": primary_coverage,
        }
        if incumbent is not None:
            model.add(expressions["spreads"] <= incumbent.spread_count)
        if spread_upper_bound is not None:
            model.add(expressions["spreads"] <= spread_upper_bound)
        for name, value in fixed.items():
            model.add(expressions[name] == value)
        if greedy_hint:
            for key, variable in variables.items():
                model.add_hint(variable, greedy_hint.get(key, 0))
        return model, variables, produced, expressions

    def _solve_profile(self, profile, demand, maximum_overproduction, markers, cancellation_checkpoint=None,
                       initial_hint=None, incumbent=None, spread_upper_bound=None, profile_time_limit=None):
        started = perf_counter()
        fixed: dict[str, int] = {}
        stages = []
        final = None
        greedy_hint = initial_hint or self._operational_greedy_seed(demand, maximum_overproduction, markers)
        profile_time_limit = self.config.time_limit_seconds if profile_time_limit is None else profile_time_limit
        stage_limit = max(0.01, profile_time_limit / max(1, len(PROFILES[profile])))
        for stage_number, objective in enumerate(PROFILES[profile], start=1):
            if cancellation_checkpoint:
                cancellation_checkpoint()
            model, variables, produced, expressions = self._build_model(
                demand, maximum_overproduction, markers, fixed, greedy_hint, incumbent, spread_upper_bound,
            )
            if objective in MAXIMIZE_OBJECTIVES:
                model.maximize(expressions[objective])
            else:
                model.minimize(expressions[objective])
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = stage_limit
            solver.parameters.num_search_workers = 1 if self.config.deterministic else 0
            solver.parameters.random_seed = self.config.seed
            status = solver.solve(model)
            status_name = solver.status_name(status)
            stage = {
                "profile": profile, "stage": stage_number, "objective": objective, "status": status_name,
                "direction": "maximize" if objective in MAXIMIZE_OBJECTIVES else "minimize",
                "elapsed_ms": round(solver.wall_time * 1000, 3), "fixed_from_previous_stage": dict(fixed),
            }
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                stages.append(stage)
                if final is None:
                    return self._infeasible(profile, status_name, stages, started, len(variables), len(model.proto.constraints))
                break
            value = int(solver.value(expressions[objective]))
            stage["value"] = value
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                stage["bound"] = int(round(solver.best_objective_bound))
            proven = status == cp_model.OPTIMAL or stage.get("bound") == value
            stage["proven_optimal"] = proven
            stages.append(stage)
            final = (solver, variables, produced, expressions, status, len(model.proto.constraints))
            if not proven:
                break
            fixed[objective] = value
        assert final is not None
        solver, variables, produced, expressions, status, constraint_count = final
        raw_spread_rows = []
        produced_values = {size: int(solver.value(expression)) for size, expression in produced.items()}
        for (marker_index, layers), variable in sorted(variables.items()):
            repeats = int(solver.value(variable))
            if repeats == 0:
                continue
            marker = markers[marker_index]
            composition = dict(marker.composition)
            production = {size: quantity * layers * repeats for size, quantity in composition.items()}
            spread_payload = {"marker": marker.marker_hash, "layers": layers, "repeats": repeats, "production": production}
            raw_spread_rows.append(PlannedSpread(
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
        spread_rows = self._compact_and_sequence(raw_spread_rows, markers, demand)
        over = {size: produced_values[size] - demand[size] for size in demand}
        total_fabric = sum(item.fabric_consumption_units for item in spread_rows)
        total_waste = sum(markers_by_hash(markers)[item.marker_hash].waste_area_units2 * item.layers * item.repeats for item in spread_rows)
        used_area = sum(markers_by_hash(markers)[item.marker_hash].piece_area_units2 * item.layers * item.repeats for item in spread_rows)
        total_area = used_area + total_waste
        # Commercial identity deliberately excludes the exact layer allocation.
        marker_catalog = markers_by_hash(markers)
        used_markers = [marker_catalog[marker_hash] for marker_hash in sorted({row.marker_hash for row in spread_rows})]
        piece_counts = [len(marker.placements) for marker in used_markers]
        marker_design_count = len(used_markers)
        fingerprint_payload = {
            "total_fabric_units": total_fabric, "spread_count": sum(row.repeats for row in spread_rows),
            "marker_design_count": marker_design_count,
            "marker_hashes": [marker.marker_hash for marker in used_markers],
            "production_by_size": produced_values, "overproduction_by_size": over,
            "global_efficiency_percentage": round(used_area / total_area * 100, 6) if total_area else 0.0,
        }
        fingerprint = canonical_json_hash(fingerprint_payload)
        solution_hash = canonical_json_hash({"solution_fingerprint": fingerprint})
        return PlanningSolution(
            profiles=(profile,), planning_status=solver.status_name(status),
            planning_optimality="SOLVER_OPTIMAL" if all(stage.get("proven_optimal") for stage in stages) else "SOLVER_FEASIBLE",
            solution_origin=self._solution_origin(spread_rows, markers), spreads=tuple(spread_rows), requested_by_size=dict(demand),
            produced_by_size=produced_values, overproduction_by_size=over, total_overproduction=sum(over.values()),
            max_overproduction=max(over.values(), default=0), total_fabric_units=total_fabric,
            total_waste_units2=total_waste, spread_count=sum(row.repeats for row in spread_rows),
            global_efficiency_percentage=round(used_area / total_area * 100, 6) if total_area else 0.0,
            marker_design_count=marker_design_count, marker_change_count=max(0, marker_design_count - 1),
            max_pieces_per_marker=max(piece_counts, default=0),
            average_pieces_per_marker=round(sum(piece_counts) / marker_design_count, 3) if marker_design_count else 0.0,
            primary_spread_covered_garments=spread_rows[0].useful_garments if spread_rows else 0,
            primary_spread_coverage_percentage=spread_rows[0].order_coverage_percentage if spread_rows else 0.0,
            objective_stages=tuple(stages), variable_count=len(variables), constraint_count=constraint_count,
            solver_time_ms=round((perf_counter() - started) * 1000, 3), fingerprint=fingerprint,
            solution_hash=solution_hash,
        )

    @staticmethod
    def _solution_origin(spreads, markers):
        used = {item.marker_hash for item in spreads}
        origins = {item.origin for item in markers if item.marker_hash in used}
        if origins and all(origin == "SINGLE_SIZE_FALLBACK" for origin in origins):
            return "FALLBACK_SINGLE_SIZE"
        if any(origin.startswith("RESIDUAL_DRIVEN") for origin in origins):
            return "RESIDUAL_IMPROVED"
        return "MIXED_CANDIDATES"

    @staticmethod
    def _infeasible(profile, status, stages, started, variables, constraints):
        payload = {"profile": profile, "status": status, "stages": stages}
        return PlanningSolution(
            profiles=(profile,), planning_status=status, planning_optimality=status, solution_origin="NONE", spreads=(),
            requested_by_size={}, produced_by_size={}, overproduction_by_size={}, total_overproduction=0,
            max_overproduction=0, total_fabric_units=0, total_waste_units2=0, spread_count=0,
            global_efficiency_percentage=0, marker_design_count=0, marker_change_count=0,
            max_pieces_per_marker=0, average_pieces_per_marker=0,
            primary_spread_covered_garments=0, primary_spread_coverage_percentage=0.0,
            objective_stages=tuple(stages), variable_count=variables,
            constraint_count=constraints, solver_time_ms=round((perf_counter() - started) * 1000, 3),
            fingerprint=canonical_json_hash(payload), solution_hash=canonical_json_hash(payload),
        )

    def _operational_greedy_seed(self, demand, maximum_overproduction, markers):
        """Fast coverage-first incumbent used only as a CP-SAT hint."""
        remaining = dict(demand)
        produced = {size: 0 for size in demand}
        hint: dict[tuple[int, int], int] = {}
        for _ in range(sum(demand.values()) + 1):
            if not any(remaining.values()):
                break
            choices = []
            for marker_index, marker in enumerate(markers):
                composition = dict(marker.composition)
                directed = marker.candidate_layers or tuple(range(1, self.config.max_layers + 1))
                for layers in directed:
                    output = {size: composition.get(size, 0) * layers for size in demand}
                    if any(produced[size] + output[size] > demand[size] + maximum_overproduction[size] for size in demand):
                        continue
                    coverage = sum(min(remaining[size], output[size]) for size in demand)
                    if coverage:
                        choices.append((coverage, len(marker.composition), -marker.marker_length_units * layers,
                                        marker_index, layers, output))
            if not choices:
                break
            _, _, _, marker_index, layers, output = max(choices)
            hint[(marker_index, layers)] = hint.get((marker_index, layers), 0) + 1
            for size in demand:
                produced[size] += output[size]
                remaining[size] = max(0, demand[size] - produced[size])
        return hint

    @staticmethod
    def _hint_from_solution(markers, solution):
        if solution is None:
            return {}
        indices = {marker.marker_hash: index for index, marker in enumerate(markers)}
        hint = {}
        for spread in solution.spreads:
            marker_index = indices.get(spread.marker_hash)
            if marker_index is not None:
                hint[(marker_index, spread.layers)] = hint.get((marker_index, spread.layers), 0) + spread.repeats
        return hint

    def _single_size_fallback_hint(self, demand, markers):
        """Construct the always-auditable single-size feasibility fallback."""
        by_size = {}
        for marker_index, marker in enumerate(markers):
            if len(marker.composition) == 1 and marker.composition[0][1] == 1:
                by_size.setdefault(marker.composition[0][0], marker_index)
        if any(quantity > 0 and size not in by_size for size, quantity in demand.items()):
            return {}
        hint = {}
        for size, quantity in demand.items():
            marker_index = by_size.get(size)
            while marker_index is not None and quantity > 0:
                layers = min(self.config.max_layers, quantity)
                hint[(marker_index, layers)] = hint.get((marker_index, layers), 0) + 1
                quantity -= layers
        return hint

    def _compact_and_sequence(self, rows, markers, demand):
        marker_catalog = markers_by_hash(markers)
        layer_units: dict[str, int] = {}
        for row in rows:
            layer_units[row.marker_hash] = layer_units.get(row.marker_hash, 0) + row.layers * row.repeats
        compacted = []
        for marker_hash, total_layers in sorted(layer_units.items()):
            marker = marker_catalog[marker_hash]
            while total_layers:
                layers = min(self.config.max_layers, total_layers)
                production = {size: quantity * layers for size, quantity in marker.composition}
                payload = {"marker": marker_hash, "layers": layers, "repeats": 1, "production": production}
                compacted.append(PlannedSpread(
                    marker_hash=marker_hash, composition=marker.composition, layers=layers, repeats=1,
                    marker_length_units=marker.marker_length_units,
                    fabric_consumption_units=marker.marker_length_units * layers,
                    production_by_size=production,
                    marker_efficiency_percentage=marker.efficiency_percentage,
                    marker_search_status=marker.marker_search_status,
                    spread_hash=canonical_json_hash(payload),
                ))
                total_layers -= layers
        remaining = dict(demand)
        sequenced = []
        while compacted:
            best = max(compacted, key=lambda row: (
                useful_coverage(remaining, row.composition, row.layers),
                len(row.composition), -row.marker_length_units, row.marker_hash,
            ))
            compacted.remove(best)
            useful = useful_coverage(remaining, best.composition, best.layers)
            before_total = sum(remaining.values())
            for size, quantity in best.production_by_size.items():
                remaining[size] = max(0, remaining.get(size, 0) - quantity)
            sequenced.append(replace(
                best, useful_garments=useful,
                order_coverage_percentage=round(useful / before_total * 100, 6) if before_total else 0.0,
                remaining_demand_after=dict(remaining), is_primary=not sequenced,
            ))
        return sequenced


def markers_by_hash(markers):
    return {marker.marker_hash: marker for marker in markers}
