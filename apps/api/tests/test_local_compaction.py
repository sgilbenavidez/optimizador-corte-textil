"""Regression tests for FASE 2F.7H-1 SHIFT_LEFT local compaction
(GlobalNestingSearch.compact).

Uses the same 5-piece fixture convention as test_global_nesting_search.py,
deliberately spaced looser than clearance so compaction has real slack to
close (see build_fixture's GAP=3000 vs. CLEARANCE=500).
"""
from __future__ import annotations

from costura_optima.domain.global_nesting_search import GlobalNestingSearch
from costura_optima.domain.marker_validator import IndependentMarkerValidator

from test_global_nesting_search import build_fixture


def make_search(**kwargs):
    marker_request, by_id, initial_state = build_fixture()
    search = GlobalNestingSearch(marker_request, by_id, seed=1, candidate_budget=500,
                                  top_k=3, beam_width=2, piece_time_budget_ms=20_000, **kwargs)
    return search, marker_request, by_id, initial_state


def test_shift_left_compacts_a_loose_fixture_and_reports_the_gain():
    search, _request, _by_id, initial_state = make_search()
    compacted, gain = search.compact(initial_state.placements)
    original_length = max(p.bbox[2] for p in initial_state.placements)
    compacted_length = max(p.bbox[2] for p in compacted)
    assert gain > 0
    assert compacted_length < original_length
    assert compacted_length == original_length - gain
    # Exact tight-packing arithmetic for the fixture's geometry (rectangles,
    # clearance=500, gap=3000): FRONT@0, BACK@10500, SLEEVE_L@21000,
    # SLEEVE_R@25500, NECKBAND@30000 -> marker length 32000.
    assert compacted_length == 32_000


def test_shift_left_never_increases_marker_length():
    search, _request, _by_id, initial_state = make_search()
    compacted, _gain = search.compact(initial_state.placements)
    original_length = max(p.bbox[2] for p in initial_state.placements)
    compacted_length = max(p.bbox[2] for p in compacted)
    assert compacted_length <= original_length


def test_shift_left_result_is_independently_valid():
    search, marker_request, _by_id, initial_state = make_search()
    compacted, gain = search.compact(initial_state.placements)
    assert gain > 0  # otherwise this fixture would trivially pass without exercising anything
    report = IndependentMarkerValidator().validate(marker_request, compacted, marker_request.max_length)
    assert report.status == "VALIDATED"


def test_shift_left_is_idempotent():
    search, _request, _by_id, initial_state = make_search()
    once, first_gain = search.compact(initial_state.placements)
    assert first_gain > 0
    twice, second_gain = search.compact(once)
    assert second_gain == 0
    assert {p.piece_instance_id: p.translation for p in twice} == {p.piece_instance_id: p.translation for p in once}


def test_shift_left_on_an_already_tight_layout_is_a_no_op():
    search, _request, _by_id, initial_state = make_search()
    compacted, _gain = search.compact(initial_state.placements)
    tight, gain = search.compact(compacted)
    assert gain == 0
    assert tight == compacted
