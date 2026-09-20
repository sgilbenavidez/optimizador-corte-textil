from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import LineString, Polygon

from costura_optima.domain.integer_kernel import (
    GeometryOperationCache,
    IntegerGeometryKernel,
    canonical_path,
    path_bbox,
    rotate_and_translate_point,
    transform_and_normalize,
    transform_piece,
    translate_path,
)
from costura_optima.domain.nesting_models import MarkerRequest, Placement, ValidationReport
from costura_optima.domain.transform_policy import EffectiveTransformResolver


@dataclass(frozen=True)
class IncrementalValidationResult:
    """Result of validating one proposed placement against a trusted prefix."""
    accepted: bool
    reason: str | None
    bbox_rejected: int = 0
    exact_overlap_rejected: int = 0
    clearance_rejected: int = 0
    containment_rejected: int = 0
    exact_checks: int = 0


class IncrementalCandidateValidator:
    """Validate only the new placement against an already validated prefix.

    The prefix is contractual input: this deliberately does not replace
    ``IndependentMarkerValidator`` for commits or final-marker validation.
    It avoids rebuilding and revalidating the same prefix for every candidate.
    """
    def __init__(self, request: MarkerRequest, placements: tuple[Placement, ...]):
        self.request = request
        self.placements = placements
        self.kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache(max_entries=64))
        self.region = (request.margins.start, request.margins.left,
                       request.max_length - request.margins.end,
                       request.margins.left + request.usable_width)
        self._polygons = tuple((item, Polygon(item.transformed_polygon)) for item in placements)

    def validate(self, instance, placement: Placement) -> IncrementalValidationResult:
        resolver = EffectiveTransformResolver(
            self.request.fabric_directionality, self.request.marker_direction_policy,
            self.request.lay_face_mode, self.request.transform_lab_mode,
        )
        allowed = resolver.resolve(
            tuple(sorted(set(instance.piece.allowed_rotations).intersection(self.request.allowed_transforms))),
            instance.piece.grainline_policy,
        )
        if placement.rotation not in allowed or placement.mirrored:
            return IncrementalValidationResult(False, "ORIENTATION")
        # Placements enter this path only through CompletionRunner's cached
        # source transform.  The independent validator still re-derives this
        # geometry before commit/final publication.
        if not self.kernel.inside_rectangle(placement.transformed_polygon, self.region):
            return IncrementalValidationResult(False, "CONTAINMENT", containment_rejected=1)
        grain_start = rotate_and_translate_point(instance.piece.grainline[0], placement.rotation,
                                                 instance.piece.cut_polygon, placement.translation)
        grain_end = rotate_and_translate_point(instance.piece.grainline[1], placement.rotation,
                                               instance.piece.cut_polygon, placement.translation)
        candidate = Polygon(placement.transformed_polygon)
        if placement.transformed_grainline != (grain_start, grain_end) or not candidate.covers(LineString([grain_start, grain_end])):
            return IncrementalValidationResult(False, "GRAINLINE")
        nearby = [(old, polygon) for old, polygon in self._polygons
                  if self.kernel.bboxes_may_conflict(placement.bbox, old.bbox, self.request.clearance)]
        if not nearby:
            return IncrementalValidationResult(True, None, bbox_rejected=len(self._polygons))
        for old, polygon in nearby:
            if self.request.clearance == 0:
                if candidate.intersection(polygon).area > 0:
                    return IncrementalValidationResult(False, "OVERLAP", bbox_rejected=len(self._polygons) - len(nearby), exact_overlap_rejected=1, exact_checks=1)
            else:
                if candidate.distance(polygon) < self.request.clearance - self.request.precision.distance_comparison_epsilon_units:
                    return IncrementalValidationResult(False, "CLEARANCE", bbox_rejected=len(self._polygons) - len(nearby), clearance_rejected=1, exact_checks=1)
        return IncrementalValidationResult(True, None, bbox_rejected=len(self._polygons) - len(nearby), exact_checks=len(nearby))


