"""Regression tests for FASE 2F.7H-1: destroy/repair operator expansion,
local compaction and ALNS checkpoint/resume.

No pytest coverage existed for global_nesting_search.py before this phase
(the original 2F.7H evidence was script/artifact-based only, per
docs/ESTADO-CONSOLIDADO-2026-09-12.md). These fixtures deliberately use small
rectangular pieces (same convention as test_nesting_geometry.py) so the real
NFP/candidate-space machinery stays fast enough for unit tests while still
exercising the production code path end to end.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from costura_optima.domain.global_nesting_search import (
    DESTROY_OPERATORS,
    REINSERT_STRATEGIES,
    GlobalNestingSearch,
    make_state,
)
from costura_optima.domain.integer_kernel import (
    canonical_path,
    path_bbox,
    rotate_and_translate_point,
    transform_piece,
)
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import (
    MarkerMargins,
    MarkerRequest,
    NestingPiece,
    Placement,
    PieceInstance,
    PrecisionConfiguration,
)


PRECISION = PrecisionConfiguration()
CLEARANCE = 500
GAP = 3000  # deliberately looser than CLEARANCE so compaction has real room to work with


def _rect_piece(instance_id: str, size_code: str, piece_code: str, width: int, height: int) -> PieceInstance:
    polygon = canonical_path([(0, 0), (width, 0), (width, height), (0, height)])
    grain_x = width // 2
    geometry = NestingPiece(
        pattern_piece_id=f"pattern-{piece_code.lower()}-{size_code.lower()}",
        size_code=size_code, piece_code=piece_code, cut_polygon=polygon,
        grainline=((grain_x, height // 10), (grain_x, height - height // 10)),
        allowed_rotations=(0, 180), mirror_allowed=False, geometry_hash=f"hash-{instance_id}",
    )
    return PieceInstance(instance_id, geometry)


def _placement_at(instance: PieceInstance, x: int, y: int, rotation: int, sequence: int) -> Placement:
    polygon = transform_piece(instance.piece.cut_polygon, rotation, (x, y))
    grainline = tuple(rotate_and_translate_point(point, rotation, instance.piece.cut_polygon, (x, y))
                       for point in instance.piece.grainline)
    return Placement(instance.instance_id, instance.piece.pattern_piece_id, instance.piece.size_code,
                      instance.piece.piece_code, rotation, False, (x, y), polygon, grainline,
                      path_bbox(polygon), instance.piece.geometry_hash, sequence)


def build_fixture(*, gap: int = GAP):
    """A 5-piece M-size garment laid out loosely left-to-right (FRONT/BACK/
    SLEEVE_L/SLEEVE_R/NECKBAND), spaced `gap` apart (> CLEARANCE), so a valid
    starting marker exists and SHIFT_LEFT compaction has real slack to close.
    """
    specs = (
        ("M_FRONT_001", "FRONT", 10_000, 20_000),
        ("M_BACK_001", "BACK", 10_000, 20_000),
        ("M_SLEEVE_001", "SLEEVE", 4_000, 8_000),
        ("M_SLEEVE_002", "SLEEVE", 4_000, 8_000),
        ("M_NECKBAND_001", "NECKBAND", 2_000, 3_000),
    )
    instances = [_rect_piece(instance_id, "M", piece_code, width, height) for instance_id, piece_code, width, height in specs]
    by_id = {item.instance_id: item for item in instances}
    marker_request = MarkerRequest(
        request_id="test-2f7h1", piece_instances=tuple(instances), usable_width=30_000, physical_width=30_000,
        max_length=60_000, clearance=CLEARANCE, margins=MarkerMargins(0, 0, 0, 0), allowed_transforms=(0, 180),
        evaluation_budget=10_000, precision=PRECISION,
    )
    placements = []
    x = 0
    for sequence, (instance, (_, _, width, _height)) in enumerate(zip(instances, specs), start=1):
        placements.append(_placement_at(instance, x, 0, 0, sequence))
        x += width + gap
    initial_state = make_state(tuple(placements), 0, None, None)
    return marker_request, by_id, initial_state


def make_search(seed: int = 1, **kwargs) -> tuple[GlobalNestingSearch, MarkerRequest, dict, "make_state"]:
    marker_request, by_id, initial_state = build_fixture()
    search = GlobalNestingSearch(marker_request, by_id, seed=seed, candidate_budget=500,
                                  top_k=3, beam_width=2, piece_time_budget_ms=20_000, **kwargs)
    return search, marker_request, by_id, initial_state


# ---- destroy operator invariants -------------------------------------------

@pytest.mark.parametrize("operator", DESTROY_OPERATORS)
def test_destroy_operator_invariants(operator):
    search, _request, _by_id, initial_state = make_search()
    all_ids = {p.piece_instance_id for p in initial_state.placements}
    kept, removed = search.destroy(initial_state, operator, k=2)
    kept_ids = {p.piece_instance_id for p in kept}
    removed_ids = set(removed)
    assert removed_ids <= all_ids
    assert kept_ids == all_ids - removed_ids
    assert len(removed_ids) >= 1
    assert len(kept_ids) >= 1  # never destroys the whole marker


def test_random_k_removal_is_deterministic_per_seed():
    search_a, _, _, state_a = make_search(seed=7)
    search_b, _, _, state_b = make_search(seed=7)
    _, removed_a = search_a.destroy(state_a, "RANDOM_K_REMOVAL", k=3)
    _, removed_b = search_b.destroy(state_b, "RANDOM_K_REMOVAL", k=3)
    assert removed_a == removed_b


def test_region_removal_is_deterministic_per_seed():
    search_a, _, _, state_a = make_search(seed=11)
    search_b, _, _, state_b = make_search(seed=11)
    _, removed_a = search_a.destroy(state_a, "REGION_REMOVAL", k=2)
    _, removed_b = search_b.destroy(state_b, "REGION_REMOVAL", k=2)
    assert removed_a == removed_b


def test_sleeve_cluster_removal_prefers_small_piece_family():
    search, _, _, initial_state = make_search()
    _, removed = search.destroy(initial_state, "SLEEVE_CLUSTER_REMOVAL", k=2)
    removed_codes = {p.piece_code for p in initial_state.placements if p.piece_instance_id in removed}
    assert removed_codes <= {"SLEEVE", "NECKBAND"}


def test_small_piece_removal_removes_smallest_area_pieces():
    search, _, _, initial_state = make_search()
    kept, removed = search.destroy(initial_state, "SMALL_PIECE_REMOVAL", k=1)
    removed_ids = set(removed)
    assert removed_ids == {"M_NECKBAND_001"}  # smallest piece (2000x3000) in the fixture


# ---- reinsert strategies ----------------------------------------------------

@pytest.mark.parametrize("strategy", REINSERT_STRATEGIES)
def test_reinsert_order_is_a_permutation_of_removed_ids(strategy):
    search, _, _, initial_state = make_search()
    kept, removed_ids = search.destroy(initial_state, "RANDOM_K_REMOVAL", k=3)
    removed_placements = tuple(p for p in initial_state.placements if p.piece_instance_id in removed_ids)
    original_order = tuple(p.piece_instance_id for p in initial_state.placements)
    ordered = search.reinsert_order(removed_placements, strategy, original_order, kept)
    assert set(ordered) == set(removed_ids)
    assert len(ordered) == len(removed_ids)


def test_contact_potential_first_orders_by_bbox_proximity_to_kept():
    search, _, _, initial_state = make_search()
    by_placement_id = {p.piece_instance_id: p for p in initial_state.placements}
    # Keep FRONT only; remove BACK (adjacent, small gap) and NECKBAND (far away).
    kept = (by_placement_id["M_FRONT_001"],)
    removed_placements = (by_placement_id["M_BACK_001"], by_placement_id["M_NECKBAND_001"])
    original_order = tuple(p.piece_instance_id for p in initial_state.placements)
    ordered = search.reinsert_order(removed_placements, "CONTACT_POTENTIAL_FIRST", original_order, kept)
    assert ordered == ("M_BACK_001", "M_NECKBAND_001")


# ---- repair validity ---------------------------------------------------------

def test_repair_result_is_independently_valid_when_it_succeeds():
    search, marker_request, _, initial_state = make_search()
    kept, removed_ids = search.destroy(initial_state, "SMALL_PIECE_REMOVAL", k=1)
    removed_placements = tuple(p for p in initial_state.placements if p.piece_instance_id in removed_ids)
    original_order = tuple(p.piece_instance_id for p in initial_state.placements)
    reinsert_ids = search.reinsert_order(removed_placements, "LARGEST_FIRST", original_order, kept)
    repaired = search.repair(kept, reinsert_ids)
    assert repaired is not None
    report = IndependentMarkerValidator().validate(marker_request, repaired, marker_request.max_length)
    assert report.status == "VALIDATED"


# ---- operator statistics -----------------------------------------------------

def test_operator_statistics_are_internally_consistent():
    search, _, _, initial_state = make_search(seed=3)
    result = search.run(initial_state, max_iterations=10, max_runtime_s=60.0)
    stats = result["operator_stats"]
    total_iterations = result["iterations"]
    proposed_total = sum(row["proposed"] for row in stats["destroy"].values())
    assert proposed_total == total_iterations
    for row in list(stats["destroy"].values()) + list(stats["reinsert"].values()):
        assert row["proposed"] >= row["repair_succeeded"] >= row["validated"] >= row["accepted"] >= row["incumbent_improvements"] >= 0


# ---- checkpoint / resume ------------------------------------------------------

def test_resumed_run_matches_an_uninterrupted_run(tmp_path):
    _, marker_request, by_id, initial_state = make_search(seed=5)

    uninterrupted = GlobalNestingSearch(marker_request, by_id, seed=5, candidate_budget=500,
                                         top_k=3, beam_width=2, piece_time_budget_ms=20_000)
    full_result = uninterrupted.run(initial_state, max_iterations=8, max_runtime_s=60.0)

    checkpoint_path = tmp_path / "checkpoint.json"
    first_half = GlobalNestingSearch(marker_request, by_id, seed=5, candidate_budget=500,
                                      top_k=3, beam_width=2, piece_time_budget_ms=20_000)
    first_half.run(initial_state, max_iterations=4, max_runtime_s=60.0,
                    checkpoint_path=checkpoint_path, checkpoint_every=4)

    resumed = GlobalNestingSearch(marker_request, by_id, seed=5, candidate_budget=500,
                                   top_k=3, beam_width=2, piece_time_budget_ms=20_000)
    checkpoint = GlobalNestingSearch.load_checkpoint(checkpoint_path)
    second_result = resumed.run(max_iterations=8, max_runtime_s=60.0, resume_checkpoint=checkpoint)

    assert second_result["best_state"].layout_hash == full_result["best_state"].layout_hash
    assert second_result["best_state"].marker_length_units == full_result["best_state"].marker_length_units
