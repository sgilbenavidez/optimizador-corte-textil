"""Regression tests for FASE 2F.7H-2 CompositionExplorer.

Deliberately DB-free: `generate_candidates`/`compute_attainability`/
`select_for_geometric_evaluation`/`protected_length` are pure, and
`evaluate_geometrically` takes injected `build_request`/`cold_start`
callables, so these tests reuse the lightweight rectangular fixture from
test_global_nesting_search.py instead of a real pattern-set database.

Repeated-complete-garment piece-multiset correctness (spec Section 29.3) is
already covered end-to-end against real pattern geometry by
test_nesting_preview.py::test_real_composition_cases (M2 -> 10 pieces, mixed
S+M -> 10 pieces, etc., all VALIDATED_FEASIBLE with no duplicate instance
ids) -- not duplicated here.
"""
from __future__ import annotations

from math import ceil

from costura_optima.domain.composition_explorer import (
    AttainabilityReport,
    CompositionCandidateReport,
    CompositionExplorer,
    compute_attainability,
    protected_length,
    to_validated_marker_candidate,
)
from costura_optima.domain.production_models import CandidateGenerationConfig

from test_global_nesting_search import build_fixture


DEMAND = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
ZERO_OVERPRODUCTION = {size: 0 for size in DEMAND}
SIZE_AREA_UNITS2 = {"XS": 26_000_000, "S": 27_500_000, "M": 28_490_234, "L": 30_000_000,
                    "XL": 32_000_000, "XXL": 34_000_000, "XXXL": 36_000_000}
SIZE_PIECE_FITS = {size: True for size in DEMAND}
USABLE_WIDTH_UNITS = 176_000
MAX_LENGTH_UNITS = 700_000


def make_explorer(**overrides) -> CompositionExplorer:
    return CompositionExplorer(CandidateGenerationConfig(**overrides))


def _report(composition, attainability_class, *, l90=100, score=0.0) -> CompositionCandidateReport:
    attainability = AttainabilityReport(100, 100, l90, 100, 100, attainability_class)
    return CompositionCandidateReport(
        composition=composition, origin="TEST", round_number=1, garment_count=sum(quantity for _, quantity in composition),
        distinct_size_count=len(composition), area_lower_bound_units=100, candidate_layers=(1,),
        potential_useful_coverage=0, potential_coverage_percentage=0.0, attainability=attainability,
        production_compatibility_score=score, candidate_hash=f"hash-{composition}",
    )


# ---- generation ---------------------------------------------------------------

def test_generate_candidates_is_deterministic_and_duplicate_free():
    explorer = make_explorer()
    first = explorer.generate_candidates(DEMAND, ZERO_OVERPRODUCTION, SIZE_AREA_UNITS2, SIZE_PIECE_FITS,
                                          USABLE_WIDTH_UNITS, MAX_LENGTH_UNITS)
    second = explorer.generate_candidates(DEMAND, ZERO_OVERPRODUCTION, SIZE_AREA_UNITS2, SIZE_PIECE_FITS,
                                           USABLE_WIDTH_UNITS, MAX_LENGTH_UNITS)
    assert first
    assert tuple(r.candidate_hash for r in first) == tuple(r.candidate_hash for r in second)
    compositions = [r.composition for r in first]
    assert len(compositions) == len(set(compositions))


def test_generate_candidates_respects_configured_bounds():
    explorer = make_explorer(max_garments_per_marker=6, max_distinct_sizes_per_marker=2, max_candidate_compositions=60)
    reports = explorer.generate_candidates(DEMAND, ZERO_OVERPRODUCTION, SIZE_AREA_UNITS2, SIZE_PIECE_FITS,
                                            USABLE_WIDTH_UNITS, MAX_LENGTH_UNITS)
    assert reports
    for report in reports:
        assert report.garment_count <= 6
        assert report.distinct_size_count <= 2


def test_generate_candidates_excludes_zero_count_sizes():
    explorer = make_explorer()
    reports = explorer.generate_candidates(DEMAND, ZERO_OVERPRODUCTION, SIZE_AREA_UNITS2, SIZE_PIECE_FITS,
                                            USABLE_WIDTH_UNITS, MAX_LENGTH_UNITS)
    assert reports
    for report in reports:
        assert all(size != "XXXL" for size, _quantity in report.composition)


# ---- attainability tiers --------------------------------------------------------

def test_attainability_tiers_are_monotonic():
    report = compute_attainability(total_area_units2=50_000_000_000, usable_width_units=176_000, max_length_units=700_000)
    assert report.theoretical_length_100_units <= report.theoretical_length_95_units
    assert report.theoretical_length_95_units <= report.theoretical_length_90_units
    assert report.theoretical_length_90_units <= report.theoretical_length_85_units
    assert report.theoretical_length_85_units <= report.theoretical_length_80_units


