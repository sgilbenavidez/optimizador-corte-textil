from __future__ import annotations

from math import ceil, floor, gcd
from functools import reduce
from time import perf_counter

from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.domain.production_models import (
    CandidateComposition,
    CandidateGenerationConfig,
    CandidateGenerationResult,
    Composition,
)
from costura_optima.domain.divisibility import DivisibilityRatioGenerator


SIZE_ORDER = {code: index for index, code in enumerate(("XS", "S", "M", "L", "XL", "XXL", "XXXL"))}


def candidate_category(origin: str, composition: Composition) -> str:
    if origin == "ZERO_RESIDUE_RATIO":
        return "ZERO_RESIDUE"
    if "DEMAND_RATIO" in origin:
        return "DEMAND_RATIO"
    if origin.startswith("PROPORTIONAL") or origin.startswith("AREA_DENSITY"):
        return "MULTI_SIZE"
    if origin == "SINGLE_SIZE_FALLBACK":
        return "SINGLE_SIZE"
    if origin == "SINGLE_SIZE_REPEAT":
        return "SINGLE_SIZE_REPEAT"
    if origin in {"RESIDUAL_PAIR", "PAIR"}:
        return "PAIR"
    if origin in {"RESIDUAL_MULTI_SIZE", "TRIPLE"}:
        return "TRIPLE"
    if origin.startswith("RESIDUAL"):
        return "RESIDUAL_DRIVEN"
    return "PAIR" if len(composition) == 2 else "TRIPLE" if len(composition) >= 3 else "SINGLE_SIZE"


def select_balanced_budget(
    candidates: tuple[CandidateComposition, ...], limit: int,
) -> tuple[tuple[CandidateComposition, ...], tuple[CandidateComposition, ...]]:
    """Reserve useful catalog capacity by category, then fill by deterministic rank."""
    if limit >= len(candidates):
        return candidates, ()
    buckets: dict[str, list[CandidateComposition]] = {}
    for item in candidates:
        buckets.setdefault(candidate_category(item.origin, item.composition), []).append(item)
    selected: list[CandidateComposition] = []
    # Every demanded size needs a fallback; multi-size must never starve when present.
    selected.extend(buckets.get("SINGLE_SIZE", []))
    ratio_by_size: dict[int, CandidateComposition] = {}
    for item in buckets.get("DEMAND_RATIO", []):
        ratio_by_size.setdefault(sum(quantity for _, quantity in item.composition), item)
    for target in (3, 4, 5, 6, 8, 10, 12, 15):
        item = ratio_by_size.get(target)
        if item is not None and item not in selected and len(selected) < limit:
            selected.append(item)
    consolidated = sorted(
        buckets.get("MULTI_SIZE", []),
        key=lambda item: (-item.potential_useful_coverage, -sum(quantity for _, quantity in item.composition), item.composition),
    )
    selected.extend(item for item in consolidated[:min(4, max(0, limit - len(selected)))] if item not in selected)
    # These are operationally important families and each receives a hard slot
    # before generic pairs or single-size repeats may consume the budget.
    reserve_order = ("DEMAND_RATIO", "MULTI_SIZE", "RESIDUAL_DRIVEN", "PAIR", "TRIPLE")
    for category in reserve_order:
        if len(selected) < limit and buckets.get(category):
            candidate = next((item for item in buckets[category] if item not in selected), None)
            if candidate is not None:
                selected.append(candidate)
    remaining = [item for item in candidates if item not in selected]
    remaining.sort(key=lambda item: (
        item.round_number,
        0 if candidate_category(item.origin, item.composition) in {"DEMAND_RATIO", "MULTI_SIZE", "PAIR", "TRIPLE", "RESIDUAL_DRIVEN"} else 1,
        -item.potential_useful_coverage,
        -sum(quantity for _, quantity in item.composition),
        item.area_lower_bound_units,
        item.composition,
    ))
    selected.extend(item for item in remaining[:max(0, limit - len(selected))] if item not in selected)
    chosen = set(selected[:limit])
    return tuple(selected[:limit]), tuple(item for item in candidates if item not in chosen)


