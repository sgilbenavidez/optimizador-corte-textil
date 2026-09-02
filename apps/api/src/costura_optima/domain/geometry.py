from __future__ import annotations

from dataclasses import dataclass
from math import hypot
from typing import Any, Iterable

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def as_list(self) -> list[float]:
        return [round(self.x, 6), round(self.y, 6)]


@dataclass(frozen=True)
class LineSegment:
    start: Point
    end: Point
    edge: str


@dataclass(frozen=True)
class CubicBezierSegment:
    start: Point
    control1: Point
    control2: Point
    end: Point
    edge: str


Segment = LineSegment | CubicBezierSegment


@dataclass(frozen=True)
class PatternPieceGeometry:
    """Geometry boundary consumed by future layout engines; no ORM/API types."""

    piece_code: str
    size_code: str
    seamline_units: tuple[tuple[int, int], ...]
    cutline_units: tuple[tuple[int, int], ...]
    grainline_units: tuple[tuple[int, int], tuple[int, int]]
    allowed_rotations_degrees: tuple[int, ...]
    mirror_allowed: bool


def _distance_to_line(point: Point, start: Point, end: Point) -> float:
    dx, dy = end.x - start.x, end.y - start.y
    if dx == 0 and dy == 0:
        return hypot(point.x - start.x, point.y - start.y)
    return abs(dy * point.x - dx * point.y + end.x * start.y - end.y * start.x) / hypot(dx, dy)


def _split_cubic(segment: CubicBezierSegment) -> tuple[CubicBezierSegment, CubicBezierSegment]:
    def midpoint(a: Point, b: Point) -> Point:
        return Point((a.x + b.x) / 2, (a.y + b.y) / 2)

    p01 = midpoint(segment.start, segment.control1)
    p12 = midpoint(segment.control1, segment.control2)
    p23 = midpoint(segment.control2, segment.end)
    p012 = midpoint(p01, p12)
    p123 = midpoint(p12, p23)
    middle = midpoint(p012, p123)
    return (
        CubicBezierSegment(segment.start, p01, p012, middle, segment.edge),
        CubicBezierSegment(middle, p123, p23, segment.end, segment.edge),
    )


def flatten_segment(segment: Segment, tolerance_cm: float, depth: int = 0) -> list[Point]:
    if isinstance(segment, LineSegment):
        return [segment.start, segment.end]
    flatness = max(
        _distance_to_line(segment.control1, segment.start, segment.end),
        _distance_to_line(segment.control2, segment.start, segment.end),
    )
    if flatness <= tolerance_cm or depth >= 20:
        return [segment.start, segment.end]
    left, right = _split_cubic(segment)
    return flatten_segment(left, tolerance_cm, depth + 1)[:-1] + flatten_segment(right, tolerance_cm, depth + 1)


def flatten_path(segments: Iterable[Segment], tolerance_cm: float) -> tuple[list[Point], list[tuple[str, list[Point]]]]:
    points: list[Point] = []
    edge_paths: list[tuple[str, list[Point]]] = []
    for segment in segments:
        flattened = flatten_segment(segment, tolerance_cm)
        edge_paths.append((segment.edge, flattened))
        points.extend(flattened if not points else flattened[1:])
    return points, edge_paths


def segment_to_dict(segment: Segment) -> dict[str, Any]:
    if isinstance(segment, LineSegment):
        return {"kind": "LINE", "start": segment.start.as_list(), "end": segment.end.as_list(), "edge": segment.edge}
    return {
        "kind": "CUBIC_BEZIER",
        "start": segment.start.as_list(),
        "control1": segment.control1.as_list(),
        "control2": segment.control2.as_list(),
        "end": segment.end.as_list(),
        "edge": segment.edge,
    }


def path_to_dict(segments: Iterable[Segment]) -> dict[str, Any]:
    return {"type": "STRUCTURED_PATH", "closed": True, "segments": [segment_to_dict(item) for item in segments]}


def _signed_area(points: list[tuple[float, float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1])) / 2


def canonical_ring(points: Iterable[Point | tuple[float, float]], digits: int = 6) -> list[list[float]]:
    values: list[tuple[float, float]] = []
    for point in points:
        value = (point.x, point.y) if isinstance(point, Point) else point
        rounded = (round(value[0], digits), round(value[1], digits))
        if not values or rounded != values[-1]:
            values.append(rounded)
    if values and values[0] == values[-1]:
        values.pop()
    if len(values) < 3:
        raise ValueError("A polygon needs at least three distinct vertices.")
    if _signed_area(values) < 0:
        values.reverse()
    start = min(range(len(values)), key=lambda index: values[index])
    values = values[start:] + values[:start]
    values.append(values[0])
    return [[x, y] for x, y in values]


def polygon_metrics(ring: list[list[float]]) -> dict[str, Any]:
    polygon = Polygon(ring)
    min_x, min_y, max_x, max_y = polygon.bounds
    return {
        "valid": polygon.is_valid and polygon.area > 0,
        "validity_reason": explain_validity(polygon),
        "area_cm2": round(polygon.area, 4),
        "perimeter_cm": round(polygon.length, 4),
        "bbox_cm": {"min_x": round(min_x, 4), "min_y": round(min_y, 4), "max_x": round(max_x, 4), "max_y": round(max_y, 4)},
    }


def derive_cutline(
    seam_ring: list[list[float]],
    edge_paths: list[tuple[str, list[Point]]],
    allowances_cm: dict[str, float],
) -> list[list[float]]:
    """Build an audited variable allowance by buffering each semantic edge independently."""
    additions = [Polygon(seam_ring)]
    for edge, points in edge_paths:
        allowance = allowances_cm[edge]
        additions.append(LineString([(point.x, point.y) for point in points]).buffer(allowance, cap_style=1, join_style=2))
    result = unary_union(additions)
    if result.geom_type == "MultiPolygon":
        result = max(result.geoms, key=lambda item: item.area)
    return canonical_ring(list(result.exterior.coords))


def ring_to_units(ring: list[list[float]], units_per_cm: int) -> list[list[int]]:
    return [[round(x * units_per_cm), round(y * units_per_cm)] for x, y in ring]


def path_length(segments: Iterable[Segment], tolerance_cm: float, edge: str | None = None) -> float:
    total = 0.0
    for segment in segments:
        if edge is not None and segment.edge != edge:
            continue
        points = flatten_segment(segment, tolerance_cm)
        total += sum(hypot(b.x - a.x, b.y - a.y) for a, b in zip(points, points[1:]))
    return total


def reverse_segment(segment: Segment) -> Segment:
    if isinstance(segment, LineSegment):
        return LineSegment(segment.end, segment.start, segment.edge)
    return CubicBezierSegment(segment.end, segment.control2, segment.control1, segment.start, segment.edge)


def mirror_segment(segment: Segment) -> Segment:
    mirror = lambda point: Point(-point.x, point.y)
    if isinstance(segment, LineSegment):
        return LineSegment(mirror(segment.start), mirror(segment.end), segment.edge)
    return CubicBezierSegment(
        mirror(segment.start), mirror(segment.control1), mirror(segment.control2), mirror(segment.end), segment.edge
    )
