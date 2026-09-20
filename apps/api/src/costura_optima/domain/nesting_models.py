from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


IntPoint = tuple[int, int]
IntPath = tuple[IntPoint, ...]


@dataclass(frozen=True)
class PrecisionConfiguration:
    geometry_units_per_cm: int = 1000
    curve_flattening_tolerance_units: int = 20
    topology_epsilon_units: int = 0
    distance_comparison_epsilon_units: float = 1e-7
    kernel_version: str = "pyclipper-1.4+shapely-2.1-v1"


@dataclass(frozen=True)
class MarkerMargins:
    left: int
    right: int
    start: int
    end: int


@dataclass(frozen=True)
class NestingPiece:
    pattern_piece_id: str
    size_code: str
    piece_code: str
    cut_polygon: IntPath
    grainline: tuple[IntPoint, IntPoint]
    allowed_rotations: tuple[int, ...]
    mirror_allowed: bool
    geometry_hash: str
    grainline_policy: str = "STRAIGHT_GRAIN_TWO_WAY"


@dataclass(frozen=True)
class PieceInstance:
    instance_id: str
    piece: NestingPiece


@dataclass(frozen=True)
class MarkerRequest:
    request_id: str
    piece_instances: tuple[PieceInstance, ...]
    usable_width: int
    physical_width: int
    max_length: int
    clearance: int
    margins: MarkerMargins
    allowed_transforms: tuple[int, ...]
    fabric_directionality: str = "NON_DIRECTIONAL"
    marker_direction_policy: str = "TWO_WAY"
    lay_face_mode: str = "FACE_ONE_WAY"
    transform_lab_mode: bool = False
    deterministic: bool = True
    seed: int = 1
    evaluation_budget: int = 100_000
    debug: bool = False
    precision: PrecisionConfiguration = field(default_factory=PrecisionConfiguration)


@dataclass(frozen=True)
class Placement:
    piece_instance_id: str
    pattern_piece_id: str
    size_code: str
    piece_code: str
    rotation: int
    mirrored: bool
    translation: IntPoint
    transformed_polygon: IntPath
    transformed_grainline: tuple[IntPoint, IntPoint]
    bbox: tuple[int, int, int, int]
    geometry_hash: str
    sequence: int


@dataclass(frozen=True)
class ValidationReport:
    status: str
    checks: dict[str, bool]
    errors: tuple[str, ...]
    pair_checks: int


@dataclass(frozen=True)
class MarkerResult:
    status: str
    search_status: str
    marker_length: int | None
    usable_width: int
    physical_width: int
    placements: tuple[Placement, ...]
    piece_area_total: int
    marker_area: int | None
    waste_area: int | None
    efficiency_percentage: float | None
    waste_percentage: float | None
    lower_bound_length: int
    area_lower_bound: int
    gap_to_area_lower_bound: int | None
    validation: ValidationReport
    algorithm: str
    algorithm_version: str
    nfp_status: str
    seed: int
    piece_order_strategy: str
    candidate_order: str
    transform_order: tuple[int, ...]
    evaluation_count: int
    stopping_reason: str
    elapsed_time_ms: float
    input_hash: str
    result_hash: str
    cache_hits: int
    cache_misses: int
    diagnostics: tuple[str, ...]
    debug_geometry: dict[str, Any] | None = None
