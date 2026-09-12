from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import json
from math import ceil
from typing import Any, Iterable

import pyclipper
from shapely.geometry import Polygon

from costura_optima.domain.nesting_models import IntPath, IntPoint, PrecisionConfiguration


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def signed_area2(path: Iterable[IntPoint]) -> int:
    points = list(path)
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))


def canonical_path(path: Iterable[Iterable[int]]) -> IntPath:
    points: list[IntPoint] = []
    for raw in path:
        point = (int(raw[0]), int(raw[1]))
        if not points or point != points[-1]:
            points.append(point)
    if points and points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        raise ValueError("Polygon requires at least three distinct integer vertices.")
    if signed_area2(points) < 0:
        points.reverse()
    start = min(range(len(points)), key=lambda index: points[index])
    return tuple(points[start:] + points[:start])


def close_path(path: IntPath) -> list[list[int]]:
    return [[x, y] for x, y in path + (path[0],)]


def path_bbox(path: IntPath) -> tuple[int, int, int, int]:
    xs, ys = zip(*path)
    return min(xs), min(ys), max(xs), max(ys)


def translate_path(path: IntPath, x: int, y: int) -> IntPath:
    return tuple((px + x, py + y) for px, py in path)


def canonical_paths(paths: Iterable[Iterable[Iterable[int]]]) -> tuple[IntPath, ...]:
    """Stable component serialization independent of Pyclipper return order."""
    return tuple(sorted((canonical_path(path) for path in paths if len(path) >= 3), key=lambda path: (
        -abs(signed_area2(path)), path_bbox(path), path,
    )))