class CandidateCompositionGenerator:
    def __init__(self, config: CandidateGenerationConfig):
        self.config = config

    def generate(
        self,
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        size_area_units2: dict[str, int],
        size_piece_fits: dict[str, bool],
        usable_width_units: int,
        max_marker_length_units: int,
        residual: dict[str, int] | None = None,
        round_number: int = 1,
        max_layers: int = 30,
        length_margins_units: int = 0,
    ) -> CandidateGenerationResult:
        started = perf_counter()
        positive = tuple(sorted((size for size, quantity in demand.items() if quantity > 0), key=SIZE_ORDER.get))
        residual_driven = residual is not None
        residual = residual or demand
        proposals: list[tuple[Composition, int, str]] = []
        if not residual_driven:
            exact = DivisibilityRatioGenerator().generate(demand, max_layers, self.config.max_garments_per_marker)
            proposals.extend((item.composition, round_number, "ZERO_RESIDUE_RATIO") for item in exact)
        for size in positive:
            if not residual_driven:
                proposals.append((((size, 1),), round_number, "SINGLE_SIZE_FALLBACK"))
        for size in positive:
            allowed = demand[size] + maximum_overproduction[size]
            for multiplicity in range(2, min(self.config.max_garments_per_marker, allowed) + 1):
                if not residual_driven:
                    proposals.append((((size, multiplicity),), round_number, "SINGLE_SIZE_REPEAT"))

        ranked = sorted(positive, key=lambda size: (-residual.get(size, 0), SIZE_ORDER[size]))
        for left_index, left in enumerate(ranked):
            for right in ranked[left_index + 1 :]:
                origin = "RESIDUAL_DRIVEN_PAIR" if residual_driven else "PAIR"
                proposals.append((tuple(sorted(((left, 1), (right, 1)), key=lambda item: SIZE_ORDER[item[0]])), round_number, origin))
        if residual_driven and len(ranked) >= 2 and self.config.max_garments_per_marker >= 3:
            left, right = ranked[:2]
            for composition in (((left, 2), (right, 1)), ((left, 1), (right, 2))):
                proposals.append((tuple(sorted(composition, key=lambda item: SIZE_ORDER[item[0]])), round_number, "RESIDUAL_DRIVEN"))
        if self.config.max_distinct_sizes_per_marker >= 3 and len(ranked) >= 3:
            triple = tuple(sorted(((size, 1) for size in ranked[:3]), key=lambda item: SIZE_ORDER[item[0]]))
            proposals.append((triple, round_number, "RESIDUAL_DRIVEN_TRIPLE" if residual_driven else "DEMAND_RATIO_SEED"))

        # Bounded, ratio-directed exploration. One demand-ratio and one area-density
        # proposal per target replaces combinatorial enumeration of all partitions.
        target_sizes = tuple(target for target in (3, 4, 5, 6, 8, 10, 12, 15) if target <= self.config.max_garments_per_marker)
        source = residual if residual_driven else demand
        for target in target_sizes:
            proportional = self._proportional(source, demand, maximum_overproduction, target)
            if proportional:
                proposals.append((proportional, round_number, "PROPORTIONAL_DEMAND_RATIO_RESIDUAL" if residual_driven else "PROPORTIONAL_DEMAND_RATIO"))
            density_weights = {
                size: source.get(size, 0) / max(1, size_area_units2[size])
                for size in positive
            }
            area_directed = self._proportional(density_weights, demand, maximum_overproduction, target)
            if area_directed:
                proposals.append((area_directed, round_number, "AREA_DENSITY_RESIDUAL" if residual_driven else "AREA_DENSITY_DEMAND"))

        # The exact simplified ratio is a useful anchor when it is already small
        # enough for the marker. Approximations above remain the main search path.
        ratio = self._simplified_ratio(source)
        if ratio and sum(quantity for _, quantity in ratio) <= self.config.max_garments_per_marker:
            proposals.append((ratio, round_number, "DEMAND_RATIO_EXACT"))

        candidates: list[CandidateComposition] = []
        pruned: list[dict] = []
        seen: set[Composition] = set()
        for composition, round_number, origin in proposals:
            if composition in seen:
                continue
            seen.add(composition)
            if len(composition) > self.config.max_distinct_sizes_per_marker:
                pruned.append({"composition": dict(composition), "reason": "distinct_size_limit"})
                continue
            garments = sum(quantity for _, quantity in composition)
            if garments > self.config.max_garments_per_marker:
                pruned.append({"composition": dict(composition), "reason": "garment_limit"})
                continue
            if any(not size_piece_fits.get(size, False) for size, _ in composition):
                pruned.append({"composition": dict(composition), "reason": "individual_piece_width"})
                continue
            absurd = any(quantity > demand[size] + maximum_overproduction[size] for size, quantity in composition)
            if absurd:
                pruned.append({"composition": dict(composition), "reason": "demand_policy"})
                continue
            total_area = sum(size_area_units2[size] * quantity for size, quantity in composition)
            area_bound = ceil(total_area / usable_width_units)
            if area_bound + length_margins_units > max_marker_length_units:
                pruned.append({"composition": dict(composition), "reason": "area_lower_bound"})
                continue
            candidate_layers = self._candidate_layers(composition, residual, max_layers)
            potential = max((useful_coverage(residual, composition, layers) for layers in candidate_layers), default=0)
            remaining_total = sum(max(0, quantity) for quantity in residual.values())
            percentage = round(potential / remaining_total * 100, 6) if remaining_total else 0.0
            payload = {"composition": composition, "round": round_number, "origin": origin,
                       "candidate_layers": candidate_layers}
            estimated_length = ceil(area_bound / max(0.01, self.config.expected_marker_efficiency)) + length_margins_units
            candidates.append(CandidateComposition(
                composition, round_number, origin, area_bound, canonical_json_hash(payload),
                candidate_layers, potential, percentage, estimated_length,
            ))
        candidates.sort(key=lambda item: (
            item.round_number,
            0 if item.origin == "ZERO_RESIDUE_RATIO" else 1,
            1 if item.origin == "SINGLE_SIZE_REPEAT" else 0,
            -item.potential_useful_coverage,
            -len(item.composition),
            item.estimated_length_units,
            item.composition,
        ))
        if len(candidates) > self.config.max_candidate_compositions:
            candidates, outside = select_balanced_budget(tuple(candidates), self.config.max_candidate_compositions)
            pruned.extend({"composition": dict(item.composition), "reason": "candidate_composition_budget",
                           "category": candidate_category(item.origin, item.composition)} for item in outside)
            candidates = list(candidates)
        distribution = {key: 0 for key in ("ZERO_RESIDUE", "SINGLE_SIZE", "SINGLE_SIZE_REPEAT", "PAIR", "TRIPLE", "RESIDUAL_DRIVEN", "DEMAND_RATIO", "MULTI_SIZE")}
        for item in candidates:
            distribution[candidate_category(item.origin, item.composition)] += 1
        return CandidateGenerationResult(tuple(candidates), tuple(pruned), round((perf_counter() - started) * 1000, 3), distribution)

    @staticmethod
    def _candidate_layers(composition: Composition, residual: dict[str, int], max_layers: int) -> tuple[int, ...]:
        layers = {1, max_layers}
        for size, marker_quantity in composition:
            center = floor(max(0, residual.get(size, 0)) / marker_quantity)
            layers.update(center + offset for offset in (-1, 0, 1))
        return tuple(sorted(value for value in layers if 1 <= value <= max_layers))

    @staticmethod
    def _simplified_ratio(demand: dict[str, int]) -> Composition:
        positive = [(size, int(quantity)) for size, quantity in demand.items() if quantity > 0]
        if not positive:
            return ()
        divisor = reduce(gcd, (quantity for _, quantity in positive))
        return tuple(sorted(((size, quantity // divisor) for size, quantity in positive), key=lambda item: SIZE_ORDER[item[0]]))

    def _proportional(
        self,
        weights: dict[str, float | int],
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        target: int,
    ) -> Composition:
        eligible = [size for size, value in weights.items() if value > 0 and demand.get(size, 0) > 0]
        eligible.sort(key=lambda size: (-float(weights[size]), SIZE_ORDER[size]))
        selected = eligible[:min(self.config.max_distinct_sizes_per_marker, target)]
        if not selected:
            return ()
        allocation = {size: 1 for size in selected}
        remaining = target - len(selected)
        total_weight = sum(float(weights[size]) for size in selected)
        while remaining > 0:
            available = [
                size for size in selected
                if allocation[size] < demand[size] + maximum_overproduction[size]
            ]
            if not available:
                break
            size = max(
                available,
                key=lambda item: (
                    (float(weights[item]) / total_weight * target) - allocation[item],
                    float(weights[item]),
                    -SIZE_ORDER[item],
                ),
            )
            allocation[size] += 1
            remaining -= 1
        if remaining:
            return ()
        return tuple(sorted(allocation.items(), key=lambda item: SIZE_ORDER[item[0]]))


def useful_coverage(remaining_demand: dict[str, int], composition: Composition, layers: int) -> int:
    """Coverage excludes production that exceeds the non-negative residual."""
    return sum(min(max(0, remaining_demand.get(size, 0)), quantity * layers)
               for size, quantity in composition)
