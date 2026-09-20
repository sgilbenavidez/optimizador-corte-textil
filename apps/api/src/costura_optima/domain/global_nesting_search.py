"""Phase 2F.7H(-1): bounded, deterministic ALNS-style global nesting search.

This is a NEW layer on top of the already-stable geometry/candidate/validator
infrastructure (CandidateSpaceEngine, DeterministicCompletionRunner,
IncrementalCandidateValidator, IndependentMarkerValidator). It does not
reimplement geometry: destroy/repair/compaction operators call back into
DeterministicCompletionRunner._evaluate_piece, IncrementalCandidateValidator
and IndependentMarkerValidator for candidate generation and certification.

Scope (Phase 2F.7H-1 adds to the original 2F.7H first pass):
  - Destroy: RANDOM_K_REMOVAL, TAIL_REMOVAL, WORST_CONTRIBUTOR_REMOVAL (2F.7H)
    plus REGION_REMOVAL, SLEEVE_CLUSTER_REMOVAL, SMALL_PIECE_REMOVAL (2F.7H-1).
  - Repair: sequential reinsertion with a small deterministic beam, top-K
    candidates per piece, reinsertion-order strategies ORIGINAL_ORDER,
    LARGEST_FIRST (2F.7H) plus CONTACT_POTENTIAL_FIRST (2F.7H-1).
  - Local compaction (2F.7H-1): SHIFT_LEFT, applied after every validated
    repair, structurally guaranteed to never lengthen the marker (see
    ``compact``). GAP_CLOSING is not a separate operator: ascending-x
    SHIFT_LEFT already performs its practical effect. SMALL_PIECE_REFILL is
    deferred (documented, not implemented) -- it needs cavity-detection logic
    that does not cleanly reuse an existing primitive.
  - Acceptance: bounded simulated-annealing-style acceptance with a seeded RNG.
  - Per-operator instrumentation (proposed/repaired/validated/accepted/
    incumbent-improving counts, compaction gain) for future weight adaptation
    (not implemented yet -- Section 6 of the phase spec explicitly defers it).
  - Deterministic checkpoint/resume of the ALNS loop itself (iteration, RNG
    state, current/best placements, operator stats) -- distinct from and in
    addition to DeterministicCompletionRunner's write-only per-piece
    checkpoints for the linear greedy phase.
  - Single-threaded, seeded, iteration- and time-budgeted, anytime (best
    validated state is tracked and returned even on early stop).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from pathlib import Path
from random import Random
from time import perf_counter
import json

from costura_optima.domain.completion_runner import DeterministicCompletionRunner
from costura_optima.domain.integer_kernel import (
    GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash,
    path_bbox, rotate_and_translate_point, transform_piece,
)
from costura_optima.domain.marker_validator import IncrementalCandidateValidator, IndependentMarkerValidator
from costura_optima.domain.nesting_models import MarkerRequest, Placement


DESTROY_OPERATORS = (
    "RANDOM_K_REMOVAL", "TAIL_REMOVAL", "WORST_CONTRIBUTOR_REMOVAL",
    "REGION_REMOVAL", "SLEEVE_CLUSTER_REMOVAL", "SMALL_PIECE_REMOVAL",
)
REINSERT_STRATEGIES = ("LARGEST_FIRST", "ORIGINAL_ORDER", "CONTACT_POTENTIAL_FIRST")
SMALL_PIECE_FAMILY = frozenset({"SLEEVE", "NECKBAND"})
CHECKPOINT_FORMAT_VERSION = 1


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


def _bbox_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    """Cheap axis-aligned bbox separation (L1, bbox-only, no NFP/shapely calls).

    Zero means the bboxes already touch or overlap on both axes.
    """
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return dx + dy


def _placement_from_row(row: dict) -> Placement:
    """Reconstruct a Placement from its ``dataclasses.asdict`` JSON round-trip.

    Mirrors the reconstruction already used ad hoc in
    ``run_phase2f7h_global_search.py::_seed_placements`` -- consolidated here
    since checkpoint/resume needs the identical conversion.
    """
    return Placement(
        row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"],
        row["rotation"], row["mirrored"], tuple(row["translation"]),
        tuple(tuple(point) for point in row["transformed_polygon"]),
        tuple(tuple(point) for point in row["transformed_grainline"]),
        tuple(row["bbox"]), row["geometry_hash"], row["sequence"],
    )


def _serialize_rng_state(rng: Random) -> list:
    version, internal_state, gauss_next = rng.getstate()
    return [version, list(internal_state), gauss_next]


def _deserialize_rng_state(data: list) -> tuple:
    version, internal_state, gauss_next = data
    return (version, tuple(internal_state), gauss_next)


def _empty_operator_stats() -> dict:
    counters = lambda: {"proposed": 0, "repair_succeeded": 0, "validated": 0, "accepted": 0, "incumbent_improvements": 0}
    return {
        "destroy": {operator: counters() for operator in DESTROY_OPERATORS},
        "reinsert": {strategy: counters() for strategy in REINSERT_STRATEGIES},
        "compaction": {"applied": 0, "iterations_improved": 0, "length_reduction_units_total": 0},
    }


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
        self.operator_stats: dict = _empty_operator_stats()

    # ---- destroy operators -------------------------------------------------
    def _pick_k(self) -> int:
        return self.rng.choice((1, 2, 3, 4))

    def destroy(self, state: SearchState, operator: str, k: int):
        placements = state.placements
        ids = [p.piece_instance_id for p in placements]
        if operator == "RANDOM_K_REMOVAL":
            removed = set(self.rng.sample(ids, min(k, len(ids) - 1)))
        elif operator == "TAIL_REMOVAL":
            ordered = sorted(placements, key=lambda p: -p.bbox[2])
            removed = {p.piece_instance_id for p in ordered[:k]}
        elif operator == "WORST_CONTRIBUTOR_REMOVAL":
            def envelope(p):
                return (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1])
            ordered = sorted(placements, key=lambda p: (-p.bbox[2], -envelope(p)))
            removed = {p.piece_instance_id for p in ordered[:k]}
        elif operator == "REGION_REMOVAL":
            # Perturbs a spatial neighborhood as a block (unlike RANDOM_K's
            # scattered removal or TAIL's end-only removal), so a locally-bad
            # region can be re-packed together instead of piecemeal.
            length = max(p.bbox[2] for p in placements)
            window = max(1, int(length * self.rng.choice((0.15, 0.2, 0.25, 0.3))))
            start = self.rng.randint(0, max(0, length - window))
            end = start + window
            removed = {p.piece_instance_id for p in placements
                       if start <= (p.bbox[0] + p.bbox[2]) // 2 <= end}
            if not removed:
                nearest = min(placements, key=lambda p: abs((p.bbox[0] + p.bbox[2]) // 2 - (start + end) // 2))
                removed = {nearest.piece_instance_id}
        elif operator == "SLEEVE_CLUSTER_REMOVAL":
            # Small pieces are typically gap-fillers; removing the cluster
            # together lets repair re-slot the whole family instead of one
            # piece locking in a first-fit position that blocks its neighbor.
            candidates = [p for p in placements if p.piece_code in SMALL_PIECE_FAMILY]
            if not candidates:
                candidates = list(placements)
            removed = {p.piece_instance_id for p in candidates[: max(1, min(k, len(candidates)))]}
        elif operator == "SMALL_PIECE_REMOVAL":
            # Cheap to reinsert (many legal candidate positions), so the
            # search can explore many small-scale repackings without paying
            # for large-piece re-placement cost.
            ordered = sorted(placements, key=lambda p: self.kernel.area_units2(self.by_id[p.piece_instance_id].piece.cut_polygon))
            removed = {p.piece_instance_id for p in ordered[:k]}
        else:
            raise ValueError(f"Unknown destroy operator: {operator}")
        if len(removed) >= len(ids):
            removed = set(sorted(removed)[: len(ids) - 1])
        kept = tuple(p for p in placements if p.piece_instance_id not in removed)
        return kept, tuple(removed)

    # ---- reinsertion order strategies --------------------------------------
    def reinsert_order(self, removed_placements: tuple[Placement, ...], strategy: str,
                        original_order: tuple[str, ...], kept: tuple[Placement, ...]) -> tuple[str, ...]:
        removed_ids = tuple(p.piece_instance_id for p in removed_placements)
        if strategy == "LARGEST_FIRST":
            return tuple(sorted(removed_ids, key=lambda pid: -self.kernel.area_units2(self.by_id[pid].piece.cut_polygon)))
        if strategy == "ORIGINAL_ORDER":
            index = {pid: i for i, pid in enumerate(original_order)}
            return tuple(sorted(removed_ids, key=lambda pid: index[pid]))
        if strategy == "CONTACT_POTENTIAL_FIRST":
            # Prioritizes reinserting pieces that already had a nearby
            # geometric opportunity (a kept-piece boundary close to their
            # pre-removal position), so repair claims that contact/gap-filling
            # position before a later piece in the same pass consumes it.
            # Bbox-only distance: no NFP calls, cheap by construction.
            if not kept:
                index = {pid: i for i, pid in enumerate(original_order)}
                return tuple(sorted(removed_ids, key=lambda pid: index[pid]))
            def gap(placement: Placement) -> int:
                return min(_bbox_gap(placement.bbox, other.bbox) for other in kept)
            ranked = sorted(removed_placements, key=lambda p: (gap(p), p.piece_instance_id))
            return tuple(p.piece_instance_id for p in ranked)
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

    # ---- local compaction (SHIFT_LEFT) -------------------------------------
    @staticmethod
    def _translated(instance, placement: Placement, x: int) -> Placement:
        y = placement.translation[1]
        polygon = transform_piece(instance.piece.cut_polygon, placement.rotation, (x, y))
        grainline = tuple(rotate_and_translate_point(point, placement.rotation, instance.piece.cut_polygon, (x, y))
                           for point in instance.piece.grainline)
        return replace(placement, translation=(x, y), transformed_polygon=polygon,
                        transformed_grainline=grainline, bbox=path_bbox(polygon))

    def compact(self, placements: tuple[Placement, ...], *, max_passes: int = 3) -> tuple[tuple[Placement, ...], int]:
        """SHIFT_LEFT local compaction (Section 5 of the phase spec).

        Processes pieces in ascending-x order; for each, binary-searches the
        minimal legal x-translation against a fresh IncrementalCandidateValidator
        built from the OTHER current placements (containment/overlap/clearance/
        grainline/orientation all checked identically to the production hot
        path). Ascending-x order already performs practical gap-closing:
        earlier pieces settle first, so later pieces compact into the space
        they vacate -- this is why GAP_CLOSING is not a separate operator.

        The binary search assumes validity is roughly monotonic in x for a
        single piece against a fixed obstacle set; that assumption is a
        heuristic, not a geometric guarantee, but ``best`` only ever holds an
        already-validated candidate, so a monotonicity violation can only
        make this pass find a smaller improvement, never an invalid one.

        Structural guarantee: the result is only ever returned if it both (a)
        independently re-validates as a whole via IndependentMarkerValidator
        and (b) has marker length <= the input's. Otherwise the original
        placements are returned unchanged -- "never worsens length" is
        enforced by comparison, not assumed.
        """
        working = list(placements)
        region_x_min = self.request.margins.start
        moved_any = False
        for _ in range(max_passes):
            working.sort(key=lambda p: p.translation[0])
            moved_this_pass = False
            for index, placement in enumerate(working):
                instance = self.by_id[placement.piece_instance_id]
                others = tuple(item for i, item in enumerate(working) if i != index)
                validator = IncrementalCandidateValidator(self.request, others)
                lo, hi = region_x_min, placement.translation[0]
                best = placement
                while lo < hi:
                    mid = (lo + hi) // 2
                    candidate = self._translated(instance, placement, mid)
                    if validator.validate(instance, candidate).accepted:
                        best = candidate
                        hi = mid
                    else:
                        lo = mid + 1
                if best.translation[0] < placement.translation[0]:
                    working[index] = best
                    moved_this_pass = True
            moved_any = moved_any or moved_this_pass
            if not moved_this_pass:
                break
        if not moved_any:
            return placements, 0
        compacted = tuple(working)
        original_length = max(p.bbox[2] for p in placements)
        compacted_length = max(p.bbox[2] for p in compacted)
        if compacted_length > original_length:
            return placements, 0
        report = self.full_validator.validate(self.request, compacted, self.request.max_length)
        if report.status != "VALIDATED":
            return placements, 0
        return compacted, original_length - compacted_length

    # ---- acceptance ---------------------------------------------------------
    def accept(self, current_length: int, candidate_length: int, temperature: float) -> bool:
        if candidate_length <= current_length:
            return True
        if temperature <= 0:
            return False
        import math
        probability = math.exp(-(candidate_length - current_length) / temperature)
        return self.rng.random() < probability

    # ---- checkpoint/resume ---------------------------------------------------
    def _write_checkpoint(self, path: Path, *, current: SearchState, best: SearchState, iteration: int,
                           temperature: float, stagnation: int, accepted_states: int,
                           first_baseline_beat: int | None, original_order: tuple[str, ...]) -> None:
        payload = {
            "checkpoint_format_version": CHECKPOINT_FORMAT_VERSION,
            "request_id": self.request.request_id,
            "seed": self.seed,
            "iteration": iteration,
            "temperature": temperature,
            "stagnation": stagnation,
            "accepted_states": accepted_states,
            "first_baseline_beat_iteration": first_baseline_beat,
            "original_order": list(original_order),
            "rng_state": _serialize_rng_state(self.rng),
            "current_placements": [asdict(item) for item in current.placements],
            "best_placements": [asdict(item) for item in best.placements],
            "current_operator": current.operator,
            "best_operator": best.operator,
            "operator_stats": self.operator_stats,
            "incumbent_history": self.incumbent_history,
            "search_trace": self.search_trace,
            "visited_layout_hashes": sorted(self.visited_layout_hashes),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def load_checkpoint(path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("checkpoint_format_version") != CHECKPOINT_FORMAT_VERSION:
            raise ValueError(f"Unsupported checkpoint format version: {data.get('checkpoint_format_version')}")
        return data

    # ---- main loop ------------------------------------------------------------
    def run(self, initial_state: SearchState | None = None, *, max_iterations: int, max_runtime_s: float,
            initial_temperature: float = 400.0, cooling: float = 0.995,
            checkpoint_path: Path | None = None, checkpoint_every: int = 25,
            resume_checkpoint: dict | None = None) -> dict:
        if resume_checkpoint is not None:
            self.rng.setstate(_deserialize_rng_state(resume_checkpoint["rng_state"]))
            current = make_state(
                tuple(_placement_from_row(row) for row in resume_checkpoint["current_placements"]),
                resume_checkpoint["iteration"], None, resume_checkpoint.get("current_operator"),
            )
            best = make_state(
                tuple(_placement_from_row(row) for row in resume_checkpoint["best_placements"]),
                resume_checkpoint["iteration"], None, resume_checkpoint.get("best_operator"),
            )
            original_order = tuple(resume_checkpoint["original_order"])
            temperature = resume_checkpoint["temperature"]
            accepted_states = resume_checkpoint["accepted_states"]
            stagnation = resume_checkpoint.get("stagnation", 0)
            first_baseline_beat = resume_checkpoint.get("first_baseline_beat_iteration")
            self.operator_stats = resume_checkpoint["operator_stats"]
            self.incumbent_history = resume_checkpoint["incumbent_history"]
            self.search_trace = resume_checkpoint["search_trace"]
            self.visited_layout_hashes = set(resume_checkpoint["visited_layout_hashes"])
            iteration = resume_checkpoint["iteration"]
        else:
            if initial_state is None:
                raise ValueError("initial_state is required unless resume_checkpoint is provided")
            original_order = tuple(p.piece_instance_id for p in initial_state.placements)
            current = initial_state
            best = initial_state
            temperature = initial_temperature
            accepted_states = 0
            stagnation = 0
            first_baseline_beat = None
            self.visited_layout_hashes.add(initial_state.layout_hash)
            self.incumbent_history.append({
                "iteration": 0, "runtime_s": 0.0, "length_units": initial_state.marker_length_units,
                "efficiency_pct": None, "operator": "SEED", "layout_hash": initial_state.layout_hash,
            })
            iteration = 0

        started = perf_counter()
        last_heartbeat = started
        stagnation_window = 40

        while iteration < max_iterations and (perf_counter() - started) < max_runtime_s:
            iteration += 1
            operator = self.rng.choice(DESTROY_OPERATORS)
            k = self._pick_k()
            strategy = self.rng.choice(REINSERT_STRATEGIES)
            destroy_stats = self.operator_stats["destroy"][operator]
            reinsert_stats = self.operator_stats["reinsert"][strategy]
            destroy_stats["proposed"] += 1
            reinsert_stats["proposed"] += 1

            kept, removed_ids = self.destroy(current, operator, k)
            removed_placements = tuple(p for p in current.placements if p.piece_instance_id in removed_ids)
            reinsert_ids = self.reinsert_order(removed_placements, strategy, original_order, kept)
            repaired_placements = self.repair(kept, reinsert_ids)

            trace_row = {"iteration": iteration, "destroy_operator": operator, "k": k,
                         "reinsert_strategy": strategy, "removed_pieces": list(removed_ids),
                         "repair_succeeded": repaired_placements is not None}
            if repaired_placements is None:
                trace_row.update({"result_length": None, "validated": False, "accepted": False,
                                  "incumbent_changed": False, "compaction_gain_units": 0})
                self.search_trace.append(trace_row)
                continue
            destroy_stats["repair_succeeded"] += 1
            reinsert_stats["repair_succeeded"] += 1

            full_report = self.full_validator.validate(self.request, repaired_placements, self.request.max_length)
            validated = full_report.status == "VALIDATED"
            if not validated:
                trace_row.update({"result_length": None, "validated": False, "accepted": False,
                                  "incumbent_changed": False, "compaction_gain_units": 0})
                self.search_trace.append(trace_row)
                continue
            destroy_stats["validated"] += 1
            reinsert_stats["validated"] += 1

            compacted_placements, compaction_gain = self.compact(repaired_placements)
            if compaction_gain > 0:
                self.operator_stats["compaction"]["applied"] += 1
                self.operator_stats["compaction"]["iterations_improved"] += 1
                self.operator_stats["compaction"]["length_reduction_units_total"] += compaction_gain

            candidate_state = make_state(compacted_placements, iteration, current.state_id, f"{operator}+{strategy}")
            self.visited_layout_hashes.add(candidate_state.layout_hash)
            accepted = self.accept(current.marker_length_units, candidate_state.marker_length_units, temperature)
            incumbent_changed = False
            if accepted:
                current = candidate_state
                accepted_states += 1
                destroy_stats["accepted"] += 1
                reinsert_stats["accepted"] += 1
                if candidate_state.marker_length_units < best.marker_length_units:
                    best = candidate_state
                    incumbent_changed = True
                    stagnation = 0
                    destroy_stats["incumbent_improvements"] += 1
                    reinsert_stats["incumbent_improvements"] += 1
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
                               "accepted": accepted, "incumbent_changed": incumbent_changed,
                               "compaction_gain_units": compaction_gain})
            self.search_trace.append(trace_row)

            if checkpoint_path is not None and iteration % checkpoint_every == 0:
                self._write_checkpoint(checkpoint_path, current=current, best=best, iteration=iteration,
                                        temperature=temperature, stagnation=stagnation,
                                        accepted_states=accepted_states, first_baseline_beat=first_baseline_beat,
                                        original_order=original_order)

            now = perf_counter()
            if self.heartbeat is not None and (iteration % 100 == 0 or now - last_heartbeat >= 30):
                last_heartbeat = now
                self.heartbeat({
                    "iteration": iteration, "max_iterations": max_iterations,
                    "current_length_units": current.marker_length_units, "best_length_units": best.marker_length_units,
                    "accepted_states": accepted_states, "incumbent_improvements": len(self.incumbent_history) - 1,
                    "elapsed_s": round(now - started, 1),
                })

        if checkpoint_path is not None:
            self._write_checkpoint(checkpoint_path, current=current, best=best, iteration=iteration,
                                    temperature=temperature, stagnation=stagnation,
                                    accepted_states=accepted_states, first_baseline_beat=first_baseline_beat,
                                    original_order=original_order)

        return {
            "iterations": iteration, "runtime_s": round(perf_counter() - started, 3),
            "best_state": best, "final_state": current, "accepted_states": accepted_states,
            "unique_valid_layouts_visited": len(self.visited_layout_hashes),
            "first_baseline_beat_iteration": first_baseline_beat,
            "seed": self.seed, "operator_stats": self.operator_stats,
        }
