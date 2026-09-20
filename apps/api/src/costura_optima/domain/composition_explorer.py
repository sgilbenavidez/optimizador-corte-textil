"""Phase 2F.7H-2: CompositionExplorer.

Tests the hypothesis that markers containing more/repeated complete garments
or mixed-size combinations can nest better than a small fixed composition
(the M2+L1 benchmark). This module is deliberately a BRIDGE, not new
infrastructure: composition generation reuses the existing, tested,
production `CandidateCompositionGenerator` (candidate_generator.py)
unmodified; geometric evaluation reuses `GlobalNestingSearch`
(global_nesting_search.py) and `IndependentMarkerValidator`
(marker_validator.py) unmodified. This file only adds (a) tiered
attainability screening on top of the generator's single-bound prefilter,
and (b) the glue that turns an arithmetic composition candidate into a
geometrically-evaluated `ValidatedMarkerCandidate` for `ProductionPlanner`.

Kept dependency-free of `application`/`infrastructure` (DB, sessions,
MarkerPreviewService) by design, matching this codebase's domain-layer
boundary: `build_request` and `cold_start` are injected callables whose real
(DB-bound) implementations live in the phase 2F.7H-2 harness scripts, not
here. This also keeps this module unit-testable without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from statistics import median
from time import perf_counter
from typing import Any, Callable

from costura_optima.domain.candidate_generator import CandidateCompositionGenerator
from costura_optima.domain.global_nesting_search import GlobalNestingSearch, make_state
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import MarkerRequest, Placement
from costura_optima.domain.production_models import (
    CandidateComposition,
    CandidateGenerationConfig,
    Composition,
    ValidatedMarkerCandidate,
)


EFFICIENCY_TIERS: tuple[float, ...] = (1.00, 0.95, 0.90, 0.85, 0.80)
STATUSES = (
    "PREFILTER_REJECTED", "SEARCH_FAILED", "VALIDATED",
    "MAX_LENGTH_EXCEEDED", "TIME_BUDGET_EXCEEDED", "INVALID_FINAL_MARKER",
)


@dataclass(frozen=True)
class AttainabilityReport:
    theoretical_length_100_units: int
    theoretical_length_95_units: int
    theoretical_length_90_units: int
    theoretical_length_85_units: int
    theoretical_length_80_units: int
    attainability_class: str  # PROMISING / CHALLENGING / VERY_TIGHT / AREA_INFEASIBLE


def compute_attainability(total_area_units2: int, usable_width_units: int, max_length_units: int) -> AttainabilityReport:
    """Cheap area-bound screening (phase spec Sections 10-11).

    These are lower-bound SCREENING metrics -- never a proof of geometric
    feasibility. `CandidateCompositionGenerator` already computes an
    equivalent single bound at its own `expected_marker_efficiency` (default
    0.72); this adds the explicit L95/L90/L85/L80 tiers on top without
    modifying that production file.

    Classification thresholds (explicit, documented, not derived from any
    hidden heuristic): AREA_INFEASIBLE if even 100% efficiency can't fit;
    VERY_TIGHT if it needs >90% efficiency; CHALLENGING if it needs 85-90%;
    PROMISING if the 85%-efficiency bound already fits with margin.
    """
    lengths = tuple(ceil(total_area_units2 / (usable_width_units * tier)) for tier in EFFICIENCY_TIERS)
    l100, l95, l90, l85, l80 = lengths
    if l100 > max_length_units:
        attainability_class = "AREA_INFEASIBLE"
    elif l90 > max_length_units:
        attainability_class = "VERY_TIGHT"
    elif l85 > max_length_units:
        attainability_class = "CHALLENGING"
    else:
        attainability_class = "PROMISING"
    return AttainabilityReport(l100, l95, l90, l85, l80, attainability_class)


def _production_compatibility_score(item: CandidateComposition) -> float:
    """Kept separate from GEOMETRY_SCORE (phase spec Section 15): rewards
    exact zero-residue divisibility against the benchmark order and simpler
    (fewer distinct sizes) compositions, which are operationally cheaper even
    when their geometric efficiency is not the highest.
    """
    zero_residue_bonus = 25.0 if item.origin == "ZERO_RESIDUE_RATIO" else 0.0
    simplicity_bonus = max(0.0, 4 - len(item.composition)) * 5.0
    return round(zero_residue_bonus + simplicity_bonus + item.potential_coverage_percentage, 6)


@dataclass(frozen=True)
class CompositionCandidateReport:
    composition: Composition
    origin: str
    round_number: int
    garment_count: int
    distinct_size_count: int
    area_lower_bound_units: int
    candidate_layers: tuple[int, ...]
    potential_useful_coverage: int
    potential_coverage_percentage: float
    attainability: AttainabilityReport
    production_compatibility_score: float
    candidate_hash: str


@dataclass(frozen=True)
class CompositionResult:
    composition_id: str
    size_counts: dict[str, int]
    garment_count: int
    piece_count: int
    piece_area_units2: int
    usable_width_units: int
    theoretical_length_100_units: int
    theoretical_length_90_units: int
    best_marker_length_units: int | None
    best_efficiency_pct: float | None
    best_seed: int | None
    validated: bool
    marker_hash: str | None
    runtime_seconds: float
    search_iterations: int
    attainability_class: str
    status: str
    seed_lengths_units: tuple[int, ...] = ()
    median_length_units: int | None = None
    worst_length_units: int | None = None
    operator_statistics: dict = field(default_factory=dict)
    placements: tuple[Placement, ...] = ()


def _composition_id(composition: Composition) -> str:
    return "+".join(f"{size}x{quantity}" for size, quantity in composition)


def protected_length(candidate_length_units: int | None, protected_floor_units: int | None) -> int | None:
    """Incumbent protection (phase spec Section 25/28): a worse (larger) new
    result must never be reported as though it beat a known-better protected
    floor. Generalizes the clamp `run_phase2f7h_global_search.py` already
    applies ad hoc to the M2+L1 legacy incumbent, for any composition that
    has a prior best result worth protecting.
    """
    if candidate_length_units is None:
        return protected_floor_units
    if protected_floor_units is None:
        return candidate_length_units
    return min(candidate_length_units, protected_floor_units)


class CompositionExplorer:
    def __init__(self, config: CandidateGenerationConfig):
        self.config = config
        self._generator = CandidateCompositionGenerator(config)

    # ---- generation + cheap screening --------------------------------------
    def generate_candidates(
        self,
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        size_area_units2: dict[str, int],
        size_piece_fits: dict[str, bool],
        usable_width_units: int,
        max_marker_length_units: int,
        *,
        max_layers: int = 30,
        length_margins_units: int = 0,
        residual: dict[str, int] | None = None,
        round_number: int = 1,
    ) -> tuple[CompositionCandidateReport, ...]:
        """`residual` drives the phase 2F.7H-3 residual-driven loop (Section
        6): passing a residual demand vector instead of None switches the
        underlying (unmodified) CandidateCompositionGenerator to its
        residual-targeted proposals (RESIDUAL_DRIVEN_PAIR/TRIPLE,
        *_RESIDUAL proportional/area-density candidates), and every
        resulting `potential_coverage_percentage` is computed against that
        residual, not the full demand -- exactly the residual-reduction
        metric Section 5 asks for, with no new generation logic needed.
        """
        result = self._generator.generate(
            demand, maximum_overproduction, size_area_units2, size_piece_fits,
            usable_width_units, max_marker_length_units,
            residual=residual, round_number=round_number,
            max_layers=max_layers, length_margins_units=length_margins_units,
        )
        reports = []
        for item in result.candidates:
            total_area = sum(size_area_units2[size] * quantity for size, quantity in item.composition)
            attainability = compute_attainability(total_area, usable_width_units, max_marker_length_units)
            reports.append(CompositionCandidateReport(
                composition=item.composition, origin=item.origin, round_number=item.round_number,
                garment_count=sum(quantity for _, quantity in item.composition),
                distinct_size_count=len(item.composition),
                area_lower_bound_units=item.area_lower_bound_units, candidate_layers=item.candidate_layers,
                potential_useful_coverage=item.potential_useful_coverage,
                potential_coverage_percentage=item.potential_coverage_percentage,
                attainability=attainability,
                production_compatibility_score=_production_compatibility_score(item),
                candidate_hash=item.candidate_hash,
            ))
        return tuple(reports)

    @staticmethod
    def select_for_geometric_evaluation(
        reports: tuple[CompositionCandidateReport, ...],
        top_n: int,
        *,
        allow_classes: tuple[str, ...] = ("PROMISING", "CHALLENGING"),
    ) -> tuple[CompositionCandidateReport, ...]:
        """Ranks per phase spec Section 15: validated status is decided later
        (geometric evaluation hasn't run yet), so this ranks by attainability
        class first, then the tightest-but-still-plausible bound, then
        production compatibility -- and excludes VERY_TIGHT/AREA_INFEASIBLE
        by default (Section 19E: do not run tight compositions blindly).
        """
        eligible = [report for report in reports if report.attainability.attainability_class in allow_classes]
        ranked = sorted(eligible, key=lambda report: (
            0 if report.attainability.attainability_class == "PROMISING" else 1,
            report.attainability.theoretical_length_90_units,
            -report.production_compatibility_score,
        ))
        return tuple(ranked[:top_n])

    @staticmethod
    def prefiltered_result(report: CompositionCandidateReport) -> CompositionResult:
        """Bookkeeping record for a candidate that never reached geometric
        evaluation (excluded by attainability class or by the top-N cut).
        """
        return CompositionResult(
            composition_id=_composition_id(report.composition),
            size_counts=dict(report.composition), garment_count=report.garment_count, piece_count=0,
            piece_area_units2=0, usable_width_units=0,
            theoretical_length_100_units=report.attainability.theoretical_length_100_units,
            theoretical_length_90_units=report.attainability.theoretical_length_90_units,
            best_marker_length_units=None, best_efficiency_pct=None, best_seed=None, validated=False,
            marker_hash=None, runtime_seconds=0.0, search_iterations=0,
            attainability_class=report.attainability.attainability_class, status="PREFILTER_REJECTED",
        )

    # ---- geometric evaluation ------------------------------------------------
    def evaluate_geometrically(
        self,
        report: CompositionCandidateReport,
        build_request: Callable[[Composition], MarkerRequest],
        cold_start: Callable[[MarkerRequest], tuple[Placement, ...]],
        *,
        seeds: tuple[int, ...],
        max_iterations: int,
        max_runtime_s: float,
        candidate_budget: int = 5000,
        top_k: int = 1,
        beam_width: int = 1,
        piece_time_budget_ms: int = 60_000,
        time_budget_s: float | None = None,
        skip_search: bool = False,
    ) -> CompositionResult:
        """`skip_search=True` is Stage B (phase 2F.7H-3 Section 11): cold-start
        + independent validation only, no ALNS. Cold-start is deterministic
        given a fixed request (the legacy engine takes no seed-dependent
        randomness here), so with skip_search only the first seed is used --
        repeating identical work across seeds would be pure waste.
        """
        started = perf_counter()
        composition_id = _composition_id(report.composition)
        request = build_request(report.composition)
        by_id = {item.instance_id: item for item in request.piece_instances}
        kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())
        piece_area_units2 = sum(kernel.area_units2(item.piece.cut_polygon) for item in request.piece_instances)
        piece_count = len(request.piece_instances)
        full_validator = IndependentMarkerValidator()

        seed_outcomes: list[dict[str, Any]] = []
        any_cold_start_over_length = False
        effective_seeds = (seeds[0],) if skip_search and seeds else seeds
        for seed in effective_seeds:
            if time_budget_s is not None and (perf_counter() - started) > time_budget_s:
                return CompositionResult(
                    composition_id=composition_id, size_counts=dict(report.composition),
                    garment_count=report.garment_count, piece_count=piece_count,
                    piece_area_units2=piece_area_units2, usable_width_units=request.usable_width,
                    theoretical_length_100_units=report.attainability.theoretical_length_100_units,
                    theoretical_length_90_units=report.attainability.theoretical_length_90_units,
                    best_marker_length_units=None, best_efficiency_pct=None, best_seed=None, validated=False,
                    marker_hash=None, runtime_seconds=round(perf_counter() - started, 3), search_iterations=0,
                    attainability_class=report.attainability.attainability_class, status="TIME_BUDGET_EXCEEDED",
                )
            try:
                seed_placements = cold_start(request)
            except Exception:
                continue
            cold_report = full_validator.validate(request, seed_placements, request.max_length)
            if cold_report.status != "VALIDATED":
                continue
            initial_state = make_state(seed_placements, 0, None, None)
            if initial_state.marker_length_units > request.max_length:
                any_cold_start_over_length = True
                continue
            if skip_search:
                seed_outcomes.append({
                    "seed": seed, "length_units": initial_state.marker_length_units, "placements": initial_state.placements,
                    "layout_hash": initial_state.layout_hash, "operator_stats": {},
                    "iterations": 0, "runtime_s": round(perf_counter() - started, 3),
                })
                continue
            search = GlobalNestingSearch(request, by_id, seed=seed, candidate_budget=candidate_budget,
                                          top_k=top_k, beam_width=beam_width, piece_time_budget_ms=piece_time_budget_ms)
            result = search.run(initial_state, max_iterations=max_iterations, max_runtime_s=max_runtime_s)
            best = result["best_state"]
            final_report = search.full_validator.validate(request, best.placements, request.max_length)
            if final_report.status != "VALIDATED":
                continue
            if best.marker_length_units > request.max_length:
                any_cold_start_over_length = True
                continue
            seed_outcomes.append({
                "seed": seed, "length_units": best.marker_length_units, "placements": best.placements,
                "layout_hash": best.layout_hash, "operator_stats": result["operator_stats"],
                "iterations": result["iterations"], "runtime_s": result["runtime_s"],
            })

        runtime = round(perf_counter() - started, 3)
        if not seed_outcomes:
            status = "MAX_LENGTH_EXCEEDED" if any_cold_start_over_length else "SEARCH_FAILED"
            return CompositionResult(
                composition_id=composition_id, size_counts=dict(report.composition),
                garment_count=report.garment_count, piece_count=piece_count,
                piece_area_units2=piece_area_units2, usable_width_units=request.usable_width,
                theoretical_length_100_units=report.attainability.theoretical_length_100_units,
                theoretical_length_90_units=report.attainability.theoretical_length_90_units,
                best_marker_length_units=None, best_efficiency_pct=None, best_seed=None, validated=False,
                marker_hash=None, runtime_seconds=runtime, search_iterations=0,
                attainability_class=report.attainability.attainability_class, status=status,
            )

        best_outcome = min(seed_outcomes, key=lambda outcome: outcome["length_units"])
        # Final defensive re-check: the per-seed loop above already validated
        # this exact state, but the spec explicitly wants every published
        # result to carry its own final-check status rather than trusting an
        # earlier pass silently.
        final_check = full_validator.validate(request, best_outcome["placements"], request.max_length)
        if final_check.status != "VALIDATED":
            return CompositionResult(
                composition_id=composition_id, size_counts=dict(report.composition),
                garment_count=report.garment_count, piece_count=piece_count,
                piece_area_units2=piece_area_units2, usable_width_units=request.usable_width,
                theoretical_length_100_units=report.attainability.theoretical_length_100_units,
                theoretical_length_90_units=report.attainability.theoretical_length_90_units,
                best_marker_length_units=None, best_efficiency_pct=None, best_seed=None, validated=False,
                marker_hash=None, runtime_seconds=runtime, search_iterations=0,
                attainability_class=report.attainability.attainability_class, status="INVALID_FINAL_MARKER",
            )

        lengths = sorted(outcome["length_units"] for outcome in seed_outcomes)
        marker_area_units2 = request.usable_width * best_outcome["length_units"]
        efficiency_pct = round(piece_area_units2 / marker_area_units2 * 100, 6) if marker_area_units2 else 0.0
        return CompositionResult(
            composition_id=composition_id, size_counts=dict(report.composition),
            garment_count=report.garment_count, piece_count=piece_count,
            piece_area_units2=piece_area_units2, usable_width_units=request.usable_width,
            theoretical_length_100_units=report.attainability.theoretical_length_100_units,
            theoretical_length_90_units=report.attainability.theoretical_length_90_units,
            best_marker_length_units=best_outcome["length_units"], best_efficiency_pct=efficiency_pct,
            best_seed=best_outcome["seed"], validated=True, marker_hash=best_outcome["layout_hash"],
            runtime_seconds=runtime, search_iterations=best_outcome["iterations"],
            attainability_class=report.attainability.attainability_class, status="VALIDATED",
            seed_lengths_units=tuple(lengths), median_length_units=int(median(lengths)), worst_length_units=lengths[-1],
            operator_statistics=best_outcome["operator_stats"], placements=best_outcome["placements"],
        )


def to_validated_marker_candidate(
    result: CompositionResult, request: MarkerRequest, *, geometry_engine_version: str,
) -> ValidatedMarkerCandidate:
    """Bridges a VALIDATED CompositionResult into the exact shape
    `ProductionPlanner.solve_profiles` already consumes -- no changes needed
    to that file (production_planner.py / production_models.py).
    """
    if not result.validated or result.best_marker_length_units is None:
        raise ValueError(f"Cannot build a ValidatedMarkerCandidate from a non-VALIDATED result: {result.status}")
    marker_area_units2 = result.usable_width_units * result.best_marker_length_units
    waste_area_units2 = marker_area_units2 - result.piece_area_units2
    placements_payload = tuple({
        "piece_instance_id": p.piece_instance_id, "translation": p.translation, "rotation": p.rotation,
        "geometry_hash": p.geometry_hash,
    } for p in result.placements)
    composition = tuple(sorted(result.size_counts.items()))
    return ValidatedMarkerCandidate(
        marker_hash=result.marker_hash, content_key=f"composition-{result.composition_id}-{result.marker_hash}",
        composition=composition, marker_length_units=result.best_marker_length_units,
        usable_width_units=result.usable_width_units, piece_area_units2=result.piece_area_units2,
        marker_area_units2=marker_area_units2, waste_area_units2=waste_area_units2,
        efficiency_percentage=result.best_efficiency_pct or 0.0, placements=placements_payload,
        validation_certificate={"status": "VALIDATED", "source": "GlobalNestingSearch+IndependentMarkerValidator"},
        geometry_engine_version=geometry_engine_version, marker_search_status="FEASIBLE_NOT_PROVEN_BEST",
        input_hash=result.marker_hash, lower_bound_length_units=result.theoretical_length_100_units,
        origin="COMPOSITION_EXPLORER_2F7H2",
    )
