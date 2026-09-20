from __future__ import annotations

from dataclasses import asdict
from math import ceil
from random import Random
from time import perf_counter
from typing import Iterable

import pyclipper

from costura_optima.domain.integer_kernel import (
    GeometryOperationCache,
    IntegerGeometryKernel,
    canonical_json_hash,
    close_path,
    path_bbox,
    rotate_and_translate_point,
    transform_piece,
    transform_and_normalize,
    translate_path,
)
from costura_optima.domain.marker_validator import IndependentMarkerValidator
from costura_optima.domain.candidate_space import CandidateSpaceEngine, PlacedGeometry
from costura_optima.domain.nesting_models import (
    IntPath,
    MarkerRequest,
    MarkerResult,
    PieceInstance,
    Placement,
    ValidationReport,
)
from costura_optima.domain.transform_policy import EffectiveTransformResolver


ALGORITHM = "DETERMINISTIC_IRREGULAR_MULTI_START_V2"
ALGORITHM_VERSION = "blf-multistart-contact-fill-v2"
NFP_STATUS = "PARTIAL"
CANDIDATE_ORDER = "MIN_X_THEN_MIN_Y_THEN_ROTATION_THEN_INTEGER_COORDINATES"


def _request_payload(request: MarkerRequest) -> dict:
    return {
        "request_id": request.request_id,
        "pieces": [
            {
                "instance_id": instance.instance_id,
                "pattern_piece_id": instance.piece.pattern_piece_id,
                "geometry_hash": instance.piece.geometry_hash,
                "polygon": close_path(instance.piece.cut_polygon),
                "rotations": instance.piece.allowed_rotations,
                "mirror": instance.piece.mirror_allowed,
            }
            for instance in request.piece_instances
        ],
        "usable_width": request.usable_width,
        "physical_width": request.physical_width,
        "max_length": request.max_length,
        "clearance": request.clearance,
        "margins": asdict(request.margins),
        "allowed_transforms": request.allowed_transforms,
        "deterministic": request.deterministic,
        "seed": request.seed,
        "evaluation_budget": request.evaluation_budget,
        "precision": asdict(request.precision),
        "algorithm_version": ALGORITHM_VERSION,
    }


