from __future__ import annotations

from math import ceil
from time import perf_counter

from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.domain.production_models import (
    CandidateComposition,
    CandidateGenerationConfig,
    CandidateGenerationResult,
    Composition,
)


SIZE_ORDER = {code: index for index, code in enumerate(("XS", "S", "M", "L", "XL", "XXL", "XXXL"))}


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
    ) -> CandidateGenerationResult:
        started = perf_counter()
        positive = tuple(sorted((size for size, quantity in demand.items() if quantity > 0), key=SIZE_ORDER.get))
        residual = residual or demand
        proposals: list[tuple[Composition, int, str]] = []
        for size in positive:
            proposals.append((((size, 1),), 1, "SINGLE_SIZE_FALLBACK"))
        for size in positive:
            allowed = demand[size] + maximum_overproduction[size]
            for multiplicity in range(2, min(self.config.max_garments_per_marker, allowed) + 1):
                proposals.append((((size, multiplicity),), 1, "SINGLE_SIZE_REPEAT"))

        ranked = sorted(positive, key=lambda size: (-residual.get(size, 0), SIZE_ORDER[size]))
        for left_index, left in enumerate(ranked):
            for right in ranked[left_index + 1 :]:
                proposals.append((tuple(sorted(((left, 1), (right, 1)), key=lambda item: SIZE_ORDER[item[0]])), 2, "RESIDUAL_PAIR"))
        if self.config.max_distinct_sizes_per_marker >= 3 and len(ranked) >= 3:
            triple = tuple(sorted(((size, 1) for size in ranked[:3]), key=lambda item: SIZE_ORDER[item[0]]))
            proposals.append((triple, 2, "RESIDUAL_MULTI_SIZE"))

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
            if area_bound > max_marker_length_units:
                pruned.append({"composition": dict(composition), "reason": "area_lower_bound"})
                continue
            payload = {"composition": composition, "round": round_number, "origin": origin}
            candidates.append(CandidateComposition(composition, round_number, origin, area_bound, canonical_json_hash(payload)))
            if len(candidates) >= self.config.max_candidate_compositions:
                break
        candidates.sort(key=lambda item: (item.round_number, item.origin != "SINGLE_SIZE_FALLBACK", item.composition))
        return CandidateGenerationResult(tuple(candidates), tuple(pruned), round((perf_counter() - started) * 1000, 3))
