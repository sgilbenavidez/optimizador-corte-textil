"""Finite, resumable CandidateSpace continuation runner.

This runner deliberately owns orchestration only.  Candidate topology, NFP/IFP,
clearance and the independent validator remain the existing authorities.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path
from time import perf_counter

from costura_optima.domain.candidate_space import (
    CandidatePosition, CandidateSpaceEngine, PlacedGeometry, legacy_fallback_candidates,
    repair_candidate_to_clearance,
)
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash, path_bbox, rotate_and_translate_point, transform_and_normalize, transform_piece, translate_path
from costura_optima.domain.marker_validator import IncrementalCandidateValidator, IndependentMarkerValidator
from costura_optima.domain.nesting_models import MarkerRequest, Placement
from costura_optima.domain.transform_policy import EffectiveTransformResolver


CANDIDATE_MODES = ("CANDIDATE_SPACE_ONLY", "UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE")
ORIENTATION_POLICIES = ("ZERO_ONLY", "TWO_WAY")
NEAR_TIE_MARGIN_UNITS = 1000  # 1 cm at 1000 units/cm; diagnostic only, never productive.
ORIENTATION_SCHEDULER = "STRATIFIED"
DEFAULT_ORIENTATION_TRANCHE_SIZE = 500


def _stratified_interleave(per_rotation: dict, tranche_size: int) -> list:
    """Round-robin tranches across orientations (Phase 2F.7G-8.1, Section 7).

    Deterministic, no randomness: rotations are visited in sorted order each
    round, taking up to ``tranche_size`` still-unconsumed items per rotation,
    until every rotation's list is exhausted. This guarantees any orientation
    with candidates gets representation within any budget prefix >= one
    tranche, instead of one orientation's full block silently exhausting the
    whole budget before a second orientation is ever reached.
    """
    cursors = {rotation: 0 for rotation in per_rotation}
    result = []
    progressed = True
    while progressed:
        progressed = False
        for rotation in sorted(per_rotation):
            items = per_rotation[rotation]
            start = cursors[rotation]
            end = min(start + tranche_size, len(items))
            if start < end:
                result.extend(items[start:end])
                cursors[rotation] = end
                progressed = True
    return result


def _placement(instance, position, rotation, sequence):
    polygon = transform_piece(instance.piece.cut_polygon, rotation, position)
    return Placement(instance.instance_id, instance.piece.pattern_piece_id, instance.piece.size_code,
        instance.piece.piece_code, rotation, False, position, polygon,
        tuple(rotate_and_translate_point(point, rotation, instance.piece.cut_polygon, position) for point in instance.piece.grainline),
        path_bbox(polygon), instance.piece.geometry_hash, sequence)


class DeterministicCompletionRunner:
    """Continue from an immutable validated prefix, committing one piece at once."""
    def __init__(self, request: MarkerRequest, frozen_prefix: tuple[Placement, ...], output_dir: Path,
                 *, piece_order: tuple[str, ...] | None = None, candidate_budget: int = 5000, recovery_budget: int = 65,
                 piece_time_budget_ms: int = 60_000, candidate_mode: str = "CANDIDATE_SPACE_ONLY",
                 orientation_policy: str = "ZERO_ONLY", depth2_diagnostics: bool = False, heartbeat=None,
                 orientation_tranche_size: int = DEFAULT_ORIENTATION_TRANCHE_SIZE):
        if candidate_mode not in CANDIDATE_MODES:
            raise ValueError(f"Unsupported candidate_mode: {candidate_mode}")
        if orientation_policy not in ORIENTATION_POLICIES:
            raise ValueError(f"Unsupported orientation_policy: {orientation_policy}")
        self.request, self.placements, self.output_dir = request, list(frozen_prefix), output_dir
        self.candidate_budget, self.recovery_budget, self.piece_time_budget_ms = candidate_budget, recovery_budget, piece_time_budget_ms
        self.candidate_mode, self.orientation_policy, self.depth2_diagnostics = candidate_mode, orientation_policy, depth2_diagnostics
        self.heartbeat = heartbeat
        self.orientation_tranche_size = orientation_tranche_size
        self.kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())
        self.validator = IndependentMarkerValidator()
        self._oriented_geometry: dict[tuple[str, int], tuple] = {}
        self._oriented_grainline: dict[tuple[str, int], tuple] = {}
        self.output_dir.joinpath("checkpoints").mkdir(parents=True, exist_ok=True)
        self.by_id = {item.instance_id: item for item in request.piece_instances}
        self.order = piece_order or tuple(item.instance_id for item in request.piece_instances)
        if set(self.order) != set(self.by_id):
            raise ValueError("piece_order must contain every request piece exactly once")
        if tuple(item.piece_instance_id for item in frozen_prefix) != self.order[:len(frozen_prefix)]:
            raise ValueError("frozen prefix does not match the declared deterministic piece order")

    def _hash(self):
        return canonical_json_hash([asdict(item) for item in self.placements])

    def _region(self):
        return (self.request.margins.start, self.request.margins.left,
                self.request.max_length - self.request.margins.end, self.request.margins.left + self.request.usable_width)

    def _effective_rotations(self, instance) -> tuple[int, ...]:
        """The same legal-transform authority the engine and validator use.

        ZERO_ONLY additionally restricts to {0} for the orientation-policy
        experiment; the underlying resolved set (used by TWO_WAY) is identical
        to what ``IndependentMarkerValidator`` will accept, so nothing here can
        propose an orientation the validator would later reject as illegal.
        """
        resolver = EffectiveTransformResolver(
            self.request.fabric_directionality, self.request.marker_direction_policy,
            self.request.lay_face_mode, self.request.transform_lab_mode,
        )
        legal = resolver.resolve(
            tuple(sorted(set(instance.piece.allowed_rotations).intersection(self.request.allowed_transforms))),
            instance.piece.grainline_policy,
        )
        if self.orientation_policy == "ZERO_ONLY":
            return tuple(rotation for rotation in legal if rotation == 0) or (0,)
        return legal

    def _partial_request(self, placements, next_instance=None):
        ids = [item.piece_instance_id for item in placements]
        if next_instance: ids.append(next_instance.instance_id)
        return replace(self.request, piece_instances=tuple(self.by_id[item_id] for item_id in ids))

    def _cached_placement(self, instance, candidate: CandidatePosition, sequence: int) -> Placement:
        """Translate precomputed orientation geometry; never rotate in the hot loop."""
        key = (instance.instance_id, candidate.orientation)
        local = self._oriented_geometry.get(key)
        grainline = self._oriented_grainline.get(key)
        if local is None:
            local = transform_and_normalize(instance.piece.cut_polygon, candidate.orientation)
            grainline = tuple(rotate_and_translate_point(point, candidate.orientation,
                                                          instance.piece.cut_polygon, (0, 0))
                              for point in instance.piece.grainline)
            self._oriented_geometry[key] = local
            self._oriented_grainline[key] = grainline
        polygon = translate_path(local, *candidate.position)
        translated_grainline = tuple((x + candidate.position[0], y + candidate.position[1]) for x, y in grainline)
        return Placement(instance.instance_id, instance.piece.pattern_piece_id, instance.piece.size_code,
                         instance.piece.piece_code, candidate.orientation, False, candidate.position, polygon,
                         translated_grainline, path_bbox(polygon), instance.piece.geometry_hash, sequence)

    def _checkpoint(self, last: Placement | None, source: tuple[str, ...] = (), candidate_set_hash: str = ""):
        count = len(self.placements)
        next_id = self.order[count] if count < len(self.order) else None
        payload = {
            "placed_piece_count": count, "remaining_piece_count": len(self.order) - count,
            "last_piece_id": last.piece_instance_id if last else None,
            "last_position": list(last.translation) if last else None,
            "last_orientation": last.rotation if last else None, "last_candidate_source": list(source),
            "layout_hash": self._hash(), "candidate_set_hash": candidate_set_hash,
            "current_marker_length": max((item.bbox[2] for item in self.placements), default=0),
            "next_piece_id": next_id,
            "placed_pieces": [asdict(item) for item in self.placements],
            "configuration": {"candidate_budget": self.candidate_budget, "recovery_budget": self.recovery_budget,
                              "piece_time_budget_ms": self.piece_time_budget_ms, "seed": self.request.seed,
                              "candidate_mode": self.candidate_mode, "orientation_policy": self.orientation_policy},
        }
        target = self.output_dir / "checkpoints" / f"piece_{count:02d}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _validate(self, instance, candidate: CandidatePosition, sequence, incremental_validator):
        placement = self._cached_placement(instance, candidate, sequence)
        started = perf_counter()
        result = incremental_validator.validate(instance, placement)
        return (placement if result.accepted else None, result.reason,
                (perf_counter() - started) * 1000, result)

    @staticmethod
    def _source_rank(sources):
        rank = {"NFP_NFP_INTERSECTION": 0, "NFP_IFP_INTERSECTION": 1, "FEASIBLE_BOUNDARY": 2,
                "NFP_VERTEX": 3, "IFP_VERTEX": 4, "FEASIBLE_INTERIOR_RECOVERY": 5, "EXACT_REPAIRED_BOUNDARY": 6,
                "LEGACY_FALLBACK": 7}
        return min(rank.get(source, 99) for source in sources)

    def _choose(self, accepted, placements):
        # Required order: resulting length, validated (all are true), contact,
        # semantic source, x, y.  No hash/set order is used.  A source only
        # breaks ties; it never suppresses a shorter-resulting-length candidate
        # regardless of which pool (legacy or CandidateSpace) proposed it.
        return min(accepted, key=lambda item: (max([item[0].bbox[2], *(p.bbox[2] for p in placements)]),
                                                -item[1].contact_count, self._source_rank(item[1].sources),
                                                item[0].translation[0], item[0].translation[1]))

    def _build_pool(self, instance, placed_geo, region, rotations):
        """Unified candidate pool for one piece across every legal orientation.

        Deduplicates by (x, y, orientation) per Section 6 of the original phase
        spec, merging provenance rather than keeping duplicate rows (dedup keys
        always carry orientation, so (x,y,0) and (x,y,180) never collide -- see
        Phase 2F.7G-8.1 Section 4). CandidateSpace rows are always included;
        LEGACY_FALLBACK rows are added only in UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE
        mode. Orientation-fair scheduling (Phase 2F.7G-8.1): the budget must
        never be able to be exhausted by a single orientation's block before a
        second legal orientation is ever reached, so the per-orientation
        (already deduped, already priority-ordered) lists are stratified into
        round-robin tranches before ``_evaluate_piece`` slices by budget. This
        changes candidate ORDER only, never the candidate SET.
        """
        recovery: list[CandidatePosition] = []
        generation_ms = 0.0
        orientation_generated: dict[int, int] = {}
        per_rotation_candidates: dict[int, list[CandidatePosition]] = {}
        for rotation in sorted(rotations):
            generated_at = perf_counter()
            space = CandidateSpaceEngine().build(
                self.kernel, instance.piece.cut_polygon, instance.piece.geometry_hash,
                rotation, region, self.request.clearance, placed_geo, compute_contact_counts=False,
            )
            boundary = [row for row in space.candidates if CandidateSpaceEngine.is_valid_reference_position(space, row.position)]
            legacy_rows: tuple[CandidatePosition, ...] = ()
            if self.candidate_mode == "UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE":
                legacy_rows = legacy_fallback_candidates(
                    self.kernel, instance.piece.cut_polygon, rotation, placed_geo, self.request.clearance, region,
                    compute_contact_counts=False,
                )
            generation_ms += (perf_counter() - generated_at) * 1000
            level0 = [row for row in boundary if set(row.sources) <= {"IFP_VERTEX", "NFP_VERTEX"}]
            level1 = [row for row in boundary if row not in level0]
            rotation_rows = level0 + level1 + list(legacy_rows)
            orientation_generated[rotation] = len(rotation_rows)
            # Dedup is scoped to this rotation: the key always includes
            # orientation, so a (x, y, 0) row can never collide with (x, y, 180).
            merged_sources: dict[tuple[int, int], set[str]] = {}
            merged_contacts: dict[tuple[int, int], int] = {}
            ordered_keys: list[tuple[int, int]] = []
            for row in rotation_rows:
                key = row.position
                if key not in merged_sources:
                    merged_sources[key] = set()
                    merged_contacts[key] = row.contact_count
                    ordered_keys.append(key)
                merged_sources[key].update(row.sources)
                merged_contacts[key] = max(merged_contacts[key], row.contact_count)
            per_rotation_candidates[rotation] = [
                CandidatePosition(key, rotation, tuple(sorted(merged_sources[key])), merged_contacts[key])
                for key in ordered_keys
            ]
            recovery.extend(CandidateSpaceEngine.interior_candidates(space, rotation)[:self.recovery_budget])
        raw_all = _stratified_interleave(per_rotation_candidates, self.orientation_tranche_size)
        return raw_all, tuple(recovery), generation_ms, orientation_generated

    def _evaluate_piece(self, instance, placements, sequence, rotations):
        started = perf_counter()
        placed_geo = tuple(
            PlacedGeometry(translate_path(item.transformed_polygon, -item.translation[0], -item.translation[1]),
                            item.geometry_hash, item.rotation, item.translation)
            for item in placements
        )
        region = self._region()
        raw_all, recovery, generation_ms, orientation_generated = self._build_pool(instance, placed_geo, region, rotations)
        raw = raw_all[: self.candidate_budget]
        candidate_hash = canonical_json_hash([(row.position, row.orientation, row.sources) for row in raw_all + list(recovery)])
        exact_started = perf_counter()
        accepted: list[tuple[Placement, CandidatePosition]] = []
        reasons: dict[str, int] = {}
        evaluated = 0
        validator_elapsed = 0.0
        evaluated_by_orientation: dict[int, int] = {}
        validated_by_orientation: dict[int, int] = {}
        incremental_validator = IncrementalCandidateValidator(self.request, tuple(placements))
        validation_counts = {"bbox_rejected": 0, "exact_overlap_rejected": 0, "clearance_rejected": 0,
                             "containment_rejected": 0, "exact_precheck_accepted": 0,
                             "full_validator_calls": 0, "full_validator_accepted": 0}

        last_heartbeat = [started]

        def evaluate(candidates, budget):
            nonlocal evaluated, validator_elapsed
            for candidate in candidates:
                if evaluated >= budget or (perf_counter() - started) * 1000 > self.piece_time_budget_ms:
                    break
                evaluated += 1
                evaluated_by_orientation[candidate.orientation] = evaluated_by_orientation.get(candidate.orientation, 0) + 1
                placement, reason, validator_ms, validation = self._validate(instance, candidate, sequence, incremental_validator)
                validator_elapsed += validator_ms
                validation_counts["bbox_rejected"] += validation.bbox_rejected
                validation_counts["exact_overlap_rejected"] += validation.exact_overlap_rejected
                validation_counts["clearance_rejected"] += validation.clearance_rejected
                validation_counts["containment_rejected"] += validation.containment_rejected
                if placement:
                    validation_counts["exact_precheck_accepted"] += 1
                    validated_by_orientation[candidate.orientation] = validated_by_orientation.get(candidate.orientation, 0) + 1
                    accepted.append((placement, candidate))
                else:
                    reasons[reason] = reasons.get(reason, 0) + 1
                # Batched heartbeat only (Section 33-34, Phase 2F.7G-8P2): every
                # 500 candidates or 30s, whichever first; aggregate counters
                # only, never per-candidate output, so this cannot become a
                # new bottleneck or perturb the hot path's timing budget checks.
                now = perf_counter()
                if self.heartbeat is not None and (evaluated % 500 == 0 or now - last_heartbeat[0] >= 30):
                    last_heartbeat[0] = now
                    best_length = min((max([p.bbox[2], *(item.bbox[2] for item in placements)]) for p, _ in accepted), default=None)
                    self.heartbeat({
                        "piece": instance.instance_id, "orientation": candidate.orientation,
                        "processed": evaluated, "total": len(raw_all), "valid": len(accepted),
                        "best_length": best_length, "elapsed_s": round(now - started, 1),
                        "nfp_cache_hit_rate_pct": round(100 * self.kernel.cache.hits / max(1, self.kernel.cache.hits + self.kernel.cache.misses), 1),
                    })

        evaluate(raw, self.candidate_budget)
        budget_expansions = 0
        while not accepted and evaluated < len(raw_all):
            next_budget = min(len(raw_all), max(self.candidate_budget, evaluated * 2))
            evaluate(raw_all[evaluated:next_budget], next_budget)
            budget_expansions += 1
        exact_ms = (perf_counter() - exact_started) * 1000
        recovery_started = perf_counter()
        if not accepted:
            evaluated = 0
            evaluate(recovery, self.recovery_budget)
        recovery_ms = (perf_counter() - recovery_started) * 1000
        repairs = 0
        if not accepted:
            for candidate in raw:
                # Repair eligibility genuinely needs the real contact count
                # (>=2 disables single-contact repair); this path is a rare
                # last resort, so computing it here (rather than eagerly for
                # every raw candidate) is still the lazy-contact-count policy.
                real_contacts = CandidateSpaceEngine.real_contact_count(
                    instance.piece.cut_polygon, candidate.orientation, candidate.position, placed_geo, self.request.clearance,
                )
                repaired = repair_candidate_to_clearance(
                    instance.piece.cut_polygon, candidate.orientation, candidate.position, placed_geo,
                    self.request.clearance, self.request.clearance, real_contacts,
                )
                repairs += repaired.attempts
                if repaired.position:
                    evaluate([CandidatePosition(repaired.position, candidate.orientation, ("EXACT_REPAIRED_BOUNDARY",), max(1, real_contacts))], self.recovery_budget)
                if accepted:
                    break
        # Lazy contact count (Phase 2F.7G-8P, Section 22): real_contact_count
        # was profiled as ~80% of microbenchmark wall time when computed for
        # every raw candidate. It is only ever used as a validated-candidate
        # tie-break, so it is backfilled here for the much smaller accepted
        # set instead (dozens-to-low-hundreds vs. tens of thousands raw).
        if accepted:
            accepted = [
                (placement, CandidatePosition(
                    candidate.position, candidate.orientation, candidate.sources,
                    max(1, CandidateSpaceEngine.real_contact_count(
                        instance.piece.cut_polygon, candidate.orientation, candidate.position, placed_geo, self.request.clearance,
                    )),
                ))
                for placement, candidate in accepted
            ]
        return {
            "raw_all": raw_all, "recovery": recovery, "accepted": accepted, "reasons": reasons,
            "evaluated": evaluated, "generation_ms": generation_ms, "exact_ms": exact_ms,
            "recovery_ms": recovery_ms, "budget_expansions": budget_expansions, "repairs": repairs,
            "candidate_hash": candidate_hash, "orientation_generated": orientation_generated,
            "evaluated_by_orientation": evaluated_by_orientation, "validated_by_orientation": validated_by_orientation,
            "validator_elapsed": validator_elapsed, "validation_counts": validation_counts,
            "total_ms": (perf_counter() - started) * 1000,
        }

    @staticmethod
    def _is_legacy_only(candidate: CandidatePosition) -> bool:
        return set(candidate.sources) <= {"LEGACY_FALLBACK"}

    def _local_comparison(self, instance, placements, accepted):
        """Best-legacy vs best-nonlegacy at this decision point (Sections 20-22)."""
        def resulting_length(pair):
            placement, _ = pair
            return max([placement.bbox[2], *(item.bbox[2] for item in placements)])

        legacy_only = [pair for pair in accepted if self._is_legacy_only(pair[1])]
        nonlegacy = [pair for pair in accepted if not self._is_legacy_only(pair[1])]
        best_legacy = min(legacy_only, key=resulting_length) if legacy_only else None
        best_nonlegacy = min(nonlegacy, key=resulting_length) if nonlegacy else None
        row = {
            "piece": instance.instance_id, "legacy_candidate_count": len(legacy_only),
            "nonlegacy_candidate_count": len(nonlegacy), "improvement": False, "tie": False, "near_tie": False,
            "best_legacy_position": list(best_legacy[0].translation) if best_legacy else None,
            "best_legacy_orientation": best_legacy[0].rotation if best_legacy else None,
            "best_legacy_length": resulting_length(best_legacy) if best_legacy else None,
            "best_nonlegacy_position": list(best_nonlegacy[0].translation) if best_nonlegacy else None,
            "best_nonlegacy_orientation": best_nonlegacy[0].rotation if best_nonlegacy else None,
            "best_nonlegacy_length": resulting_length(best_nonlegacy) if best_nonlegacy else None,
            "best_nonlegacy_source": list(best_nonlegacy[1].sources) if best_nonlegacy else None,
            "best_nonlegacy_contact_count": best_nonlegacy[1].contact_count if best_nonlegacy else None,
        }
        if best_legacy and best_nonlegacy:
            legacy_len, nonlegacy_len = resulting_length(best_legacy), resulting_length(best_nonlegacy)
            row["improvement"] = nonlegacy_len < legacy_len
            row["tie"] = nonlegacy_len == legacy_len and best_nonlegacy[0].translation != best_legacy[0].translation
            row["near_tie"] = nonlegacy_len <= legacy_len + NEAR_TIE_MARGIN_UNITS
        return row, best_legacy, best_nonlegacy

    def _depth2_branch(self, sequence, base_placements, instance, chosen_candidate):
        """Place ``instance`` at ``chosen_candidate`` then greedily place the next piece.

        Returns the two-piece resulting length, or None if either step fails or
        there is no next piece to look ahead into.
        """
        if sequence >= len(self.order):
            return None
        placement, reason, _, _ = self._validate(instance, chosen_candidate, sequence,
                                                  IncrementalCandidateValidator(self.request, tuple(base_placements)))
        if placement is None:
            return None
        branch_placements = base_placements + [placement]
        next_instance = self.by_id[self.order[sequence]]
        next_rotations = self._effective_rotations(next_instance)
        result = self._evaluate_piece(next_instance, branch_placements, sequence + 1, next_rotations)
        if not result["accepted"]:
            return None
        best = min(result["accepted"], key=lambda pair: max([pair[0].bbox[2], *(item.bbox[2] for item in branch_placements)]))
        return max([best[0].bbox[2], *(item.bbox[2] for item in branch_placements)])

    def run(self):
        initial = self._checkpoint(self.placements[-1] if self.placements else None)
        (self.output_dir / "post_m_sleeve_002_checkpoint.json").write_text(json.dumps(initial, indent=2), encoding="utf-8")
        rows, marker_generated = [], 0
        marker_accepted = marker_scored = marker_chosen = recovery_generated = recovery_chosen = 0
        local_comparisons: list[dict] = []
        depth2_rows: list[dict] = []
        orientation_totals = {0: {"generated": 0, "evaluated": 0, "validator_accepted": 0, "chosen": 0},
                              180: {"generated": 0, "evaluated": 0, "validator_accepted": 0, "chosen": 0}}
        active_blocker = None
        for sequence in range(len(self.placements) + 1, len(self.order) + 1):
            instance = self.by_id[self.order[sequence - 1]]
            rotations = self._effective_rotations(instance)
            result = self._evaluate_piece(instance, self.placements, sequence, rotations)
            raw_all, recovery, accepted = result["raw_all"], result["recovery"], result["accepted"]
            recovery_generated += len(recovery)
            marker_generated += len(raw_all) + len(recovery)
            for rotation, count in result["orientation_generated"].items():
                orientation_totals.setdefault(rotation, {"generated": 0, "evaluated": 0, "validator_accepted": 0, "chosen": 0})
                orientation_totals[rotation]["generated"] += count
            for _, candidate in accepted:
                orientation_totals[candidate.orientation]["validator_accepted"] += 1
            for rotation, count in result["evaluated_by_orientation"].items():
                orientation_totals.setdefault(rotation, {"generated": 0, "evaluated": 0, "validator_accepted": 0, "chosen": 0})
                orientation_totals[rotation]["evaluated"] += count

            local_row = None
            best_legacy = best_nonlegacy = None
            if self.candidate_mode == "UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE" and accepted:
                local_row, best_legacy, best_nonlegacy = self._local_comparison(instance, self.placements, accepted)
                local_comparisons.append(local_row)
                if self.depth2_diagnostics and (local_row["tie"] or local_row["near_tie"]) and best_legacy and best_nonlegacy:
                    length_a = self._depth2_branch(sequence, list(self.placements), instance, best_legacy[1])
                    length_b = self._depth2_branch(sequence, list(self.placements), instance, best_nonlegacy[1])
                    if length_a is not None and length_b is not None:
                        depth2_rows.append({
                            "piece": instance.instance_id, "piece_index": sequence,
                            "choice_a": "LEGACY", "choice_a_two_piece_length": length_a,
                            "choice_b": "NONLEGACY", "choice_b_two_piece_length": length_b,
                            "improvement": length_b < length_a,
                            "gain_units": (length_a - length_b) if length_b < length_a else 0,
                        })

            scoring_started = perf_counter()
            chosen = self._choose(accepted, self.placements) if accepted else None
            scoring_ms = (perf_counter() - scoring_started) * 1000
            timed_out = result["total_ms"] > self.piece_time_budget_ms
            metric = {"piece": instance.instance_id, "piece_index": sequence, "raw_candidates": len(raw_all),
                      "candidate_set_total": len(raw_all) + len(recovery), "validated_candidates": len(accepted),
                      "scored_candidates": len(accepted), "interior_recovery_candidates": len(recovery), "repair_attempts": result["repairs"],
                      "piece_candidate_budget": self.candidate_budget, "piece_recovery_budget": self.recovery_budget, "piece_time_budget_ms": self.piece_time_budget_ms,
                      "candidate_budget_initial": self.candidate_budget, "candidate_budget_final": result["evaluated"], "candidate_set_exhausted": result["evaluated"] >= len(raw_all),
                      "budget_expansions": result["budget_expansions"], "candidate_generation_ms": round(result["generation_ms"], 3),
                      "exact_precheck_ms": round(result["exact_ms"], 3), "interior_recovery_ms": round(result["recovery_ms"], 3),
                      "validator_ms": round(result["validator_elapsed"], 3), "scoring_ms": round(scoring_ms, 3), "total_piece_ms": round(result["total_ms"], 3),
                      "candidate_set_hash": result["candidate_hash"], "pipeline_stage": "commit" if chosen else "blocked", "run_timeout": timed_out,
                      "candidate_mode": self.candidate_mode, "orientation_policy": self.orientation_policy, "rotations_evaluated": list(rotations),
                      "rotation_180_candidates_generated": result["orientation_generated"].get(180, 0),
                      "local_comparison": local_row}
            if not chosen:
                active_blocker = instance.instance_id
                metric.update({"chosen_source": None, "marker_length_after_placement": None,
                               "blocker": {"exact_precheck_accepted": len(accepted), "validator_accepted": len(accepted),
                                           "candidate_budget_exhausted": result["evaluated"] >= self.candidate_budget}})
                rows.append(metric)
                (self.output_dir / "piece-timing.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
                (self.output_dir / "piece-candidate-metrics.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
                break
            placement, candidate = chosen
            # The incremental check is the hot-path filter; the independent
            # full-marker validator remains the authority at every commit.
            commit_report = self.validator.validate(
                self._partial_request(self.placements, instance), tuple(self.placements + [placement]), self.request.max_length
            )
            result["validation_counts"]["full_validator_calls"] += 1
            if commit_report.status != "VALIDATED":
                raise RuntimeError(f"INCREMENTAL_FULL_VALIDATOR_DIVERGENCE:{instance.instance_id}:{commit_report.errors}")
            result["validation_counts"]["full_validator_accepted"] += 1
            self.placements.append(placement)
            source = candidate.sources
            recovery_chosen += int("FEASIBLE_INTERIOR_RECOVERY" in source)
            marker_accepted += len(accepted); marker_scored += len(accepted); marker_chosen += 1
            orientation_totals[candidate.orientation]["chosen"] += 1
            checkpoint = self._checkpoint(placement, source, result["candidate_hash"])
            metric.update({
                "chosen_source": list(source), "chosen_position": list(placement.translation),
                "chosen_orientation": placement.rotation,
                "chosen_candidate_global_rank": next((index + 1 for index, row in enumerate(raw_all + list(recovery))
                                                       if row.position == candidate.position and row.orientation == candidate.orientation), None),
                "marker_length_after_placement": checkpoint["current_marker_length"],
                "placed_piece_count_after": len(self.placements), "remaining_piece_count_after": len(self.order) - len(self.placements),
            })
            rows.append(metric)
            (self.output_dir / "piece-timing.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
            (self.output_dir / "piece-candidate-metrics.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        complete = len(self.placements) == len(self.order) and active_blocker is None
        full_validation = self.validator.validate(self.request, tuple(self.placements), self.request.max_length) if complete else None
        complete = complete and full_validation.status == "VALIDATED"
        summary = {"outcome": "MARKER_COMPLETE" if complete else "MARKER_BLOCKED", "completion_runner_status": "PASS", "run_timeout": False,
                   "candidate_mode": self.candidate_mode, "orientation_policy": self.orientation_policy,
                   "last_completed_piece": self.placements[-1].piece_instance_id if self.placements else None, "active_blocking_piece": active_blocker,
                   "historical_blocking_pieces": ["M_SLEEVE_002"], "pieces_expected": len(self.order), "pieces_placed": len(self.placements), "pieces_remaining": len(self.order) - len(self.placements),
                   "candidate_space_only_completes_marker": complete, "legacy_fallback_used": self.candidate_mode == "UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE",
                   "validator_status": full_validation.status if full_validation else "PASS",
                   "marker_new_candidates_generated": marker_generated, "marker_new_candidates_validator_accepted": marker_accepted, "marker_new_candidates_scored": marker_scored, "marker_new_candidates_chosen": marker_chosen,
                   "interior_recovery_candidates_generated": recovery_generated, "interior_recovery_candidates_chosen": recovery_chosen, "piece_metrics": rows,
                   "local_comparisons": local_comparisons, "depth2_diagnostics": depth2_rows, "orientation_totals": orientation_totals,
                   "layout_hash": self._hash(), "final_validation": asdict(full_validation) if full_validation else None}
        (self.output_dir / "piece-timing.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        (self.output_dir / "piece-candidate-metrics.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        (self.output_dir / "completion-run.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (self.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if not complete:
            # These gates are intentionally not failures: their prerequisite is
            # a complete, independently validated 0° marker.
            (self.output_dir / "determinism.json").write_text(json.dumps({"executed": False, "status": "NOT_RUN", "reason": "MARKER_NOT_COMPLETE"}, indent=2), encoding="utf-8")
            (self.output_dir / "docker-equivalence.json").write_text(json.dumps({"executed": False, "status": "NOT_RUN", "reason": "HOST_MARKER_NOT_COMPLETE"}, indent=2), encoding="utf-8")
        return summary