def _significant_vertices(path: IntPath, target: int = 6) -> tuple[tuple[int, int], ...]:
    if len(path) <= target:
        return path
    min_x, min_y, max_x, max_y = path_bbox(path)
    selected = {min(path), max(path)}
    selected.update(
        {
            min(path, key=lambda point: (point[0], point[1])),
            max(path, key=lambda point: (point[0], -point[1])),
            min(path, key=lambda point: (point[1], point[0])),
            max(path, key=lambda point: (point[1], -point[0])),
        }
    )
    step = max(1, len(path) // target)
    selected.update(path[::step])
    return tuple(sorted(selected))


class DeterministicNestingEngine:
    def __init__(self, cache: GeometryOperationCache | None = None, candidate_mode: str = "LEGACY"):
        self.cache = cache or GeometryOperationCache()
        aliases = {"LEGACY_ONLY": "LEGACY", "CANDIDATE_SPACE_ONLY": "CANDIDATE_SPACE",
                   "CANDIDATE_SPACE_PLUS_LEGACY": "CANDIDATE_SPACE_PLUS_LEGACY"}
        candidate_mode = aliases.get(candidate_mode, candidate_mode)
        if candidate_mode not in {"LEGACY", "NFP_VERTEX_ONLY", "CANDIDATE_SPACE", "CANDIDATE_SPACE_PLUS_LEGACY"}:
            raise ValueError(f"Unsupported candidate mode: {candidate_mode}")
        self.candidate_mode = candidate_mode
        self._candidate_sources: dict[tuple[int, int], tuple[str, ...]] = {}
        self._candidate_space_state: dict[str, object] = {}

    def nest(self, request: MarkerRequest) -> MarkerResult:
        started = perf_counter()
        input_hash = canonical_json_hash(_request_payload(request))
        diagnostics = self._preflight(request)
        resolver = self._resolver(request)
        paths_and_rotations = [
            (
                instance.piece.cut_polygon,
                resolver.resolve(tuple(sorted(set(instance.piece.allowed_rotations).intersection(request.allowed_transforms))), instance.piece.grainline_policy),
            )
            for instance in request.piece_instances
        ]
        area_bound, extent_bound, lower_bound = IntegerGeometryKernel.lower_bounds(
            paths_and_rotations, request.usable_width, request.margins.start + request.margins.end
        ) if request.piece_instances and all(rotations for _, rotations in paths_and_rotations) else (0, 0, 0)
        if diagnostics:
            return self._empty_result(request, input_hash, diagnostics, lower_bound, area_bound, started)

        kernel = IntegerGeometryKernel(request.precision, self.cache)
        evaluation = [0]
        debug_data = {"candidate_positions": [], "rejected_placements": [], "placement_sequence": [], "orientation_audit": {},
                      "placement_strategy": self.candidate_mode, "legacy_fallback_enabled": self.candidate_mode == "CANDIDATE_SPACE_PLUS_LEGACY",
                      "candidate_trace": []} if request.debug else None
        sequences = self._initial_sequences(request.piece_instances, kernel, request.seed)
        # The adoption diagnostic deliberately holds one piece order fixed: it
        # measures candidate generation/selection, not multi-start variation.
        if self.candidate_mode == "CANDIDATE_SPACE":
            sequences = sequences[:1]
        conservative_order = sequences[0][1]
        conservative, conservative_exhausted = (None, False) if self.candidate_mode == "CANDIDATE_SPACE" else self._pack_extrema_frontier(
            request, conservative_order, kernel, evaluation, debug_data
        )
        best: tuple[tuple[Placement, ...], str] | None = (
            (conservative, "CONSERVATIVE_EXTREMA_FRONTIER") if conservative else None
        )
        budget_exhausted = conservative_exhausted
        for name, order, reverse_rotations in sequences:
            packed, exhausted = self._pack(request, order, kernel, evaluation, reverse_rotations, debug_data)
            budget_exhausted |= exhausted
            if packed and (best is None or self._layout_key(packed) < self._layout_key(best[0])):
                best = packed, name
            if exhausted:
                break

        if best and not budget_exhausted and self.candidate_mode != "CANDIDATE_SPACE":
            for name, order, reverse_rotations in self._local_variants(best[0], request.piece_instances):
                packed, exhausted = self._pack(request, order, kernel, evaluation, reverse_rotations, debug_data)
                budget_exhausted |= exhausted
                if packed and self._layout_key(packed) < self._layout_key(best[0]):
                    best = packed, name
                if exhausted:
                    break

        if best is None:
            # A finite heuristic search is not a proof that arbitrary irregular
            # polygons cannot fit.  Only preflight capacity/containment facts use
            # PROVEN_INFEASIBLE; this branch preserves the distinction for callers.
            status = "NOT_FOUND_WITHIN_BUDGET" if budget_exhausted else "SEARCH_EXHAUSTED"
            reason = "evaluation_budget_exhausted" if budget_exhausted else "all_configured_starts_exhausted"
            return self._empty_result(request, input_hash, (reason,), lower_bound, area_bound, started, status, evaluation[0], debug_data)

        placements, strategy = best
        marker_length = max(placement.bbox[2] for placement in placements) + request.margins.end
        validator = IndependentMarkerValidator()
        validation = validator.validate(request, placements, marker_length)
        if validation.status != "VALIDATED":
            return self._invalid_result(
                request, input_hash, placements, validation, lower_bound, area_bound, evaluation[0], started, strategy
            )

        piece_area = sum(kernel.area_units2(instance.piece.cut_polygon) for instance in request.piece_instances)
        marker_area = request.usable_width * marker_length
        waste_area = marker_area - piece_area
        efficiency = round(piece_area / marker_area * 100, 6) if marker_area else 0.0
        stopping_reason = "evaluation_budget_exhausted_with_valid_layout" if budget_exhausted else "deterministic_neighborhoods_completed"
        payload = {
            "status": "VALIDATED_FEASIBLE",
            "marker_length": marker_length,
            "placements": [self._placement_payload(item) for item in placements],
            "piece_area_total": piece_area,
            "marker_area": marker_area,
            "validation": asdict(validation),
            "algorithm": ALGORITHM,
            "algorithm_version": ALGORITHM_VERSION,
            "seed": request.seed,
            "input_hash": input_hash,
            "evaluation_count": evaluation[0],
            "stopping_reason": stopping_reason,
        }
        return MarkerResult(
            status="VALIDATED_FEASIBLE",
            search_status="FEASIBLE_NOT_PROVEN_BEST",
            marker_length=marker_length,
            usable_width=request.usable_width,
            physical_width=request.physical_width,
            placements=placements,
            piece_area_total=piece_area,
            marker_area=marker_area,
            waste_area=waste_area,
            efficiency_percentage=efficiency,
            waste_percentage=round(100 - efficiency, 6),
            lower_bound_length=lower_bound,
            area_lower_bound=area_bound,
            gap_to_area_lower_bound=marker_length - area_bound,
            validation=validation,
            algorithm=ALGORITHM,
            algorithm_version=ALGORITHM_VERSION,
            nfp_status=NFP_STATUS,
            seed=request.seed,
            piece_order_strategy=strategy,
            candidate_order=CANDIDATE_ORDER,
            transform_order=tuple(sorted(request.allowed_transforms)),
            evaluation_count=evaluation[0],
            stopping_reason=stopping_reason,
            elapsed_time_ms=round((perf_counter() - started) * 1000, 3),
            input_hash=input_hash,
            result_hash=canonical_json_hash(payload),
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            diagnostics=("NFP unavailable: exact polygon collision fallback active.",),
            debug_geometry=debug_data,
        )

    def _preflight(self, request: MarkerRequest) -> tuple[str, ...]:
        issues: list[str] = []
        if not request.piece_instances:
            issues.append("composition_has_no_piece_instances")
        if request.usable_width <= 0 or request.max_length <= 0:
            issues.append("non_positive_nesting_region")
        if request.margins.left + request.usable_width + request.margins.right > request.physical_width:
            issues.append("margins_and_usable_width_exceed_physical_width")
        if request.margins.start + request.margins.end >= request.max_length:
            issues.append("margins_leave_no_usable_length")
        kernel = IntegerGeometryKernel(request.precision, self.cache)
        available_length = request.max_length - request.margins.start - request.margins.end
        total_area = 0
        for instance in request.piece_instances:
            rotations = self._resolver(request).resolve(tuple(sorted(set(instance.piece.allowed_rotations).intersection(request.allowed_transforms))), instance.piece.grainline_policy)
            if not rotations:
                issues.append(f"no_allowed_transform:{instance.instance_id}")
                continue
            fits = False
            for rotation in rotations:
                path = transform_and_normalize(instance.piece.cut_polygon, rotation)
                min_x, min_y, max_x, max_y = path_bbox(path)
                fits |= max_y - min_y <= request.usable_width and max_x - min_x <= available_length
            if not fits:
                issues.append(f"piece_exceeds_valid_region:{instance.instance_id}")
            total_area += kernel.area_units2(instance.piece.cut_polygon)
        if total_area > max(0, request.usable_width * available_length):
            issues.append("total_piece_area_exceeds_marker_capacity")
        return tuple(sorted(set(issues)))

    def _initial_sequences(self, instances: tuple[PieceInstance, ...], kernel: IntegerGeometryKernel, seed: int):
        def dimensions(instance: PieceInstance) -> tuple[int, int]:
            min_x, min_y, max_x, max_y = path_bbox(instance.piece.cut_polygon)
            return max_x - min_x, max_y - min_y

        definitions = [
            ("AREA_DESC", lambda item: (-kernel.area_units2(item.piece.cut_polygon), item.instance_id)),
            ("BBOX_AREA_DESC", lambda item: (-(dimensions(item)[0] * dimensions(item)[1]), item.instance_id)),
            ("LENGTH_DESC", lambda item: (-dimensions(item)[0], item.instance_id)),
            ("WIDTH_DESC", lambda item: (-dimensions(item)[1], item.instance_id)),
            ("PERIMETER_DESC", lambda item: (-kernel.perimeter_units(item.piece.cut_polygon), item.instance_id)),
            ("TYPE_SIZE_GROUP", lambda item: (item.piece.piece_code, item.piece.size_code, item.instance_id)),
            ("CANONICAL_ID", lambda item: (item.instance_id,)),
            # Bodies establish the structural envelope; sleeves/bands are then
            # deliberately available as hole-fillers rather than being forced by
            # lexical piece-code order.
            ("STRUCTURAL_THEN_FILL", lambda item: (
                0 if item.piece.piece_code in {"FRONT", "BACK"} else 1,
                -kernel.area_units2(item.piece.cut_polygon), item.instance_id,
            )),
        ]
        result = []
        seen = set()
        for name, key in definitions:
            order = tuple(sorted(instances, key=key))
            signature = tuple(item.instance_id for item in order)
            if signature not in seen:
                result.append((name, order, False))
                seen.add(signature)
        # Reproducible multi-start: seed-derived permutations explore mixed sizes
        # and piece types without making scheduling/concurrency affect the winner.
        randomizer = Random(seed)
        for start in range(4):
            shuffled = list(instances)
            randomizer.shuffle(shuffled)
            order = tuple(shuffled)
            signature = tuple(item.instance_id for item in order)
            if signature not in seen:
                result.append((f"SEEDED_MIXED_{start + 1}", order, bool(start % 2)))
                seen.add(signature)
        return result

    def _local_variants(self, placements: tuple[Placement, ...], instances: tuple[PieceInstance, ...]):
        by_id = {item.instance_id: item for item in instances}
        base = [by_id[item.piece_instance_id] for item in placements]
        variants = []
        for index in range(min(4, max(0, len(base) - 1))):
            changed = base.copy(); changed[index], changed[index + 1] = changed[index + 1], changed[index]
            variants.append((f"LOCAL_SWAP_{index}_{index+1}", tuple(changed), False))
        if len(base) > 2:
            inserted = base.copy(); item = inserted.pop(-1); inserted.insert(1, item)
            variants.append(("LOCAL_INSERT_LAST_AT_1", tuple(inserted), False))
            reversed_part = base.copy(); end = min(len(base), 6); reversed_part[1:end] = reversed(reversed_part[1:end])
            variants.append(("LOCAL_REVERSE_SUBSEQUENCE", tuple(reversed_part), False))
            split = len(base) // 2
            repacked = base[:split] + sorted(base[split:], key=lambda item: item.instance_id)
            variants.append(("LOCAL_COMPACT_REPACK_SUFFIX", tuple(repacked), False))
        variants.append(("LOCAL_ORIENTATION_CHANGE", tuple(base), True))
        return variants

    def _pack(self, request, order, kernel, evaluation, reverse_rotations, debug_data):
        placements: list[Placement] = []
        region = (
            request.margins.start,
            request.margins.left,
            request.max_length - request.margins.end,
            request.margins.left + request.usable_width,
        )
        for sequence, instance in enumerate(order, start=1):
            rotations = sorted(self._resolver(request).resolve(tuple(sorted(set(instance.piece.allowed_rotations).intersection(request.allowed_transforms))), instance.piece.grainline_policy), reverse=reverse_rotations)
            if debug_data is not None:
                debug_data["orientation_audit"][instance.instance_id] = {"configured": list(instance.piece.allowed_rotations), "effective": list(rotations), "evaluated": []}
            options = []
            source_rows: list[tuple[int, int, int, tuple[str, ...]]] = []
            for rotation in rotations:
                oriented = transform_and_normalize(instance.piece.cut_polygon, rotation)
                for x, y in self._candidates(
                    oriented, placements, request.clearance, region, kernel,
                    instance.piece.geometry_hash, rotation,
                ):
                    sources = self._candidate_sources.get((x, y), ())
                    options.append((x, y, rotation, oriented, sources))
                    source_rows.append((x, y, rotation, sources))
                if debug_data is not None:
                    debug_data["orientation_audit"][instance.instance_id]["evaluated"].append(rotation)
            options.sort(key=lambda item: (item[0], item[1], item[2]))
            chosen = None
            accepted: list[tuple[tuple, Placement, tuple[str, ...]]] = []
            rejected = 0
            rejection_counts = {"OVERLAP": 0, "CLEARANCE": 0, "CONTAINMENT": 0}
            for x, y, rotation, oriented, sources in options:
                if evaluation[0] >= request.evaluation_budget:
                    return None, True
                evaluation[0] += 1
                translated = transform_piece(instance.piece.cut_polygon, rotation, (x, y))
                candidate_bbox = path_bbox(translated)
                reason = None
                if not kernel.inside_rectangle(translated, region):
                    reason = "CONTAINMENT"
                else:
                    for existing in placements:
                        if kernel.bboxes_may_conflict(candidate_bbox, existing.bbox, request.clearance) and kernel.conflicts(
                            translated, existing.transformed_polygon, request.clearance
                        ):
                            candidate_polygon = kernel.polygon(translated)
                            obstacle_polygon = kernel.polygon(existing.transformed_polygon)
                            distance = candidate_polygon.distance(obstacle_polygon)
                            category = "OVERLAP" if candidate_polygon.intersection(obstacle_polygon).area > 0 else "CLEARANCE"
                            reason = f"{category}:{existing.piece_instance_id}:{distance:.6f}"
                            break
                if debug_data is not None:
                    debug_data["candidate_positions"].append([x, y])
                    if reason:
                        debug_data["rejected_placements"].append({
                            "position": [x, y], "orientation": rotation, "sources": list(sources), "reason": reason,
                        })
                if reason is None:
                    candidate = Placement(
                        piece_instance_id=instance.instance_id,
                        pattern_piece_id=instance.piece.pattern_piece_id,
                        size_code=instance.piece.size_code,
                        piece_code=instance.piece.piece_code,
                        rotation=rotation,
                        mirrored=False,
                        translation=(x, y),
                        transformed_polygon=translated,
                        transformed_grainline=(
                            rotate_and_translate_point(instance.piece.grainline[0], rotation, instance.piece.cut_polygon, (x, y)),
                            rotate_and_translate_point(instance.piece.grainline[1], rotation, instance.piece.cut_polygon, (x, y)),
                        ),
                        bbox=candidate_bbox,
                        geometry_hash=instance.piece.geometry_hash,
                        sequence=sequence,
                    )
                    # A source only controls diagnostic/evaluation order.  It must
                    # never suppress a later non-legacy candidate merely because a
                    # legacy position was valid first.
                    resulting_length = max([candidate_bbox[2], *(item.bbox[2] for item in placements)])
                    # CandidateSpace already independently records real contact
                    # counts.  Here source provenance is only a deterministic
                    # tie-breaker; recomputing Shapely distance for every raw
                    # proposal would turn the diagnostic pipeline quadratic.
                    contact_count = 2 if "NFP_NFP_INTERSECTION" in sources else (1 if any(source.startswith("NFP_") for source in sources) else 0)
                    accepted.append(((resulting_length, -contact_count, x, y, rotation), candidate, sources))
                else:
                    rejected += 1
                    rejection_counts[reason.split(":", 1)[0]] = rejection_counts.get(reason.split(":", 1)[0], 0) + 1
            if accepted:
                _, chosen, chosen_sources = min(accepted, key=lambda item: item[0])
            else:
                chosen_sources = ()
            if debug_data is not None:
                legacy_rows = [row for row in source_rows if "LEGACY_FALLBACK" in row[3]]
                nonlegacy_rows = [row for row in source_rows if any(source != "LEGACY_FALLBACK" for source in row[3])]
                debug_data["candidate_trace"].append({
                    "piece_index": sequence,
                    "piece_instance_id": instance.instance_id,
                    "piece_type": instance.piece.piece_code,
                    "size": instance.piece.size_code,
                    "orientations": list(rotations),
                    "legacy_generated_count": len(legacy_rows),
                    "candidate_space_generated_count": len(nonlegacy_rows),
                    "candidate_space_nonlegacy_count": len(nonlegacy_rows),
                    "deduplicated_count": len(options),
                    "validator_accepted_count": len(accepted),
                    "validator_rejected_count": rejected,
                    "rejection_counts": rejection_counts,
                    "scored_count": len(accepted),
                    "evaluated_count": len(options),
                    "candidate_set_hash_after_piece_i": canonical_json_hash(sorted((x, y, rotation, sources) for x, y, rotation, sources in source_rows)),
                    "chosen_candidate": list(chosen.translation) if chosen else None,
                    "chosen_source": list(chosen_sources),
                    "resulting_length_after_piece": chosen.bbox[2] if chosen else None,
                    "failure": "NO_CANDIDATE_SPACE_POSITION" if chosen is None and self.candidate_mode == "CANDIDATE_SPACE" else None,
                    "candidate_space_state": self._candidate_space_state if chosen is None else None,
                })
            if chosen is None:
                return None, False
            placements.append(chosen)
            if debug_data is not None:
                debug_data["orientation_audit"][instance.instance_id]["selected"] = chosen.rotation
            if debug_data is not None:
                debug_data["placement_sequence"].append(chosen.piece_instance_id)
        return tuple(placements), False

    def _pack_extrema_frontier(self, request, order, kernel, evaluation, debug_data):
        """Build a safe seed from polygon extrema; exact predicates still decide validity.

        This is deliberately conservative and exists so a greedy irregular BLF dead-end
        is not misreported as infeasibility.  The normal BLF sequences then try to improve it.
        """
        placements: list[Placement] = []
        x0 = request.margins.start
        y0 = request.margins.left
        x_limit = request.max_length - request.margins.end
        y_limit = request.margins.left + request.usable_width
        cursor_x, cursor_y, column_right = x0, y0, x0
        for sequence, instance in enumerate(order, start=1):
            choices = []
            for rotation in self._resolver(request).resolve(tuple(sorted(set(instance.piece.allowed_rotations).intersection(request.allowed_transforms))), instance.piece.grainline_policy):
                oriented = transform_and_normalize(instance.piece.cut_polygon, rotation)
                bx0, by0, bx1, by1 = path_bbox(oriented)
                choices.append((by1 - by0, bx1 - bx0, rotation, oriented))
            choices.sort(key=lambda item: (item[0], item[1], item[2]))
            chosen = None
            for height, width, rotation, oriented in choices:
                candidate_x, candidate_y = cursor_x, cursor_y
                if candidate_y + height > y_limit:
                    candidate_x, candidate_y = column_right + request.clearance, y0
                translated = transform_piece(instance.piece.cut_polygon, rotation, (candidate_x, candidate_y))
                candidate_bbox = path_bbox(translated)
                if evaluation[0] >= request.evaluation_budget:
                    return None, True
                evaluation[0] += 1
                collision = any(
                    kernel.bboxes_may_conflict(candidate_bbox, prior.bbox, request.clearance)
                    and kernel.conflicts(translated, prior.transformed_polygon, request.clearance)
                    for prior in placements
                )
                if candidate_bbox[2] <= x_limit and kernel.inside_rectangle(
                    translated, (x0, y0, x_limit, y_limit)
                ) and not collision:
                    chosen = Placement(
                        piece_instance_id=instance.instance_id,
                        pattern_piece_id=instance.piece.pattern_piece_id,
                        size_code=instance.piece.size_code,
                        piece_code=instance.piece.piece_code,
                        rotation=rotation,
                        mirrored=False,
                        translation=(candidate_x, candidate_y),
                        transformed_polygon=translated,
                        transformed_grainline=(
                            rotate_and_translate_point(instance.piece.grainline[0], rotation, instance.piece.cut_polygon, (candidate_x, candidate_y)),
                            rotate_and_translate_point(instance.piece.grainline[1], rotation, instance.piece.cut_polygon, (candidate_x, candidate_y)),
                        ),
                        bbox=candidate_bbox,
                        geometry_hash=instance.piece.geometry_hash,
                        sequence=sequence,
                    )
                    if candidate_x != cursor_x:
                        cursor_x, cursor_y, column_right = candidate_x, y0, candidate_x
                    break
            if chosen is None:
                return None, False
            placements.append(chosen)
            cursor_y = chosen.bbox[3] + request.clearance
            column_right = max(column_right, chosen.bbox[2])
            if debug_data is not None:
                debug_data["placement_sequence"].append(chosen.piece_instance_id)
        return tuple(placements), False

    def _candidates(self, moving: IntPath, placed: list[Placement], clearance: int, region,
                    kernel: IntegerGeometryKernel, moving_hash: str, moving_rotation: int):
        min_x, min_y, max_x, max_y = path_bbox(moving)
        width, height = max_x - min_x, max_y - min_y
        x0, y0, x1, y1 = region
        candidates = {(x0, y0), (x0, y1 - height)} if self.candidate_mode != "CANDIDATE_SPACE" else set()
        sources = {point: {"LEGACY_FALLBACK"} for point in candidates}
        # IFP bounds are the authoritative containment space for the moving
        # reference point.  NFP contours below add contact points from actual
        # polygon Minkowski geometry; legacy extrema remain a safe fallback.
        ifp_x0, ifp_y0, ifp_x1, ifp_y1 = kernel.ifp_bounds(moving, region)
        # Full vertex contacts are inexpensive for the engineering patterns and
        # preserve concavity opportunities.  Large future polygons are bounded
        # deterministically to avoid an unbounded Cartesian product.
        moving_vertices = moving if len(moving) <= 24 else _significant_vertices(moving, 12)
        if self.candidate_mode in {"CANDIDATE_SPACE", "CANDIDATE_SPACE_PLUS_LEGACY"}:
            candidate_space = CandidateSpaceEngine().build(
                kernel, moving, moving_hash, moving_rotation, region, clearance,
                tuple(PlacedGeometry(
                    translate_path(existing.transformed_polygon, -existing.translation[0], -existing.translation[1]),
                    existing.geometry_hash, existing.rotation, existing.translation,
                ) for existing in placed),
            )
            # Raw polygonal proposals remain subject to the exact collision
            # predicate and final independent marker validation in _pack/nest.
            for row in candidate_space.candidates:
                # The engine must not spend its finite candidate budget on raw
                # NFP/IFP boundary vertices that CandidateSpace itself marks as
                # forbidden.  Filtering precedes the deterministic cap below.
                if not CandidateSpaceEngine.is_valid_reference_position(candidate_space, row.position):
                    continue
                candidates.add(row.position)
                sources.setdefault(row.position, set()).update(row.sources)
            # Boundary contacts are the first-class candidate set, but an
            # inner-approximated offset can leave every boundary proposal a
            # fraction inside contractual clearance.  Add bounded,
            # deterministic component probes; exact collision and the final
            # independent validator remain the only authorities.
            for row in CandidateSpaceEngine.interior_candidates(candidate_space, moving_rotation):
                candidates.add(row.position)
                sources.setdefault(row.position, set()).update(row.sources)
            self._candidate_space_state = {
                "ifp": candidate_space.ifp_geometry,
                "forbidden_components": len(candidate_space.forbidden_union),
                "feasible_components": len(candidate_space.feasible_space),
                "source_counts": {
                    source: sum(source in row.sources for row in candidate_space.candidates)
                    for source in ("IFP_VERTEX", "NFP_VERTEX", "NFP_IFP_INTERSECTION", "NFP_NFP_INTERSECTION", "FEASIBLE_BOUNDARY", "FEASIBLE_INTERIOR_RECOVERY")
                },
            }
        # CandidateSpace is the complete topology producer.  In particular it
        # already contains every translated NFP vertex.  Re-adding them below
        # used to destroy its priority ordering and the subsequent 1,200 cap
        # silently dropped NFP/NFP contacts and any future interior recovery.
        # The legacy branch remains deliberately isolated.
        for existing in placed:
            bx0, by0, bx1, by1 = existing.bbox
            if self.candidate_mode in {"LEGACY", "CANDIDATE_SPACE_PLUS_LEGACY"}:
                xs = (x0, bx0, bx1 + clearance, bx0 - width - clearance, bx1 - width)
                ys = (y0, by0, by1 + clearance, by0 - height - clearance, by1 - height)
                candidates.update((x, y) for x in xs for y in ys)
                for x in xs:
                    for y in ys:
                        sources.setdefault((x, y), set()).add("LEGACY_FALLBACK")
                existing_vertices = (existing.transformed_polygon if len(existing.transformed_polygon) <= 24
                                     else _significant_vertices(existing.transformed_polygon, 12))
                offsets = ((0, 0),) if clearance == 0 else ((clearance, 0), (-clearance, 0), (0, clearance), (0, -clearance))
                for px, py in existing_vertices:
                    for mx, my in moving_vertices:
                        for ox, oy in offsets:
                            candidates.add((px - mx + ox, py - my + oy))
                            sources.setdefault((px - mx + ox, py - my + oy), set()).add("LEGACY_FALLBACK")
            if self.candidate_mode in {"LEGACY", "CANDIDATE_SPACE_PLUS_LEGACY", "CANDIDATE_SPACE"}:
                continue
            try:
                # Existing paths are normalized by construction and translated;
                # translate relative NFP contour vertices into marker coordinates.
                base_fixed = translate_path(existing.transformed_polygon, -existing.translation[0], -existing.translation[1])
                for contour in kernel.nfp(
                    base_fixed, moving, existing.geometry_hash, existing.rotation,
                    moving_hash, moving_rotation, clearance,
                ):
                    for px, py in contour:
                        candidates.add((px + existing.translation[0], py + existing.translation[1]))
                        sources.setdefault((px + existing.translation[0], py + existing.translation[1]), set()).add("NFP_VERTEX")
            except (ValueError, pyclipper.ClipperException):
                # Exact collision remains the authorised correctness fallback.
                pass
        valid = [
            (x, y)
            for x, y in candidates
            if ifp_x0 <= x <= ifp_x1 and ifp_y0 <= y <= ifp_y1
        ]
        self._candidate_sources = {point: tuple(sorted(sources.get(point, {"NFP_VERTEX"}))) for point in valid}
        # CandidateSpace rows arrive in semantic source/contact priority.  Keep
        # that order and do not apply the historical X/Y truncation: it was a
        # filtering bug, not a geometry budget.  The caller's evaluation budget
        # remains the explicit, auditable bound.
        if self.candidate_mode == "CANDIDATE_SPACE":
            # The historic bound remains a performance guard, now applied to
            # CandidateSpace's deterministic semantic order rather than to an
            # accidental X/Y ordering of a mixed legacy set.
            boundary = [row.position for row in candidate_space.candidates
                        if row.position in self._candidate_sources][:1200]
            interior = sorted(point for point, labels in self._candidate_sources.items()
                              if "FEASIBLE_INTERIOR_RECOVERY" in labels and point not in set(boundary))
            return boundary + interior
        return sorted(valid, key=lambda item: (item[0], item[1]))[:1200]

    @staticmethod
    def _layout_key(placements: tuple[Placement, ...]):
        length = max(item.bbox[2] for item in placements)
        return length, tuple((item.piece_instance_id, item.rotation, item.translation) for item in placements)

    @staticmethod
    def _placement_payload(placement: Placement) -> dict:
        return {
            "piece_instance_id": placement.piece_instance_id,
            "pattern_piece_id": placement.pattern_piece_id,
            "size_code": placement.size_code,
            "piece_code": placement.piece_code,
            "rotation": placement.rotation,
            "mirrored": placement.mirrored,
            "translation": placement.translation,
            "transformed_polygon": close_path(placement.transformed_polygon),
            "transformed_grainline": placement.transformed_grainline,
            "bbox": placement.bbox,
            "geometry_hash": placement.geometry_hash,
            "sequence": placement.sequence,
        }

    @staticmethod
    def _resolver(request: MarkerRequest) -> EffectiveTransformResolver:
        return EffectiveTransformResolver(
            request.fabric_directionality, request.marker_direction_policy,
            request.lay_face_mode, request.transform_lab_mode,
        )

    def _empty_result(self, request, input_hash, diagnostics, lower_bound, area_bound, started, status="PROVEN_INFEASIBLE", evaluations=0, debug_geometry=None):
        validation = ValidationReport("NOT_RUN", {}, tuple(diagnostics), 0)
        payload = {"status": status, "input_hash": input_hash, "diagnostics": diagnostics, "algorithm_version": ALGORITHM_VERSION}
        return MarkerResult(
            status=status, search_status=status, marker_length=None, usable_width=request.usable_width,
            physical_width=request.physical_width, placements=(), piece_area_total=0, marker_area=None, waste_area=None,
            efficiency_percentage=None, waste_percentage=None, lower_bound_length=lower_bound, area_lower_bound=area_bound,
            gap_to_area_lower_bound=None, validation=validation, algorithm=ALGORITHM, algorithm_version=ALGORITHM_VERSION,
            nfp_status=NFP_STATUS, seed=request.seed, piece_order_strategy="NONE", candidate_order=CANDIDATE_ORDER,
            transform_order=tuple(sorted(request.allowed_transforms)), evaluation_count=evaluations,
            stopping_reason=status.lower(), elapsed_time_ms=round((perf_counter() - started) * 1000, 3), input_hash=input_hash,
            result_hash=canonical_json_hash(payload), cache_hits=self.cache.hits, cache_misses=self.cache.misses,
            diagnostics=tuple(diagnostics), debug_geometry=debug_geometry,
        )

    def _invalid_result(self, request, input_hash, placements, validation, lower_bound, area_bound, evaluations, started, strategy):
        payload = {"status": "ENGINE_ERROR", "input_hash": input_hash, "validation": asdict(validation)}
        return MarkerResult(
            status="ENGINE_ERROR", search_status="INVALID_LAYOUT_REJECTED", marker_length=None, usable_width=request.usable_width,
            physical_width=request.physical_width, placements=placements, piece_area_total=0, marker_area=None, waste_area=None,
            efficiency_percentage=None, waste_percentage=None, lower_bound_length=lower_bound, area_lower_bound=area_bound,
            gap_to_area_lower_bound=None, validation=validation, algorithm=ALGORITHM, algorithm_version=ALGORITHM_VERSION,
            nfp_status=NFP_STATUS, seed=request.seed, piece_order_strategy=strategy, candidate_order=CANDIDATE_ORDER,
            transform_order=tuple(sorted(request.allowed_transforms)), evaluation_count=evaluations,
            stopping_reason="independent_validator_rejected_layout", elapsed_time_ms=round((perf_counter() - started) * 1000, 3),
            input_hash=input_hash, result_hash=canonical_json_hash(payload), cache_hits=self.cache.hits, cache_misses=self.cache.misses,
            diagnostics=validation.errors, debug_geometry=None,
        )
