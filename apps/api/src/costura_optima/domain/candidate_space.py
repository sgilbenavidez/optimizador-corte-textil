"""Authoritative integer candidate-space construction for one moving piece."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable

import pyclipper
from shapely.geometry import Polygon
from shapely.ops import nearest_points

from costura_optima.domain.integer_kernel import (
    IntegerGeometryKernel, boundary_intersections, canonical_paths, path_bbox,
    signed_area2, transform_and_normalize, transform_piece, translate_path,
)
from costura_optima.domain.nesting_models import IntPath, IntPoint


@dataclass(frozen=True)
class PlacedGeometry:
    geometry: IntPath
    geometry_hash: str
    rotation: int
    marker_position: IntPoint


@dataclass(frozen=True)
class CandidatePosition:
    position: IntPoint
    orientation: int
    sources: tuple[str, ...]
    contact_count: int


# CandidatePosition is deliberately a raw, polygonal-topology proposal.  It is
# not a placement and has no authority to enter a published marker.
RawGeometricCandidate = CandidatePosition


@dataclass(frozen=True)
class ValidatedCandidate:
    raw: RawGeometricCandidate
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class CandidateValidationMetrics:
    raw_candidate_count: int
    raw_feasible_candidate_count: int
    validator_accepted_count: int
    validator_rejected_clearance: int
    validator_rejected_overlap: int
    validator_rejected_containment: int


class CandidateValidator:
    """Separate raw polygon proposals from validator-authorised candidates.

    ``validate`` must construct the placement through ``transform_piece`` and
    invoke ``IndependentMarkerValidator`` over the complete marker.  Keeping it
    injected prevents CandidateSpace from becoming a second validator.
    """
    def filter(self, raw: tuple[RawGeometricCandidate, ...], feasible: Callable[[IntPoint], bool],
               validate: Callable[[RawGeometricCandidate], tuple[bool, str | None]]) -> tuple[tuple[ValidatedCandidate, ...], CandidateValidationMetrics]:
        rows: list[ValidatedCandidate] = []
        feasible_count = accepted = rejected_clearance = rejected_overlap = rejected_containment = 0
        for candidate in raw:
            if not feasible(candidate.position):
                continue
            feasible_count += 1
            ok, reason = validate(candidate)
            rows.append(ValidatedCandidate(candidate, ok, reason))
            if ok:
                accepted += 1
            elif reason == "CLEARANCE":
                rejected_clearance += 1
            elif reason == "OVERLAP":
                rejected_overlap += 1
            elif reason == "CONTAINMENT":
                rejected_containment += 1
        return tuple(rows), CandidateValidationMetrics(
            raw_candidate_count=len(raw), raw_feasible_candidate_count=feasible_count,
            validator_accepted_count=accepted, validator_rejected_clearance=rejected_clearance,
            validator_rejected_overlap=rejected_overlap, validator_rejected_containment=rejected_containment,
        )


@dataclass(frozen=True)
class CandidateSpaceResult:
    ifp_geometry: IntPath
    nfp_geometries: tuple[tuple[IntPath, ...], ...]
    forbidden_union: tuple[IntPath, ...]
    feasible_space: tuple[IntPath, ...]
    candidates: tuple[CandidatePosition, ...]
    metrics: dict[str, float | int]


@dataclass(frozen=True)
class BoundaryRepairResult:
    position: IntPoint | None
    status: str
    displacement_units: float | None
    attempts: int


def repair_candidate_to_clearance(
    moving: IntPath, orientation: int, position: IntPoint, placed: tuple[PlacedGeometry, ...],
    clearance: int, max_repair_units: int, contact_count: int = 1,
) -> BoundaryRepairResult:
    """Find the minimum integer single-contact displacement which restores clearance.

    This intentionally uses the same Euclidean geometry as the independent
    validator only to *repair a proposal*.  The caller must still run the full
    validator afterwards; this function cannot certify containment, grainline,
    transform policy, or every marker-level constraint.
    """
    if contact_count >= 2:
        return BoundaryRepairResult(None, "REPAIR_REQUIRES_MULTI_CONSTRAINT", None, 0)
    moving_polygon = Polygon(transform_piece(moving, orientation, position))
    obstacles = [Polygon(transform_piece(item.geometry, item.rotation, item.marker_position)) for item in placed]
    distances = [moving_polygon.distance(obstacle) for obstacle in obstacles]
    violating = [index for index, distance in enumerate(distances) if distance < clearance]
    if not violating:
        return BoundaryRepairResult(position, "ALREADY_CLEAR", 0.0, 0)
    nearest_index = min(violating, key=lambda index: distances[index])
    deficit = clearance - distances[nearest_index]
    if deficit > max_repair_units:
        return BoundaryRepairResult(None, "NON_APPROXIMATION_CLEARANCE_FAILURE", None, 0)
    obstacle_point, moving_point = nearest_points(obstacles[nearest_index], moving_polygon)
    vx, vy = moving_point.x - obstacle_point.x, moving_point.y - obstacle_point.y
    # A zero vector has no single-contact outward normal that can be trusted.
    if vx == 0 and vy == 0:
        return BoundaryRepairResult(None, "REPAIR_DIRECTION_UNDEFINED", None, 0)
    attempts = 0
    # Enumerate integer offsets in increasing Euclidean norm.  The dot-product
    # guard ensures candidates move away from the active obstacle, never toward it.
    options: list[tuple[int, int]] = []
    for dx in range(-max_repair_units, max_repair_units + 1):
        for dy in range(-max_repair_units, max_repair_units + 1):
            if not dx and not dy:
                continue
            if dx * vx + dy * vy <= 0:
                continue
            options.append((dx, dy))
    options.sort(key=lambda delta: (delta[0] * delta[0] + delta[1] * delta[1], delta))
    for dx, dy in options:
        attempts += 1
        repaired = Polygon(transform_piece(moving, orientation, (position[0] + dx, position[1] + dy)))
        if all(repaired.distance(obstacle) >= clearance for obstacle in obstacles):
            return BoundaryRepairResult((position[0] + dx, position[1] + dy), "REPAIRED", (dx * dx + dy * dy) ** 0.5, attempts)
    return BoundaryRepairResult(None, "REPAIR_LIMIT_EXHAUSTED", None, attempts)


def _significant_vertices(path: IntPath, target: int = 6) -> IntPath:
    if len(path) <= target:
        return path
    selected = {min(path), max(path)}
    selected.update({
        min(path, key=lambda point: (point[0], point[1])),
        max(path, key=lambda point: (point[0], -point[1])),
        min(path, key=lambda point: (point[1], point[0])),
        max(path, key=lambda point: (point[1], -point[0])),
    })
    step = max(1, len(path) // target)
    selected.update(path[::step])
    return tuple(sorted(selected))


def legacy_fallback_candidates(
    kernel: IntegerGeometryKernel, moving: IntPath, orientation: int,
    placed: tuple[PlacedGeometry, ...], clearance: int, region: tuple[int, int, int, int],
    compute_contact_counts: bool = True,
) -> tuple[CandidatePosition, ...]:
    """Legacy extrema/vertex-offset candidate positions, orientation-aware.

    Mirrors the LEGACY_FALLBACK branch in ``nesting_engine.py::_candidates`` so a
    frozen-prefix completion run can build a real unified pool (legacy +
    CandidateSpace) without depending on the full multi-start engine's
    ``Placement`` bookkeeping.  ``moving`` is the raw, unrotated cut polygon;
    orientation is applied the same way ``CandidateSpaceEngine.build`` does.
    """
    oriented = transform_and_normalize(moving, orientation)
    min_x, min_y, max_x, max_y = path_bbox(oriented)
    width, height = max_x - min_x, max_y - min_y
    x0, y0, x1, y1 = region
    points: set[IntPoint] = {(x0, y0), (x0, y1 - height)}
    moving_vertices = oriented if len(oriented) <= 24 else _significant_vertices(oriented, 12)
    for item in placed:
        absolute = transform_piece(item.geometry, item.rotation, item.marker_position)
        bx0, by0, bx1, by1 = path_bbox(absolute)
        xs = (x0, bx0, bx1 + clearance, bx0 - width - clearance, bx1 - width)
        ys = (y0, by0, by1 + clearance, by0 - height - clearance, by1 - height)
        points.update((x, y) for x in xs for y in ys)
        existing_vertices = absolute if len(absolute) <= 24 else _significant_vertices(absolute, 12)
        offsets = ((0, 0),) if clearance == 0 else ((clearance, 0), (-clearance, 0), (0, clearance), (0, -clearance))
        for px, py in existing_vertices:
            for mx, my in moving_vertices:
                for ox, oy in offsets:
                    points.add((px - mx + ox, py - my + oy))
    ifp_x0, ifp_y0, ifp_x1, ifp_y1 = kernel.ifp_bounds(oriented, region)
    valid = [point for point in points if ifp_x0 <= point[0] <= ifp_x1 and ifp_y0 <= point[1] <= ifp_y1]
    rows = []
    for point in sorted(valid):
        # Contact count is only ever used as a validated-candidate tie-break
        # (Section 22, Phase 2F.7G-8P): computing it here for every raw,
        # pre-validation proposal was the dominant profiled cost (real_contact_count
        # accounted for ~80% of microbenchmark wall time). Callers that need it
        # eagerly (e.g. a caller inspecting raw rows directly) can still ask for
        # it; the completion runner defers it to the much smaller accepted set.
        contacts = CandidateSpaceEngine.real_contact_count(moving, orientation, point, placed, clearance) if compute_contact_counts else 0
        rows.append(CandidatePosition(point, orientation, ("LEGACY_FALLBACK",), max(1, contacts) if compute_contact_counts else 0))
    return tuple(rows)


class CandidateSpaceEngine:
    """NFP/IFP share marker-space coordinates of the moving reference point."""
    def build(self, kernel: IntegerGeometryKernel, moving: IntPath, moving_hash: str,
              orientation: int, region: tuple[int, int, int, int], clearance: int,
              placed: tuple[PlacedGeometry, ...], compute_contact_counts: bool = True) -> CandidateSpaceResult:
        started = perf_counter()
        moving_local = transform_and_normalize(moving, orientation)
        ifp_bounds = kernel.ifp_bounds(moving_local, region)
        ifp = ((ifp_bounds[0], ifp_bounds[1]), (ifp_bounds[2], ifp_bounds[1]),
               (ifp_bounds[2], ifp_bounds[3]), (ifp_bounds[0], ifp_bounds[3]))
        nfps: list[tuple[IntPath, ...]] = []
        translated_nfps: list[IntPath] = []
        for item in placed:
            fixed_local = transform_and_normalize(item.geometry, item.rotation)
            relative = kernel.nfp(fixed_local, moving_local, item.geometry_hash, item.rotation,
                                  moving_hash, orientation, clearance)
            # Pyclipper's closed Minkowski can return an interior bookkeeping
            # contour.  The outer forbidden contour is the maximal-area path;
            # holes are a future topology extension rather than additional
            # forbidden islands.
            outer = max(relative, key=lambda path: abs(signed_area2(path)))
            marker_nfp = (translate_path(outer, *item.marker_position),)
            nfps.append(marker_nfp); translated_nfps.extend(marker_nfp)
        union_started = perf_counter()
        # Clipper has no empty-subject union.  With no placed obstacle the
        # forbidden topology is exactly empty and the whole IFP is feasible.
        forbidden = self._boolean(pyclipper.CT_UNION, translated_nfps, ()) if translated_nfps else ()
        union_ms = (perf_counter() - union_started) * 1000
        diff_started = perf_counter()
        feasible = self._boolean(pyclipper.CT_DIFFERENCE, (ifp,), forbidden) if forbidden else (ifp,)
        difference_ms = (perf_counter() - diff_started) * 1000
        candidates: dict[IntPoint, set[str]] = {}
        def add(point: IntPoint, source: str) -> None:
            if ifp_bounds[0] <= point[0] <= ifp_bounds[2] and ifp_bounds[1] <= point[1] <= ifp_bounds[3]:
                candidates.setdefault(point, set()).add(source)
        for point in ifp: add(point, "IFP_VERTEX")
        for paths in nfps:
            for contour in paths:
                for point in contour: add(point, "NFP_VERTEX")
                for point in boundary_intersections(contour, ifp): add(point, "NFP_IFP_INTERSECTION")
        for left in range(len(nfps)):
            for right in range(left + 1, len(nfps)):
                for first in nfps[left]:
                    for second in nfps[right]:
                        for point in boundary_intersections(first, second): add(point, "NFP_NFP_INTERSECTION")
        for contour in feasible:
            for point in contour: add(point, "FEASIBLE_BOUNDARY")
        rows = []
        priority = {"NFP_NFP_INTERSECTION": 0, "NFP_IFP_INTERSECTION": 1,
                    "FEASIBLE_BOUNDARY": 2, "NFP_VERTEX": 3, "IFP_VERTEX": 4}
        for point, sources in candidates.items():
            # Provenance explains where a proposal came from; contact count is
            # independently measured in physical marker space and is never
            # inferred merely from an intersection label.  Computing it eagerly
            # for every raw candidate here is the default (unchanged existing
            # contract for every current caller/test); compute_contact_counts=False
            # lets a caller defer it to CandidateSpaceEngine.real_contact_count
            # applied only to the much smaller post-validation accepted set.
            contacts = self.real_contact_count(moving, orientation, point, placed, clearance) if compute_contact_counts else 0
            rows.append(CandidatePosition(point, orientation, tuple(sorted(sources)), max(1, contacts) if compute_contact_counts else 0))
        rows.sort(key=lambda row: (min(priority[source] for source in row.sources), row.position))
        return CandidateSpaceResult(ifp, tuple(nfps), forbidden, feasible, tuple(rows), {
            "nfp_count": len(nfps), "forbidden_components": len(forbidden), "feasible_components": len(feasible),
            "candidate_total": len(rows), "union_ms": round(union_ms, 3),
            "difference_ms": round(difference_ms, 3), "total_ms": round((perf_counter() - started) * 1000, 3),
        })

    @staticmethod
    def real_contact_count(moving: IntPath, orientation: int, position: IntPoint,
                           placed: tuple[PlacedGeometry, ...], clearance: int,
                           tolerance_units: float = 1e-7) -> int:
        """Count exact Euclidean clearance contacts at a marker-space position."""
        candidate = Polygon(transform_piece(moving, orientation, position))
        return sum(
            abs(candidate.distance(Polygon(transform_piece(item.geometry, item.rotation, item.marker_position))) - clearance)
            <= tolerance_units
            for item in placed
        )

    @staticmethod
    def is_valid_reference_position(result: CandidateSpaceResult, position: IntPoint) -> bool:
        """Classify a marker-space reference point using the constructed IFP/NFPs.

        IFP boundaries are valid containment positions.  NFP boundaries are also
        valid: clearance is contractual as ``distance < clearance`` is forbidden,
        so a point exactly at clearance is a permitted contact.
        """
        in_ifp = any(pyclipper.PointInPolygon(position, list(path)) != 0 for path in (result.ifp_geometry,))
        in_forbidden_interior = any(
            pyclipper.PointInPolygon(position, list(path)) == 1
            for path in result.forbidden_union
        )
        return in_ifp and not in_forbidden_interior

    @staticmethod
    def interior_candidates(result: CandidateSpaceResult, orientation: int, max_grid_side: int = 8) -> tuple[CandidatePosition, ...]:
        """Return a small deterministic set of strictly interior topology probes.

        Boundary topology is deliberately not a certificate of Euclidean
        feasibility.  These probes are bounded (one representative plus an
        8x8 scanline lattice per component) and are ordered by component then
        coordinates, so callers can safely pass them through the authoritative
        exact validator without introducing random sampling.
        """
        rows: dict[IntPoint, CandidatePosition] = {}
        for component_id, contour in enumerate(result.feasible_space):
            x0, y0, x1, y1 = path_bbox(contour)
            polygon = Polygon(contour)
            representative = polygon.representative_point()
            probes = {(round(representative.x), round(representative.y))}
            # A bounded scanline grid, placed away from the boundary.  The
            # integer point-in-polygon check preserves the Clipper topology
            # contract before exact Euclidean validation.
            for xi in range(1, max_grid_side + 1):
                for yi in range(1, max_grid_side + 1):
                    probes.add((x0 + (x1 - x0) * xi // (max_grid_side + 1),
                                y0 + (y1 - y0) * yi // (max_grid_side + 1)))
            for point in sorted(probes):
                if pyclipper.PointInPolygon(point, list(contour)) == 1:
                    rows.setdefault(point, CandidatePosition(
                        point, orientation, ("FEASIBLE_INTERIOR_RECOVERY",), 0
                    ))
        return tuple(rows[point] for point in sorted(rows))

    @staticmethod
    def _boolean(operation: int, subject: tuple[IntPath, ...] | list[IntPath], clip: tuple[IntPath, ...] | list[IntPath]) -> tuple[IntPath, ...]:
        engine = pyclipper.Pyclipper()
        if subject: engine.AddPaths([list(path) for path in subject], pyclipper.PT_SUBJECT, True)
        if clip: engine.AddPaths([list(path) for path in clip], pyclipper.PT_CLIP, True)
        return canonical_paths(engine.Execute(operation, pyclipper.PFT_EVENODD, pyclipper.PFT_EVENODD))