def boundary_intersections(first: IntPath, second: IntPath) -> tuple[IntPoint, ...]:
    """Exact integer segment contacts; non-integral crossings are not rounded."""
    found: set[IntPoint] = set()
    for a, b in zip(first, first[1:] + first[:1]):
        for c, d in zip(second, second[1:] + second[:1]):
            rx, ry = b[0] - a[0], b[1] - a[1]
            sx, sy = d[0] - c[0], d[1] - c[1]
            den = rx * sy - ry * sx
            qx, qy = c[0] - a[0], c[1] - a[1]
            if den == 0:
                for point in (a, b, c, d):
                    if min(a[0], b[0]) <= point[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= point[1] <= max(a[1], b[1]) and min(c[0], d[0]) <= point[0] <= max(c[0], d[0]) and min(c[1], d[1]) <= point[1] <= max(c[1], d[1]):
                        found.add(point)
                continue
            t_num, u_num = qx * sy - qy * sx, qx * ry - qy * rx
            if not (0 <= t_num <= den if den > 0 else den <= t_num <= 0):
                continue
            if not (0 <= u_num <= den if den > 0 else den <= u_num <= 0):
                continue
            x_num, y_num = a[0] * den + t_num * rx, a[1] * den + t_num * ry
            if x_num % den == 0 and y_num % den == 0:
                found.add((x_num // den, y_num // den))
    return tuple(sorted(found))


def transform_and_normalize(path: IntPath, rotation: int) -> IntPath:
    if rotation == 0:
        transformed = path
    elif rotation == 180:
        transformed = tuple((-x, -y) for x, y in path)
    else:
        raise ValueError(f"Unsupported rotation: {rotation}")
    min_x, min_y, _, _ = path_bbox(transformed)
    return canonical_path((x - min_x, y - min_y) for x, y in transformed)


# Coordinate contract: local piece space is canonicalized then normalized so its
# sole reference point is (0, 0).  Marker and container spaces share integer
# axes (X=marker length, Y=fabric width).  A placement translation is therefore
# exactly the marker-space coordinate of that reference point.
PIECE_REFERENCE_POINT: IntPoint = (0, 0)


def transform_piece(path: IntPath, rotation: int, marker_position: IntPoint) -> IntPath:
    """The only local-piece -> marker-space transform contract."""
    return translate_path(transform_and_normalize(path, rotation), *marker_position)


def rotate_and_translate_point(point: IntPoint, rotation: int, source_path: IntPath, translation: IntPoint) -> IntPoint:
    if rotation == 0:
        raw = point
    elif rotation == 180:
        raw = (-point[0], -point[1])
    else:
        raise ValueError(f"Unsupported rotation: {rotation}")
    rotated_path = source_path if rotation == 0 else tuple((-x, -y) for x, y in source_path)
    min_x, min_y, _, _ = path_bbox(rotated_path)
    return raw[0] - min_x + translation[0], raw[1] - min_y + translation[1]


class GeometryOperationCache:
    def __init__(self, max_entries: int = 1024):
        self.max_entries = max_entries
        self._values: OrderedDict[tuple, Any] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> Any | None:
        value = self._values.get(key)
        if value is None:
            self.misses += 1
            return None
        self._values.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: tuple, value: Any) -> None:
        self._values[key] = value
        self._values.move_to_end(key)
        while len(self._values) > self.max_entries:
            self._values.popitem(last=False)


@dataclass
class IntegerGeometryKernel:
    precision: PrecisionConfiguration
    cache: GeometryOperationCache

    def polygon(self, path: IntPath) -> Polygon:
        return _cached_polygon(path)

    def area_units2(self, path: IntPath) -> int:
        return abs(signed_area2(path)) // 2

    def perimeter_units(self, path: IntPath) -> float:
        return self.polygon(path).length

    def offset(self, path: IntPath, delta: int, geometry_hash: str, rotation: int) -> IntPath:
        key = (geometry_hash, rotation, delta, self.precision.kernel_version)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        offsetter = pyclipper.PyclipperOffset(miter_limit=2.0, arc_tolerance=max(1.0, delta / 100))
        offsetter.AddPath(list(path), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        solutions = offsetter.Execute(delta)
        if not solutions:
            raise ValueError("Integer offset produced no polygon.")
        result = canonical_path(max(solutions, key=lambda item: abs(pyclipper.Area(item))))
        self.cache.put(key, result)
        return result

    def nfp(self, fixed: IntPath, moving: IntPath, fixed_hash: str, fixed_rotation: int,
            moving_hash: str, moving_rotation: int, clearance: int) -> tuple[IntPath, ...]:
        """Integer Minkowski NFP for the reference point of ``moving``.

        The fixed polygon is expanded by clearance before the Minkowski sum with
        the reflected moving polygon.  Pyclipper is local/offline and keeps the
        construction in integer coordinates.  Returned contours are canonical;
        callers still use the independent predicate for final acceptance.
        """
        key = ("nfp-v1", fixed_hash, fixed_rotation, moving_hash, moving_rotation,
               clearance, self.precision.kernel_version)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        base = fixed
        if clearance:
            # The expanded obstacle makes a boundary contact satisfy clearance.
            offsetter = pyclipper.PyclipperOffset(miter_limit=2.0, arc_tolerance=max(1.0, clearance / 100))
            offsetter.AddPath(list(fixed), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
            expanded = offsetter.Execute(clearance)
            if not expanded:
                raise ValueError("NFP fixed-polygon offset produced no contour")
            base = canonical_path(max(expanded, key=lambda item: abs(pyclipper.Area(item))))
        reflected = [(-x, -y) for x, y in moving]
        paths = pyclipper.MinkowskiSum(list(base), reflected, True)
        canonical = tuple(sorted(
            (canonical_path(path) for path in paths if len(path) >= 3),
            key=lambda path: (path_bbox(path), path),
        ))
        if not canonical:
            raise ValueError("NFP Minkowski produced no contour")
        self.cache.put(key, canonical)
        return canonical

    @staticmethod
    def ifp_bounds(moving: IntPath, region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Rectangular IFP for a normalized piece reference point, in integers."""
        min_x, min_y, max_x, max_y = path_bbox(moving)
        x0, y0, x1, y1 = region
        return x0 - min_x, y0 - min_y, x1 - max_x, y1 - max_y

    def inside_rectangle(self, path: IntPath, bounds: tuple[int, int, int, int]) -> bool:
        min_x, min_y, max_x, max_y = path_bbox(path)
        return min_x >= bounds[0] and min_y >= bounds[1] and max_x <= bounds[2] and max_y <= bounds[3]

    def conflicts(self, first: IntPath, second: IntPath, clearance: int) -> bool:
        a, b = self.polygon(first), self.polygon(second)
        if clearance == 0:
            return a.intersection(b).area > 0
        return a.distance(b) < clearance - self.precision.distance_comparison_epsilon_units

    @staticmethod
    def bboxes_may_conflict(a: tuple[int, int, int, int], b: tuple[int, int, int, int], clearance: int) -> bool:
        return not (
            a[2] + clearance <= b[0]
            or b[2] + clearance <= a[0]
            or a[3] + clearance <= b[1]
            or b[3] + clearance <= a[1]
        )

    @staticmethod
    def lower_bounds(paths_by_instance: Iterable[tuple[IntPath, tuple[int, ...]]], usable_width: int, margins_start_end: int) -> tuple[int, int, int]:
        values = list(paths_by_instance)
        total_area = sum(abs(signed_area2(path)) // 2 for path, _ in values)
        area_bound = ceil(total_area / usable_width) + margins_start_end
        largest_extent = 0
        for path, rotations in values:
            extents = []
            for rotation in rotations:
                transformed = transform_and_normalize(path, rotation)
                min_x, _, max_x, _ = path_bbox(transformed)
                extents.append(max_x - min_x)
            if extents:
                largest_extent = max(largest_extent, min(extents))
        return area_bound, largest_extent + margins_start_end, max(area_bound, largest_extent + margins_start_end)
@lru_cache(maxsize=8192)
def _cached_polygon(path: IntPath) -> Polygon:
    return Polygon(path)
