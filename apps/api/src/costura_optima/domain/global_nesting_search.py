"""Phase 2F.7H: bounded, deterministic ALNS-style global nesting search.

This is a NEW layer on top of the already-stable geometry/candidate/validator
infrastructure (CandidateSpaceEngine, DeterministicCompletionRunner,
IncrementalCandidateValidator, IndependentMarkerValidator). It does not
reimplement geometry: destroy/repair operators call back into
DeterministicCompletionRunner._evaluate_piece for candidate generation and
IndependentMarkerValidator for full-state certification.

Scope (first implementation, intentionally bounded per phase Section 13):
  - Destroy: RANDOM_K_REMOVAL, TAIL_REMOVAL, WORST_CONTRIBUTOR_REMOVAL.
  - Repair: sequential reinsertion with a small deterministic beam, top-K
    candidates per piece, two reinsertion-order strategies
    (ORIGINAL_ORDER, LARGEST_FIRST).
  - Acceptance: bounded simulated-annealing-style acceptance with a seeded RNG.
  - Single-threaded, seeded, iteration- and time-budgeted, anytime (best
    validated state is tracked and returned even on early stop).
Deferred (not implemented in this pass, reported honestly as such):
  REGION_REMOVAL, SLEEVE_CLUSTER_REMOVAL, SMALL_PIECE_REMOVAL,
  MOST_CONSTRAINED_FIRST / SMALL_FILL_LAST / RANDOM_DETERMINISTIC / TAIL_FIRST
  reinsert strategies, operator-weight adaptation (uniform weights only),
  disk checkpoint/resume, local shift-left/group compaction, ablation study.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from random import Random
from time import perf_counter
import json

from costura_optima.domain.completion_runner import DeterministicCompletionRunner
from costura_optima.domain.integer_kernel import (
    GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash,
)
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.nesting_models import MarkerRequest, Placement


DESTROY_OPERATORS = ("RANDOM_K_REMOVAL", "TAIL_REMOVAL", "WORST_CONTRIBUTOR_REMOVAL")
REINSERT_STRATEGIES = ("LARGEST_FIRST", "ORIGINAL_ORDER")


@dataclass(frozen=True)
class SearchState:
    placements: tuple[Placement, ...]
    marker_length_units: int
    layout_hash: str
    iteration: int
    parent_id: str | None
    operator: str | None

    @property
    def state_id(self) -> str:
        return self.layout_hash


def make_state(placements: tuple[Placement, ...], iteration: int, parent_id: str | None, operator: str | None) -> SearchState:
    length = max(p.bbox[2] for p in placements)
    layout_hash = canonical_json_hash([asdict(item) for item in placements])
    return SearchState(placements, length, layout_hash, iteration, parent_id, operator)


def _score_key(placement: Placement, candidate, kept: tuple[Placement, ...]):
    """Mirrors DeterministicCompletionRunner._choose's lexicographic key.

    Duplicated here (read-only) rather than reaching into the stable runner's
    private sort, per Section 3: this file must not modify closed geometry/
    scoring code, only call it.
    """
    rank = {"NFP_NFP_INTERSECTION": 0, "NFP_IFP_INTERSECTION": 1, "FEASIBLE_BOUNDARY": 2,
            "NFP_VERTEX": 3, "IFP_VERTEX": 4, "FEASIBLE_INTERIOR_RECOVERY": 5,
            "EXACT_REPAIRED_BOUNDARY": 6, "LEGACY_FALLBACK": 7}
    source_rank = min(rank.get(source, 99) for source in candidate.sources)
    resulting_length = max([placement.bbox[2], *(p.bbox[2] for p in kept)])
    return (resulting_length, -candidate.contact_count, source_rank, placement.translation[0], placement.translation[1])


class GlobalNestingSearch:
    """Bounded ALNS over the unified two-way candidate pool for one marker."""

    def __init__(self, request: MarkerRequest, by_id: dict, *, seed: int, candidate_budget: int = 5000,
                 top_k: int = 5, beam_width: int = 3, piece_time_budget_ms: int = 60_000,
                 heartbeat=None):
        self.request = request
        self.by_id = by_id
        self.rng = Random(seed)
        self.seed = seed
        self.kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())
        self.full_validator = IndependentMarkerValidator()
        # A single reusable runner: _evaluate_piece takes `placements` as an
        # explicit argument, so one instance safely serves every repair call
        # regardless of which state/beam is being repaired. Its own
        # constructor order-matching contract is irrelevant here because we
        # never call .run() / ._checkpoint() on it.
        self._pool_runner = DeterministicCompletionRunner(
            request, (), Path("artifacts/phase2f7h/_pool_runner_scratch"),
            piece_order=tuple(by_id), candidate_budget=candidate_budget, recovery_budget=65,
            piece_time_budget_ms=piece_time_budget_ms, candidate_mode="UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE",
            orientation_policy="TWO_WAY", depth2_diagnostics=False,
        )
        self.top_k = top_k
        self.beam_width = beam_width
        self.heartbeat = heartbeat
        self.visited_layout_hashes: set[str] = set()
        self.incumbent_history: list[dict] = []
        self.search_trace: list[dict] = []

    # ---- destroy operators -------------------------------------------------
    def _pick_k(self) -> int:
        return self.rng.choice((1, 2, 3, 4))

    def destroy(self, state: SearchState, operator: str, k: int):
        ids = [p.piece_instance_id for p in state.placements]
        if operator == "RANDOM_K_REMOVAL":
            removed = set(self.rng.sample(ids, min(k, len(ids) - 1)))
        elif operator == "TAIL_REMOVAL":
            ordered = sorted(state.placements, key=lambda p: -p.bbox[2])
            removed = {p.piece_instance_id for p in ordered[:k]}
        elif operator == "WORST_CONTRIBUTOR_REMOVAL":
            def envelope(p):
                return (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1])
            ordered = sorted(state.placements, key=lambda p: (-p.bbox[2], -envelope(p)))
            removed = {p.piece_instance_id for p in ordered[:k]}
        else:
            raise ValueError(f"Unknown destroy operator: {operator}")
        kept = tuple(p for p in state.placements if p.piece_instance_id not in removed)
        return kept, tuple(removed)

    # ---- reinsertion order strategies --------------------------------------
    def reinsert_order(self, removed_ids: tuple[str, ...], strategy: str, original_order: tuple[str, ...]) -> tuple[str, ...]:
        if strategy == "LARGEST_FIRST":
            return tuple(sorted(removed_ids, key=lambda pid: -self.kernel.area_units2(self.by_id[pid].piece.cut_polygon)))
        if strategy == "ORIGINAL_ORDER":
            index = {pid: i for i, pid in enumerate(original_order)}
            return tuple(sorted(removed_ids, key=lambda pid: index[pid]))
        raise ValueError(f"Unknown reinsert strategy: {strategy}")

    # ---- repair (small beam, top-K candidates per piece) -------------------
    def repair(self, kept: tuple[Placement, ...], reinsert_ids: tuple[str, ...]):
        beams: list[tuple[Placement, ...]] = [kept]
        for pid in reinsert_ids:
            instance = self.by_id[pid]
            rotations = self._pool_runner._effective_rotations(instance)
            next_beams: list[tuple[Placement, ...]] = []
            for beam in beams:
                result = self._pool_runner._evaluate_piece(instance, list(beam), len(beam) + 1, rotations)
                if not result["accepted"]:
                    continue
                ranked = sorted(result["accepted"], key=lambda pair: _score_key(pair[0], pair[1], beam))
                for placement, _candidate in ranked[: self.top_k]:
                    next_beams.append(beam + (placement,))
            if not next_beams:
                return None
            next_beams.sort(key=lambda placements: max(p.bbox[2] for p in placements))
            beams = next_beams[: self.beam_width]
        return beams[0] if beams else None

    # ---- acceptance ---------------------------------------------------------
    def accept(self, current_length: int, candidate_length: int, temperature: float) -> bool:
        if candidate_length <= current_length:
            return True
        if temperature <= 0:
            return False
        import math
        probability = math.exp(-(candidate_length - current_length) / temperature)
        return self.rng.random() < probability

    # ---- main loop ------------------------------------------------------------
    def run(self, initial_state: SearchState, *, max_iterations: int, max_runtime_s: float,
            initial_temperature: float = 400.0, cooling: float = 0.995) -> dict:
        original_order = tuple(p.piece_instance_id for p in initial_state.placements)
        current = initial_state
        best = initial_state
        temperature = initial_temperature
        started = perf_counter()
        last_heartbeat = started
        accepted_states = 0
        stagnation = 0
        stagnation_window = 40
        first_baseline_beat = None
        gates_hit = {}

        self.visited_layout_hashes.add(initial_state.layout_hash)
        self.incumbent_history.append({
            "iteration": 0, "runtime_s": 0.0, "length_units": initial_state.marker_length_units,
            "efficiency_pct": None, "operator": "SEED", "layout_hash": initial_state.layout_hash,
        })

        iteration = 0
        while iteration < max_iterations and (perf_counter() - started) < max_runtime_s:
            iteration += 1
            operator = self.rng.choice(DESTROY_OPERATORS)
            k = self._pick_k()
            strategy = self.rng.choice(REINSERT_STRATEGIES)
            kept, removed_ids = self.destroy(current, operator, k)
            reinsert_ids = self.reinsert_order(removed_ids, strategy, original_order)
            repaired_placements = self.repair(kept, reinsert_ids)

            trace_row = {"iteration": iteration, "destroy_operator": operator, "k": k,
                         "reinsert_strategy": strategy, "removed_pieces": list(removed_ids),
                         "repair_succeeded": repaired_placements is not None}
            if repaired_placements is None:
                trace_row.update({"result_length": None, "validated": False, "accepted": False, "incumbent_changed": False})
                self.search_trace.append(trace_row)
                continue

            full_report = self.full_validator.validate(self.request, repaired_placements, self.request.max_length)
            validated = full_report.status == "VALIDATED"
            if not validated:
                trace_row.update({"result_length": None, "validated": False, "accepted": False, "incumbent_changed": False})
                self.search_trace.append(trace_row)
                continue

            candidate_state = make_state(repaired_placements, iteration, current.state_id, f"{operator}+{strategy}")
            self.visited_layout_hashes.add(candidate_state.layout_hash)
            accepted = self.accept(current.marker_length_units, candidate_state.marker_length_units, temperature)
            incumbent_changed = False
            if accepted:
                current = candidate_state
                accepted_states += 1
                if candidate_state.marker_length_units < best.marker_length_units:
                    best = candidate_state
                    incumbent_changed = True
                    stagnation = 0
                    self.incumbent_history.append({
                        "iteration": iteration, "runtime_s": round(perf_counter() - started, 3),
                        "length_units": best.marker_length_units, "operator": candidate_state.operator,
                        "layout_hash": best.layout_hash,
                    })
                    if first_baseline_beat is None and best.marker_length_units < 229_500:
                        first_baseline_beat = iteration
                else:
                    stagnation += 1
            else:
                stagnation += 1

            temperature *= cooling
            if stagnation >= stagnation_window:
                temperature = max(temperature, initial_temperature * 0.25)  # deterministic bounded reheat
                stagnation = 0

            trace_row.update({"result_length": candidate_state.marker_length_units, "validated": True,
                               "accepted": accepted, "incumbent_changed": incumbent_changed})
            self.search_trace.append(trace_row)

            now = perf_counter()
            if self.heartbeat is not None and (iteration % 100 == 0 or now - last_heartbeat >= 30):
                last_heartbeat = now
                self.heartbeat({
                    "iteration": iteration, "max_iterations": max_iterations,
                    "current_length_units": current.marker_length_units, "best_length_units": best.marker_length_units,
                    "accepted_states": accepted_states, "incumbent_improvements": len(self.incumbent_history) - 1,
                    "elapsed_s": round(now - started, 1),
                })

        return {
            "iterations": iteration, "runtime_s": round(perf_counter() - started, 3),
            "best_state": best, "final_state": current, "accepted_states": accepted_states,
            "unique_valid_layouts_visited": len(self.visited_layout_hashes),
            "first_baseline_beat_iteration": first_baseline_beat,
            "seed": self.seed,
        }
