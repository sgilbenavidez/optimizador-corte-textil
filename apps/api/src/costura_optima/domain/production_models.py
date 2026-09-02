from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


Composition = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class CandidateGenerationConfig:
    max_garments_per_marker: int = 3
    max_distinct_sizes_per_marker: int = 3
    max_candidate_compositions: int = 24
    max_rounds: int = 2


@dataclass(frozen=True)
class PlanningConfig:
    max_layers: int = 30
    time_limit_seconds: float = 30.0
    deterministic: bool = True
    seed: int = 1


@dataclass(frozen=True)
class CandidateComposition:
    composition: Composition
    round_number: int
    origin: str
    area_lower_bound_units: int
    candidate_hash: str


@dataclass(frozen=True)
class CandidateGenerationResult:
    candidates: tuple[CandidateComposition, ...]
    pruned: tuple[dict[str, Any], ...]
    elapsed_ms: float


@dataclass(frozen=True)
class ValidatedMarkerCandidate:
    marker_hash: str
    content_key: str
    composition: Composition
    marker_length_units: int
    usable_width_units: int
    piece_area_units2: int
    marker_area_units2: int
    waste_area_units2: int
    efficiency_percentage: float
    placements: tuple[dict[str, Any], ...]
    validation_certificate: dict[str, Any]
    geometry_engine_version: str
    marker_search_status: str
    input_hash: str
    lower_bound_length_units: int


@dataclass(frozen=True)
class PlannedSpread:
    marker_hash: str
    composition: Composition
    layers: int
    repeats: int
    marker_length_units: int
    fabric_consumption_units: int
    production_by_size: dict[str, int]
    marker_efficiency_percentage: float
    marker_search_status: str
    spread_hash: str


@dataclass(frozen=True)
class PlanningSolution:
    profiles: tuple[str, ...]
    planning_status: str
    planning_optimality: str
    solution_origin: str
    spreads: tuple[PlannedSpread, ...]
    requested_by_size: dict[str, int]
    produced_by_size: dict[str, int]
    overproduction_by_size: dict[str, int]
    total_overproduction: int
    max_overproduction: int
    total_fabric_units: int
    total_waste_units2: int
    spread_count: int
    global_efficiency_percentage: float
    objective_stages: tuple[dict[str, Any], ...]
    variable_count: int
    constraint_count: int
    solver_time_ms: float
    fingerprint: str
    solution_hash: str
    explanation: str = ""


@dataclass(frozen=True)
class PlanValidationReport:
    status: str
    checks: dict[str, bool]
    errors: tuple[str, ...]
    recomputed: dict[str, Any] = field(default_factory=dict)
