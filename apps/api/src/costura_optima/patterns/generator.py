from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from math import isclose
from typing import Any

from shapely.geometry import Polygon

from costura_optima.domain.geometry import (
    CubicBezierSegment,
    LineSegment,
    Point,
    Segment,
    canonical_ring,
    derive_cutline,
    flatten_path,
    mirror_segment,
    path_length,
    path_to_dict,
    polygon_metrics,
    reverse_segment,
    ring_to_units,
)


PIECE_QUANTITIES = {"FRONT": 1, "BACK": 1, "SLEEVE": 2, "NECKBAND": 1}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GeneratedPiece:
    size_code: str
    piece_code: str
    quantity: int
    source_geometry: dict[str, Any]
    seamline_geometry: dict[str, Any]
    cutline_geometry: dict[str, Any]
    operational_geometry: dict[str, Any]
    grainline: dict[str, Any]
    allowed_rotations_degrees: list[int]
    mirror_allowed: bool
    edge_allowances_cm: dict[str, float]
    measurements: dict[str, float]
    metrics: dict[str, Any]
    geometry_hash: str


class EngineeringPatternGenerator:
    algorithm_version = "cubic-bezier-adaptive-flatten-variable-allowance-v3"

    def __init__(self, profile: dict[str, Any]):
        self.profile = profile
        self.tolerance = profile["curve_flatten_tolerance_cm"]
        self.units_per_cm = profile["geometry_units_per_cm"]

    def generate_size(self, size_code: str, size_index: int, measurements: dict[str, float]) -> list[GeneratedPiece]:
        front_segments = self._body_segments("FRONT", size_index, measurements)
        back_segments = self._body_segments("BACK", size_index, measurements)
        front = self._make_piece(size_code, "FRONT", front_segments, self._body_grainline(measurements), {})
        back = self._make_piece(size_code, "BACK", back_segments, self._body_grainline(measurements), {})

        front_armhole = path_length(front_segments, self.tolerance, "ARMHOLE") / 2
        back_armhole = path_length(back_segments, self.tolerance, "ARMHOLE") / 2
        sleeve_segments, cap_target, cap_actual = self._sleeve_segments(measurements, front_armhole, back_armhole)
        sleeve = self._make_piece(
            size_code,
            "SLEEVE",
            sleeve_segments,
            self._sleeve_grainline(measurements),
            {
                "front_armhole_cm": round(front_armhole, 4),
                "back_armhole_cm": round(back_armhole, 4),
                "sleeve_cap_target_cm": round(cap_target, 4),
                "sleeve_cap_actual_cm": round(cap_actual, 4),
                "sleeve_cap_ease_cm": self.profile["sleeve"]["cap_ease_cm"],
            },
        )

        neckline = path_length(front_segments, self.tolerance, "NECKLINE") + path_length(
            back_segments, self.tolerance, "NECKLINE"
        )
        neckband_segments, neckband_length = self._neckband_segments(neckline)
        neckband = self._make_piece(
            size_code,
            "NECKBAND",
            neckband_segments,
            self._neckband_grainline(neckband_length),
            {
                "actual_neckline_cm": round(neckline, 4),
                "neckband_ratio": self.profile["neckband"]["neckline_ratio"],
                "neckband_seam_length_cm": round(neckband_length, 4),
            },
        )
        return [front, back, sleeve, neckband]

    def _body_segments(self, piece_code: str, size_index: int, m: dict[str, float]) -> list[Segment]:
        body = self.profile["body"]
        length = m["HPS_LENGTH"]
        chest_half = m["FINISHED_CHEST"] / 4
        hem_half = m["FINISHED_HEM"] / 4
        shoulder_half = m["FINISHED_SHOULDERS"] / 2
        neck_width = min(
            body["neck_width_max_cm"],
            max(body["neck_width_min_cm"], m["FINISHED_SHOULDERS"] * body["neck_width_to_shoulder_ratio"]),
        )
        neck_half = neck_width / 2
        shoulder_drop = body["shoulder_drop_base_cm"] + size_index * body["shoulder_drop_grade_cm"]
        armhole_depth = m["FINISHED_CHEST"] / body["armhole_depth_chest_divisor"] + body["armhole_depth_add_cm"]
        underarm_y = length - armhole_depth
        shoulder_y = length - shoulder_drop
        depth = (
            body["front_neck_depth_base_cm"] + size_index * body["front_neck_depth_grade_cm"]
            if piece_code == "FRONT"
            else body["back_neck_depth_base_cm"] + size_index * body["back_neck_depth_grade_cm"]
        )
        inset_ratio = body["front_armhole_inset_ratio"] if piece_code == "FRONT" else body["back_armhole_inset_ratio"]

        center_bottom, hem, underarm = Point(0, 0), Point(hem_half, 0), Point(chest_half, underarm_y)
        shoulder, neck, center_neck = Point(shoulder_half, shoulder_y), Point(neck_half, length), Point(0, length - depth)
        right: list[Segment] = [
            LineSegment(center_bottom, hem, "HEM"),
            CubicBezierSegment(
                hem,
                Point(hem_half, underarm_y * 0.34),
                Point(chest_half, underarm_y * 0.72),
                underarm,
                "SIDE",
            ),
            CubicBezierSegment(
                underarm,
                Point(chest_half, underarm_y + armhole_depth * 0.25),
                Point(shoulder_half + (chest_half - shoulder_half) * inset_ratio, shoulder_y - armhole_depth * 0.34),
                shoulder,
                "ARMHOLE",
            ),
            LineSegment(shoulder, neck, "SHOULDER"),
            CubicBezierSegment(
                neck,
                Point(neck_half * 0.62, length),
                Point(neck_half * 0.24, length - depth),
                center_neck,
                "NECKLINE",
            ),
        ]
        left = [reverse_segment(mirror_segment(segment)) for segment in reversed(right)]
        return right + left

    def _sleeve_segments(
        self, m: dict[str, float], front_armhole: float, back_armhole: float
    ) -> tuple[list[Segment], float, float]:
        sleeve = self.profile["sleeve"]
        cap_half = (m["BICEPS"] + sleeve["cap_width_biceps_ease_cm"]) / 2
        opening_half = m["SLEEVE_OPENING"] / 2
        target = front_armhole + back_armhole + sleeve["cap_ease_cm"]
        length = m["SHORT_SLEEVE_LENGTH"]

        def segments_for_height(height: float) -> list[Segment]:
            cap_base_y = max(3.0, length - height)
            bottom_left, bottom_right = Point(-opening_half, 0), Point(opening_half, 0)
            left_cap, right_cap, top = Point(-cap_half, cap_base_y), Point(cap_half, cap_base_y), Point(0, length)
            return [
                LineSegment(bottom_left, bottom_right, "HEM"),
                LineSegment(bottom_right, right_cap, "SIDE"),
                CubicBezierSegment(
                    right_cap,
                    Point(cap_half * sleeve["front_control_ratio"], cap_base_y + height * 0.20),
                    Point(cap_half * 0.34, length),
                    top,
                    "CAP_FRONT",
                ),
                CubicBezierSegment(
                    top,
                    Point(-cap_half * 0.30, length),
                    Point(-cap_half * sleeve["back_control_ratio"], cap_base_y + height * 0.22),
                    left_cap,
                    "CAP_BACK",
                ),
                LineSegment(left_cap, bottom_left, "SIDE"),
            ]

        low, high = sleeve["cap_height_min_cm"], min(sleeve["cap_height_max_cm"], length - 3)
        for _ in range(sleeve["cap_solver_iterations"]):
            middle = (low + high) / 2
            candidate = segments_for_height(middle)
            cap_length = path_length(candidate, self.tolerance, "CAP_FRONT") + path_length(
                candidate, self.tolerance, "CAP_BACK"
            )
            if cap_length < target:
                low = middle
            else:
                high = middle
        result = segments_for_height((low + high) / 2)
        actual = path_length(result, self.tolerance, "CAP_FRONT") + path_length(result, self.tolerance, "CAP_BACK")
        return result, target, actual

    def _neckband_segments(self, neckline: float) -> tuple[list[Segment], float]:
        length = neckline * self.profile["neckband"]["neckline_ratio"]
        width = self.profile["neckband"]["finished_width_cm"] * 2
        p0, p1, p2, p3 = Point(0, 0), Point(length, 0), Point(length, width), Point(0, width)
        return [
            LineSegment(p0, p1, "NECKBAND"),
            LineSegment(p1, p2, "NECKBAND"),
            LineSegment(p2, p3, "NECKBAND"),
            LineSegment(p3, p0, "NECKBAND"),
        ], length

    def _make_piece(
        self,
        size_code: str,
        piece_code: str,
        segments: list[Segment],
        grainline: dict[str, Any],
        measurements: dict[str, float],
    ) -> GeneratedPiece:
        points, edge_paths = flatten_path(segments, self.tolerance)
        seam_ring = canonical_ring(points)
        allowance_values = self.profile["seam_allowances_cm"]
        edge_allowances = {
            edge: allowance_values["HEM"] if edge == "HEM" else allowance_values["NECKBAND"] if edge == "NECKBAND" else allowance_values["GENERAL"]
            for edge in sorted({segment.edge for segment in segments})
        }
        if piece_code == "NECKBAND":
            box = polygon_metrics(seam_ring)["bbox_cm"]
            allowance = allowance_values["NECKBAND"]
            cut_ring = canonical_ring([
                (box["min_x"] - allowance, box["min_y"] - allowance),
                (box["max_x"] + allowance, box["min_y"] - allowance),
                (box["max_x"] + allowance, box["max_y"] + allowance),
                (box["min_x"] - allowance, box["max_y"] + allowance),
            ])
        else:
            cut_ring = derive_cutline(seam_ring, edge_paths, edge_allowances)
        seam_metrics, cut_metrics = polygon_metrics(seam_ring), polygon_metrics(cut_ring)
        if not seam_metrics["valid"] or not cut_metrics["valid"]:
            raise ValueError(f"Invalid geometry for {size_code}/{piece_code}: {seam_metrics}, {cut_metrics}")
        if not Polygon(cut_ring).buffer(1e-8).covers(Polygon(seam_ring)):
            raise ValueError(f"Cutline does not contain seamline for {size_code}/{piece_code}")

        transforms = self.profile["transforms"]
        operational = {
            "type": "Polygon",
            "unit": "geometry_unit",
            "units_per_cm": self.units_per_cm,
            "coordinates": [ring_to_units(cut_ring, self.units_per_cm)],
        }
        hash_payload = {
            "algorithm_version": self.algorithm_version,
            "size_code": size_code,
            "piece_code": piece_code,
            "source_geometry": path_to_dict(segments),
            "operational_geometry": operational,
            "grainline": grainline,
            "transforms": transforms,
            "edge_allowances_cm": edge_allowances,
        }
        return GeneratedPiece(
            size_code=size_code,
            piece_code=piece_code,
            quantity=PIECE_QUANTITIES[piece_code],
            source_geometry=path_to_dict(segments),
            seamline_geometry={"type": "Polygon", "unit": "cm", "coordinates": [seam_ring]},
            cutline_geometry={"type": "Polygon", "unit": "cm", "coordinates": [cut_ring]},
            operational_geometry=operational,
            grainline=grainline,
            allowed_rotations_degrees=list(transforms["allowed_rotations_degrees"]),
            mirror_allowed=transforms["mirror_allowed"],
            edge_allowances_cm=edge_allowances,
            measurements=measurements,
            metrics={"seamline": seam_metrics, "cutline": cut_metrics},
            geometry_hash=canonical_hash(hash_payload),
        )

    @staticmethod
    def _body_grainline(m: dict[str, float]) -> dict[str, Any]:
        return {"kind": "STRAIGHT_GRAIN", "unit": "cm", "start": [0, 8], "end": [0, round(m["HPS_LENGTH"] - 10, 4)]}

    @staticmethod
    def _sleeve_grainline(m: dict[str, float]) -> dict[str, Any]:
        return {"kind": "STRAIGHT_GRAIN", "unit": "cm", "start": [0, 3], "end": [0, round(m["SHORT_SLEEVE_LENGTH"] - 3, 4)]}

    @staticmethod
    def _neckband_grainline(length: float) -> dict[str, Any]:
        return {"kind": "GREATEST_STRETCH", "unit": "cm", "start": [3, 1.8], "end": [round(length - 3, 4), 1.8]}


def validate_compatibility(pieces: list[GeneratedPiece], tolerance_cm: float) -> None:
    by_code = {piece.piece_code: piece for piece in pieces}
    sleeve = by_code["SLEEVE"].measurements
    if abs(sleeve["sleeve_cap_actual_cm"] - sleeve["sleeve_cap_target_cm"]) > tolerance_cm:
        raise ValueError("Sleeve cap and armhole seam lengths are incompatible.")
    neckband = by_code["NECKBAND"].measurements
    expected = neckband["actual_neckline_cm"] * neckband["neckband_ratio"]
    if not isclose(neckband["neckband_seam_length_cm"], expected, abs_tol=0.001):
        raise ValueError("Neckband length does not satisfy the configured neckline ratio.")