def test_attainability_classification_boundaries():
    width = 100_000
    area = 500_000 * width  # L100 == 500_000 exactly
    assert compute_attainability(area, width, max_length_units=499_999).attainability_class == "AREA_INFEASIBLE"
    l90 = ceil(area / (width * 0.90))
    l85 = ceil(area / (width * 0.85))
    assert compute_attainability(area, width, max_length_units=l90 - 1).attainability_class == "VERY_TIGHT"
    assert compute_attainability(area, width, max_length_units=l85 - 1).attainability_class == "CHALLENGING"
    assert compute_attainability(area, width, max_length_units=l85).attainability_class == "PROMISING"


# ---- ranking / selection -----------------------------------------------------

def test_select_for_geometric_evaluation_excludes_very_tight_and_infeasible_by_default():
    reports = (
        _report((("M", 1),), "PROMISING", l90=50),
        _report((("M", 2),), "CHALLENGING", l90=80),
        _report((("M", 3),), "VERY_TIGHT", l90=95),
        _report((("M", 4),), "AREA_INFEASIBLE", l90=120),
    )
    selected = CompositionExplorer.select_for_geometric_evaluation(reports, top_n=10)
    assert {r.composition for r in selected} == {(("M", 1),), (("M", 2),)}


def test_prefiltered_result_marks_status_without_running_geometry():
    report = _report((("M", 4),), "VERY_TIGHT", l90=750_000)
    result = CompositionExplorer.prefiltered_result(report)
    assert result.status == "PREFILTER_REJECTED"
    assert result.validated is False
    assert result.theoretical_length_90_units == 750_000


# ---- incumbent protection -------------------------------------------------------

def test_protected_length_never_lets_worse_result_beat_a_better_floor():
    assert protected_length(230_000, 227_483) == 227_483
    assert protected_length(220_000, 227_483) == 220_000
    assert protected_length(None, 227_483) == 227_483
    assert protected_length(220_000, None) == 220_000
    assert protected_length(None, None) is None


# ---- geometric evaluation (injected build_request/cold_start) -----------------

def test_evaluate_geometrically_produces_a_validated_result_end_to_end():
    marker_request, _by_id, initial_state = build_fixture()
    explorer = make_explorer()
    report = _report((("M", 1),), "PROMISING")

    result = explorer.evaluate_geometrically(
        report, build_request=lambda _composition: marker_request,
        cold_start=lambda _request: initial_state.placements,
        seeds=(1, 2), max_iterations=3, max_runtime_s=60.0,
        candidate_budget=500, top_k=2, beam_width=2, piece_time_budget_ms=20_000,
    )

    assert result.status == "VALIDATED"
    assert result.validated is True
    assert result.best_marker_length_units is not None
    assert result.best_marker_length_units <= initial_state.marker_length_units
    assert result.piece_count == 5
    assert result.marker_hash
    assert len(result.seed_lengths_units) == 2

    candidate = to_validated_marker_candidate(result, marker_request, geometry_engine_version="test-2f7h2")
    assert candidate.marker_length_units == result.best_marker_length_units
    assert candidate.efficiency_percentage == result.best_efficiency_pct
    assert candidate.marker_hash == result.marker_hash


def test_evaluate_geometrically_skip_search_uses_cold_start_only():
    marker_request, _by_id, initial_state = build_fixture()
    explorer = make_explorer()
    report = _report((("M", 1),), "PROMISING")

    result = explorer.evaluate_geometrically(
        report, build_request=lambda _composition: marker_request,
        cold_start=lambda _request: initial_state.placements,
        seeds=(1, 2, 3), max_iterations=999, max_runtime_s=60.0, skip_search=True,
    )

    assert result.status == "VALIDATED"
    assert result.search_iterations == 0
    assert result.best_marker_length_units == initial_state.marker_length_units
    assert result.best_seed == 1  # only the first seed is used -- cold start is deterministic
    assert result.seed_lengths_units == (initial_state.marker_length_units,)


def test_evaluate_geometrically_reports_search_failed_when_cold_start_always_fails():
    marker_request, _by_id, _initial_state = build_fixture()
    explorer = make_explorer()
    report = _report((("M", 1),), "PROMISING")

    def _always_fails(_request):
        raise RuntimeError("no feasible marker")

    result = explorer.evaluate_geometrically(
        report, build_request=lambda _composition: marker_request, cold_start=_always_fails,
        seeds=(1,), max_iterations=2, max_runtime_s=10.0,
    )
    assert result.status == "SEARCH_FAILED"
    assert result.validated is False
    assert result.best_marker_length_units is None