class IndependentMarkerValidator:
    """Rebuilds and verifies the marker without sharing nesting-engine state."""

    def validate(self, request: MarkerRequest, placements: tuple[Placement, ...], marker_length: int) -> ValidationReport:
        kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache(max_entries=64))
        errors: list[str] = []
        expected = {instance.instance_id: instance for instance in request.piece_instances}
        actual_ids = [placement.piece_instance_id for placement in placements]
        checks = {
            "all_instances_present": set(actual_ids) == set(expected) and len(actual_ids) == len(expected),
            "no_duplicate_instances": len(actual_ids) == len(set(actual_ids)),
            "inside_valid_region": True,
            "no_polygon_overlap": True,
            "clearance_satisfied": True,
            "transforms_allowed": True,
            "grainline_valid": True,
            "max_length_respected": marker_length <= request.max_length,
            "quantities_correct": set(actual_ids) == set(expected),
            "placement_geometry_matches_source": True,
        }
        for name in ("all_instances_present", "no_duplicate_instances", "max_length_respected", "quantities_correct"):
            if not checks[name]:
                errors.append(name)

        region = (
            request.margins.start,
            request.margins.left,
            request.max_length - request.margins.end,
            request.margins.left + request.usable_width,
        )
        polygons: list[tuple[Placement, Polygon]] = []
        for placement in placements:
            instance = expected.get(placement.piece_instance_id)
            if instance is None:
                errors.append(f"unexpected_instance:{placement.piece_instance_id}")
                continue
            allowed = EffectiveTransformResolver(
                request.fabric_directionality, request.marker_direction_policy,
                request.lay_face_mode, request.transform_lab_mode,
            ).resolve(tuple(sorted(set(instance.piece.allowed_rotations).intersection(request.allowed_transforms))), instance.piece.grainline_policy)
            transform_ok = placement.rotation in allowed and placement.mirrored is False and not placement.mirrored
            checks["transforms_allowed"] &= transform_ok
            if not transform_ok:
                errors.append(f"transform_not_allowed:{placement.piece_instance_id}")
            expected_polygon = transform_piece(instance.piece.cut_polygon, placement.rotation, placement.translation) if transform_ok else ()
            geometry_ok = transform_ok and canonical_path(placement.transformed_polygon) == canonical_path(expected_polygon)
            checks["placement_geometry_matches_source"] &= geometry_ok
            if not geometry_ok:
                errors.append(f"geometry_mismatch:{placement.piece_instance_id}")
            inside = kernel.inside_rectangle(placement.transformed_polygon, region)
            checks["inside_valid_region"] &= inside
            if not inside:
                errors.append(f"outside_region:{placement.piece_instance_id}")
            if transform_ok:
                grain_start = rotate_and_translate_point(
                    instance.piece.grainline[0], placement.rotation, instance.piece.cut_polygon, placement.translation
                )
                grain_end = rotate_and_translate_point(
                    instance.piece.grainline[1], placement.rotation, instance.piece.cut_polygon, placement.translation
                )
                grain_ok = placement.transformed_grainline == (grain_start, grain_end) and Polygon(
                    placement.transformed_polygon
                ).covers(LineString([grain_start, grain_end]))
            else:
                grain_ok = False
            checks["grainline_valid"] &= grain_ok
            if not grain_ok:
                errors.append(f"invalid_grainline:{placement.piece_instance_id}")
            polygons.append((placement, Polygon(placement.transformed_polygon)))

        pair_checks = 0
        for index, (first, first_polygon) in enumerate(polygons):
            for second, second_polygon in polygons[index + 1 :]:
                pair_checks += 1
                if request.clearance == 0:
                    overlap = first_polygon.intersection(second_polygon).area > 0
                    if overlap:
                        checks["no_polygon_overlap"] = False
                        errors.append(f"overlap:{first.piece_instance_id}:{second.piece_instance_id}")
                else:
                    distance = first_polygon.distance(second_polygon)
                    if distance < request.clearance - request.precision.distance_comparison_epsilon_units:
                        checks["clearance_satisfied"] = False
                        checks["no_polygon_overlap"] = False if distance == 0 else checks["no_polygon_overlap"]
                        errors.append(f"clearance:{first.piece_instance_id}:{second.piece_instance_id}:{distance:.6f}")

        status = "VALIDATED" if all(checks.values()) else "INVALID"
        return ValidationReport(status=status, checks=checks, errors=tuple(sorted(set(errors))), pair_checks=pair_checks)
