"""Phase 2F.7H-3: coverage-aware composition scoring, conservative Pareto
catalog filtering, and cheap infeasibility diagnostics.

Kept dependency-free of `application`/`infrastructure`, matching
`composition_explorer.py`'s domain-layer boundary. Reuses the existing,
already-tested primitives rather than rebuilding them:
`CandidateComposition.potential_coverage_percentage` (candidate_generator.py)
is already residual-aware whenever the generator was called with a
`residual=` demand vector; `pareto_filter` mirrors
`planning_coordinator.py::remove_dominated`'s deliberately conservative
same-composition-only dominance rule (kept local instead of imported across
the application/domain layer boundary).
"""
from __future__ import annotations

from dataclasses import dataclass

from costura_optima.domain.production_models import CandidateComposition, Composition, ValidatedMarkerCandidate


def coverage_priority_score(
    candidate: CandidateComposition,
    sizes_covered_by_catalog: frozenset[str],
    *,
    seen_size_sets: frozenset[frozenset[str]] = frozenset(),
) -> float:
    """Ranks candidates for the residual-driven search (phase spec Section 5).

    `candidate.potential_coverage_percentage` already reflects residual
    reduction (it was computed by CandidateCompositionGenerator against
    whatever residual/demand vector was passed to `.generate(...)`) -- this
    adds exactly two things on top, as the spec asks for: a bonus for each
    composition size the catalog does not yet produce at all (missing-size
    coverage), and a penalty when a candidate's size-set exactly duplicates
    one already evaluated (redundancy) -- geometrically excellent but
    coverage-redundant compositions should rank below coverage-opening ones.
    """
    composition_sizes = frozenset(size for size, _quantity in candidate.composition)
    missing_size_bonus = 20.0 * len(composition_sizes - sizes_covered_by_catalog)
    redundancy_penalty = 15.0 if composition_sizes in seen_size_sets else 0.0
    return round(candidate.potential_coverage_percentage + missing_size_bonus - redundancy_penalty, 6)


def pareto_filter(markers: tuple[ValidatedMarkerCandidate, ...]) -> tuple[ValidatedMarkerCandidate, ...]:
    """Conservative dominance (phase spec Section 8): only ever drops a
    marker in favor of ANOTHER MARKER WITH THE EXACT SAME COMPOSITION that is
    at least as good on length and waste, strictly better on one. Markers
    with different composition vectors are never compared against each
    other here -- their production vectors differ meaningfully, and the
    spec explicitly asks to be conservative about that. Mirrors
    `planning_coordinator.py::remove_dominated`'s already-tested rule
    (kept local rather than imported across the application/domain
    boundary -- this module has no application-layer dependency).
    """
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


@dataclass(frozen=True)
class InfeasibilityDiagnosis:
    sizes_with_no_catalog_coverage: tuple[str, ...]
    sizes_covered: tuple[str, ...]
    residual_by_size: dict[str, int]
    max_producible_by_size: dict[str, int]


def diagnose_infeasibility(
    catalog: tuple[ValidatedMarkerCandidate, ...], demand: dict[str, int], max_layers: int,
) -> InfeasibilityDiagnosis:
    """Cheap, no-CP-SAT-internals diagnostics (phase spec Section 7): which
    sizes no catalog marker produces at all, and a crude per-size upper bound
    on producible quantity (single best marker at max_layers, ignoring
    cross-marker combination -- CP-SAT itself already explores combinations;
    this is only meant to explain an INFEASIBLE result quickly, not to
    replace the solver).
    """
    covered: set[str] = set()
    max_producible: dict[str, int] = {size: 0 for size in demand}
    for marker in catalog:
        composition = dict(marker.composition)
        covered.update(composition)
        for size, quantity in composition.items():
            max_producible[size] = max(max_producible[size], quantity * max_layers)
    uncovered = tuple(sorted(size for size in demand if demand[size] > 0 and size not in covered))
    residual = {size: max(0, demand[size] - max_producible.get(size, 0)) for size in demand}
    return InfeasibilityDiagnosis(
        sizes_with_no_catalog_coverage=uncovered,
        sizes_covered=tuple(sorted(covered)),
        residual_by_size=residual,
        max_producible_by_size=max_producible,
    )
