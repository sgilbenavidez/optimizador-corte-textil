from __future__ import annotations

from dataclasses import dataclass

from costura_optima.domain.candidate_generator import candidate_category
from costura_optima.domain.production_models import CandidateComposition


@dataclass(frozen=True)
class CandidateFunnelResult:
    selected: tuple[CandidateComposition, ...]
    excluded: tuple[CandidateComposition, ...]
    counts: dict[str, int]


def select_geometry_top_k(
    candidates: tuple[CandidateComposition, ...], top_k: int,
) -> CandidateFunnelResult:
    """Cheap deterministic funnel; no geometry operation is allowed here."""
    requested_top_k = max(0, min(top_k, len(candidates)))
    fallbacks = [item for item in candidates if candidate_category(item.origin, item.composition) == "SINGLE_SIZE"]
    operational = [item for item in candidates if item not in fallbacks]
    operational.sort(key=lambda item: (
        -item.potential_useful_coverage,
        -item.potential_coverage_percentage,
        0 if len(item.composition) > 1 else 1,
        item.estimated_length_units,
        -len(item.composition),
        item.composition,
    ))
    # The improvement funnel is bounded by TOP-K, while exact single-size
    # fallbacks are mandatory feasibility infrastructure. A too-small user
    # TOP-K therefore grows only enough to retain every fallback plus one
    # high-coverage primary candidate.
    mandatory_slots = len(fallbacks) + (1 if operational else 0)
    effective_top_k = min(len(candidates), max(requested_top_k, mandatory_slots))
    fallback_slots = len(fallbacks)
    primary_slots = max(0, effective_top_k - fallback_slots)
    # Evaluate one high-coverage multi-size primary first, then establish the
    # guaranteed fallback catalog before spending time on further refinements.
    primary = operational[:1] if primary_slots else []
    remaining_primary = operational[len(primary):primary_slots]
    selected = primary + fallbacks[:fallback_slots] + remaining_primary
    if len(selected) < effective_top_k:
        selected.extend(item for item in operational[primary_slots:] if item not in selected)
    selected = selected[:effective_top_k]
    chosen = set(selected)
    return CandidateFunnelResult(
        selected=tuple(selected),
        excluded=tuple(item for item in candidates if item not in chosen),
        counts={
            "raw_compositions": len(candidates),
            "coverage_ranked": len(candidates),
            "estimated_length_ranked": len(candidates),
            "operational_ranked": len(candidates),
            "geometry_top_k": len(selected),
            "geometry_top_k_requested": requested_top_k,
            "mandatory_fallbacks": len(fallbacks),
        },
    )
