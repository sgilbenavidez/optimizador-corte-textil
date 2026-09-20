from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

from costura_optima.domain.candidate_generator import useful_coverage
from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.domain.production_models import PlannedSpread, PlanningSolution, ValidatedMarkerCandidate


@dataclass(frozen=True)
class _State:
    produced: tuple[tuple[str, int], ...]
    choices: tuple[tuple[int, int], ...]
    fabric: int
    marker_hashes: frozenset[str]


class OperationalHeuristicSolver:
    """Local greedy/beam production solver with no OR-Tools dependency."""

    def __init__(self, max_layers: int = 30, beam_width: int = 10):
        self.max_layers = max_layers
        self.beam_width = beam_width

    def solve(self, demand, maximum_overproduction, markers) -> PlanningSolution:
        started = perf_counter()
        markers = tuple(markers)
        fallback = self._fallback_choices(demand, markers)
        if fallback is None:
            return self._empty(demand, started, "FALLBACK_MARKERS_MISSING")
        incumbents = [fallback]
        greedy = self._greedy_choices(demand, maximum_overproduction, markers)
        if greedy is not None:
            incumbents.append(greedy)
        beam = self._beam_choices(demand, maximum_overproduction, markers)
        if beam is not None:
            incumbents.append(beam)
        choices = min(incumbents, key=lambda item: self._choice_key(item, demand, markers))
        return self._build_solution(demand, markers, choices, started)

    def _fallback_choices(self, demand, markers):
        by_size = {}
        for index, marker in enumerate(markers):
            if len(marker.composition) == 1 and marker.composition[0][1] == 1:
                by_size.setdefault(marker.composition[0][0], index)
        if any(quantity and size not in by_size for size, quantity in demand.items()):
            return None
        choices = []
        for size, requested in sorted(demand.items()):
            while requested > 0:
                layers = min(self.max_layers, requested)
                choices.append((by_size[size], layers))
                requested -= layers
        return tuple(choices)

    def _greedy_choices(self, demand, maximum_overproduction, markers):
        produced = {size: 0 for size in demand}
        choices = []
        used = set()
        for _ in range(sum(demand.values()) + 1):
            remaining = {size: max(0, demand[size] - produced[size]) for size in demand}
            if not any(remaining.values()):
                return tuple(choices)
            options = self._options(remaining, produced, demand, maximum_overproduction, markers, used)
            if not options:
                return None
            _, index, layers = min(options)
            choices.append((index, layers)); used.add(markers[index].marker_hash)
            for size, quantity in markers[index].composition:
                produced[size] += quantity * layers
        return None

    def _beam_choices(self, demand, maximum_overproduction, markers):
        sizes = tuple(sorted(demand))
        initial = _State(tuple((size, 0) for size in sizes), (), 0, frozenset())
        beam = [initial]
        complete = []
        max_depth = sum((quantity + self.max_layers - 1) // self.max_layers for quantity in demand.values()) + 4
        for _ in range(max_depth):
            expanded = []
            for state in beam:
                produced = dict(state.produced)
                remaining = {size: max(0, demand[size] - produced[size]) for size in sizes}
                if not any(remaining.values()):
                    complete.append(state); continue
                for _, index, layers in self._options(remaining, produced, demand, maximum_overproduction, markers, state.marker_hashes)[:self.beam_width]:
                    updated = dict(produced)
                    for size, quantity in markers[index].composition:
                        updated[size] += quantity * layers
                    expanded.append(_State(
                        tuple(sorted(updated.items())), state.choices + ((index, layers),),
                        state.fabric + markers[index].marker_length_units * layers,
                        state.marker_hashes | {markers[index].marker_hash},
                    ))
            if complete or not expanded:
                break
            unique = {}
            for state in expanded:
                key = state.produced
                prior = unique.get(key)
                if prior is None or self._state_key(state, demand) < self._state_key(prior, demand):
                    unique[key] = state
            beam = sorted(unique.values(), key=lambda state: self._state_key(state, demand))[:self.beam_width]
        return min((state.choices for state in complete), key=lambda item: self._choice_key(item, demand, markers), default=None)

    def _options(self, remaining, produced, demand, maximum_overproduction, markers, used):
        rows = []
        for index, marker in enumerate(markers):
            composition = dict(marker.composition)
            layer_values = {1, self.max_layers, *(marker.candidate_layers or ())}
            for size, quantity in composition.items():
                if quantity:
                    center = (remaining.get(size, 0) + quantity - 1) // quantity
                    layer_values.update((center - 1, center, center + 1))
            for layers in sorted(value for value in layer_values if 1 <= value <= self.max_layers):
                output = {size: composition.get(size, 0) * layers for size in demand}
                if any(produced[size] + output[size] > demand[size] + maximum_overproduction[size] for size in demand):
                    continue
                coverage = sum(min(remaining[size], output[size]) for size in demand)
                if not coverage:
                    continue
                over = sum(max(0, produced[size] + output[size] - demand[size]) for size in demand)
                rows.append(((-coverage, over, marker.marker_length_units * layers,
                              0 if marker.marker_hash in used else 1, -layers, marker.marker_hash), index, layers))
        return sorted(rows)

    @staticmethod
    def _state_key(state, demand):
        produced = dict(state.produced)
        remaining = sum(max(0, demand[size] - produced[size]) for size in demand)
        return (remaining, len(state.choices), len(state.marker_hashes), state.fabric)

    @staticmethod
    def _choice_key(choices, demand, markers):
        produced = {size: 0 for size in demand}
        fabric = 0; designs = set()
        for index, layers in choices:
            marker = markers[index]; designs.add(marker.marker_hash)
            fabric += marker.marker_length_units * layers
            for size, quantity in marker.composition:
                produced[size] += quantity * layers
        shortage = sum(max(0, demand[size] - produced[size]) for size in demand)
        first_coverage = useful_coverage(demand, markers[choices[0][0]].composition, choices[0][1]) if choices else 0
        return (shortage, len(choices), -first_coverage, len(designs), fabric)

    def _build_solution(self, demand, markers, choices, started):
        by_hash = {marker.marker_hash: marker for marker in markers}
        layer_units = {}
        for index, layers in choices:
            marker_hash = markers[index].marker_hash
            layer_units[marker_hash] = layer_units.get(marker_hash, 0) + layers
        rows = []
        for marker_hash, total_layers in layer_units.items():
            marker = by_hash[marker_hash]
            while total_layers:
                layers = min(self.max_layers, total_layers); total_layers -= layers
                production = {size: quantity * layers for size, quantity in marker.composition}
                payload = {"marker": marker_hash, "layers": layers, "repeats": 1, "production": production}
                rows.append(PlannedSpread(
                    marker_hash, marker.composition, layers, 1, marker.marker_length_units,
                    marker.marker_length_units * layers, production, marker.efficiency_percentage,
                    marker.marker_search_status, canonical_json_hash(payload),
                ))
        remaining = dict(demand); sequenced = []
        while rows:
            best = max(rows, key=lambda row: (useful_coverage(remaining, row.composition, row.layers), len(row.composition), -row.marker_length_units))
            rows.remove(best); before = sum(remaining.values())
            useful = useful_coverage(remaining, best.composition, best.layers)
            for size, quantity in best.production_by_size.items():
                remaining[size] = max(0, remaining.get(size, 0) - quantity)
            sequenced.append(replace(best, useful_garments=useful,
                order_coverage_percentage=round(useful / before * 100, 6) if before else 0.0,
                remaining_demand_after=dict(remaining), is_primary=not sequenced))
        produced = {size: 0 for size in demand}
        for row in sequenced:
            for size, quantity in row.production_by_size.items(): produced[size] += quantity
        over = {size: produced[size] - demand[size] for size in demand}
        used = [by_hash[value] for value in sorted({row.marker_hash for row in sequenced})]
        fabric = sum(row.fabric_consumption_units for row in sequenced)
        waste = sum(by_hash[row.marker_hash].waste_area_units2 * row.layers for row in sequenced)
        piece_area = sum(by_hash[row.marker_hash].piece_area_units2 * row.layers for row in sequenced)
        fingerprint_payload = {"fabric": fabric, "spreads": len(sequenced), "markers": [m.marker_hash for m in used], "produced": produced}
        fingerprint = canonical_json_hash(fingerprint_payload)
        elapsed = round((perf_counter() - started) * 1000, 3)
        return PlanningSolution(
            profiles=("MAX_ORDER_PER_CUT",), planning_status="FEASIBLE", planning_optimality="HEURISTIC_FEASIBLE",
            solution_origin="OPERATIONAL_HEURISTIC", spreads=tuple(sequenced), requested_by_size=dict(demand),
            produced_by_size=produced, overproduction_by_size=over, total_overproduction=sum(over.values()),
            max_overproduction=max(over.values(), default=0), total_fabric_units=fabric, total_waste_units2=waste,
            spread_count=len(sequenced), global_efficiency_percentage=round(piece_area / (piece_area + waste) * 100, 6) if piece_area + waste else 0,
            marker_design_count=len(used), marker_change_count=max(0, len(used) - 1),
            max_pieces_per_marker=max((len(marker.placements) for marker in used), default=0),
            average_pieces_per_marker=round(sum(len(marker.placements) for marker in used) / len(used), 3) if used else 0,
            primary_spread_covered_garments=sequenced[0].useful_garments if sequenced else 0,
            primary_spread_coverage_percentage=sequenced[0].order_coverage_percentage if sequenced else 0,
            objective_stages=({
                "profile": "MAX_ORDER_PER_CUT", "stage": 0, "objective": "operational_beam",
                "status": "FEASIBLE", "value": len(sequenced), "elapsed_ms": elapsed,
                "fixed_from_previous_stage": {}, "proven_optimal": False,
            },),
            variable_count=0, constraint_count=0, solver_time_ms=elapsed,
            fingerprint=fingerprint, solution_hash=canonical_json_hash({"heuristic": fingerprint}),
        )

    @staticmethod
    def _empty(demand, started, reason):
        payload = {"reason": reason, "demand": demand}
        return PlanningSolution(
            profiles=("MAX_ORDER_PER_CUT",), planning_status="INFEASIBLE", planning_optimality="HEURISTIC_INFEASIBLE",
            solution_origin="OPERATIONAL_HEURISTIC", spreads=(), requested_by_size=dict(demand), produced_by_size={},
            overproduction_by_size={}, total_overproduction=0, max_overproduction=0, total_fabric_units=0,
            total_waste_units2=0, spread_count=0, global_efficiency_percentage=0, marker_design_count=0,
            marker_change_count=0, max_pieces_per_marker=0, average_pieces_per_marker=0,
            primary_spread_covered_garments=0, primary_spread_coverage_percentage=0,
            objective_stages=({"profile": "MAX_ORDER_PER_CUT", "stage": 0, "objective": "fallback", "status": reason},),
            variable_count=0, constraint_count=0, solver_time_ms=round((perf_counter() - started) * 1000, 3),
            fingerprint=canonical_json_hash(payload), solution_hash=canonical_json_hash(payload),
        )


OperationalCoveragePlanner = OperationalHeuristicSolver
