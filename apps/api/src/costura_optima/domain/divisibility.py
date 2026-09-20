"""Bounded exact-ratio candidate generation for production demand."""
from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from itertools import combinations
from math import gcd, isqrt


@dataclass(frozen=True)
class DivisibilityRatio:
    composition: tuple[tuple[str, int], ...]
    layers: int
    gcd_value: int
    prime_factors: tuple[int, ...]
    candidate_divisors: tuple[int, ...]
    residual: dict[str, int]


def _prime_factors(value: int) -> tuple[int, ...]:
    factors: list[int] = []
    divisor = 2
    while divisor <= isqrt(value):
        while value % divisor == 0:
            factors.append(divisor); value //= divisor
        divisor += 1
    return tuple(factors + ([value] if value > 1 else []))


class DivisibilityRatioGenerator:
    """Enumerates exact, bounded layer ratios for relevant size subsets."""

    def generate(self, demand_by_size: dict[str, int], max_layers: int, max_garments: int = 15) -> tuple[DivisibilityRatio, ...]:
        sizes = tuple(sorted(size for size, value in demand_by_size.items() if value > 0))
        rows: list[DivisibilityRatio] = []
        # Pairs/triples capture useful partitions without exponential all-subset search.
        for width in range(2, min(3, len(sizes)) + 1):
            for subset in combinations(sizes, width):
                values = [demand_by_size[size] for size in subset]
                common = reduce(gcd, values)
                divisors = tuple(divisor for divisor in range(1, min(common, max_layers) + 1) if common % divisor == 0)
                for layers in sorted(divisors, reverse=True):
                    composition = tuple((size, demand_by_size[size] // layers) for size in subset)
                    if sum(quantity for _, quantity in composition) > max_garments:
                        continue
                    rows.append(DivisibilityRatio(composition, layers, common, _prime_factors(common), divisors, {size: 0 for size in subset}))
        return tuple(sorted(rows, key=lambda item: (-item.layers, -len(item.composition), item.composition)))
