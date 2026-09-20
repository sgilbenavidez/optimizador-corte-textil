"""Regression tests for FASE 2F.7H-3 coverage-aware scoring, conservative
Pareto filtering, and infeasibility diagnostics.
"""
from __future__ import annotations

from costura_optima.domain.coverage_catalog import (
    coverage_priority_score,
    diagnose_infeasibility,
    pareto_filter,
)
from costura_optima.domain.production_models import CandidateComposition, ValidatedMarkerCandidate


def _candidate(composition, *, coverage_pct=10.0, origin="TEST"):
    return CandidateComposition(
        composition=composition, round_number=1, origin=origin, area_lower_bound_units=100,
        candidate_hash=f"hash-{composition}", candidate_layers=(1,), potential_useful_coverage=0,
        potential_coverage_percentage=coverage_pct,
    )


def marker(name, composition, length, waste=100):
    piece_area = 1000
    return ValidatedMarkerCandidate(
        marker_hash=name, content_key=f"key-{name}", composition=tuple(composition.items()),
        marker_length_units=length, usable_width_units=100, piece_area_units2=piece_area,
        marker_area_units2=piece_area + waste, waste_area_units2=waste,
        efficiency_percentage=piece_area / (piece_area + waste) * 100,
        placements=tuple({"piece": index} for index in range(sum(composition.values()) * 5)),
        validation_certificate={"status": "VALIDATED", "checks": {}}, geometry_engine_version="test",
        marker_search_status="FEASIBLE_NOT_PROVEN_BEST", input_hash=f"input-{name}", lower_bound_length_units=1,
    )


# ---- coverage_priority_score ---------------------------------------------------

def test_coverage_priority_score_rewards_missing_size_coverage():
    covers_new_size = _candidate((("XS", 1), ("XL", 1)), coverage_pct=10.0)
    covers_known_sizes_only = _candidate((("M", 1), ("L", 1)), coverage_pct=10.0)
    covered = frozenset({"M", "L", "S"})
    score_new = coverage_priority_score(covers_new_size, covered)
    score_known = coverage_priority_score(covers_known_sizes_only, covered)
    assert score_new > score_known
    # Two uncovered sizes (XS, XL) -> 2x the missing-size bonus of one.
    only_one_new = _candidate((("XS", 1), ("L", 1)), coverage_pct=10.0)
    assert score_new > coverage_priority_score(only_one_new, covered)


def test_coverage_priority_score_penalizes_redundant_size_sets():
    candidate = _candidate((("XS", 2), ("XL", 1)), coverage_pct=10.0)
    covered = frozenset()
    fresh_score = coverage_priority_score(candidate, covered, seen_size_sets=frozenset())
    redundant_score = coverage_priority_score(candidate, covered, seen_size_sets=frozenset({frozenset({"XS", "XL"})}))
    assert redundant_score < fresh_score


# ---- pareto_filter (conservative, same-composition-only dominance) --------------

def test_pareto_filter_keeps_shorter_less_wasteful_same_composition_marker():
    worse = marker("worse", {"XS": 1, "XL": 1}, length=200, waste=300)
    better = marker("better", {"XS": 1, "XL": 1}, length=180, waste=200)
    retained = pareto_filter((worse, better))
    assert retained == (better,)


def test_pareto_filter_never_compares_across_different_compositions():
    # "high_eff" has much better efficiency than "low_eff", but a DIFFERENT
    # composition (different production vector) -- must never be dropped for
    # that reason alone (phase spec Section 8: be conservative).
    high_eff = marker("high_eff", {"M": 4}, length=100, waste=10)
    low_eff = marker("low_eff", {"XS": 1, "XL": 1}, length=300, waste=500)
    retained = pareto_filter((high_eff, low_eff))
    assert {item.marker_hash for item in retained} == {"high_eff", "low_eff"}


# ---- diagnose_infeasibility -------------------------------------------------------

def test_diagnose_infeasibility_reports_uncovered_sizes_and_residual():
    catalog = (
        marker("m2l1", {"M": 2, "L": 1}, length=218_000, waste=1000),
        marker("s3xxl5", {"S": 3, "XXL": 5}, length=660_000, waste=2000),
    )
    demand = {"XS": 30, "S": 33, "M": 42, "L": 21, "XL": 35, "XXL": 55, "XXXL": 0}
    diagnosis = diagnose_infeasibility(catalog, demand, max_layers=55)
    assert diagnosis.sizes_with_no_catalog_coverage == ("XL", "XS")
    assert set(diagnosis.sizes_covered) == {"M", "L", "S", "XXL"}
    assert diagnosis.residual_by_size["XS"] == 30  # no coverage at all -> full residual remains
    assert diagnosis.residual_by_size["XL"] == 35
    assert diagnosis.residual_by_size["S"] == 0  # 3 * 55 max_layers far exceeds demand of 33
