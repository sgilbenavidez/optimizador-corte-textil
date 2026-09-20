from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


NonNegativeStrictInt = Annotated[StrictInt, Field(ge=0)]


class MeasurementResponse(BaseModel):
    measurement_code: str
    measurement_kind: str
    value: float
    unit: str
    provenance_status: str
    source_reference: str
    derivation: str | None
    version: str


class SizeResponse(BaseModel):
    id: str
    code: str
    label: str
    display_order: int
    measurements: list[MeasurementResponse] = []


class GarmentModelVersionResponse(BaseModel):
    id: str
    model_code: str
    version_code: str
    display_name: str
    market_profile: str
    lifecycle_status: str
    validation_status: str
    is_orderable: bool
    unit: str
    content_hash: str
    provenance: dict
    warning: str | None
    sizes: list[SizeResponse] = []


class GarmentModelSummaryResponse(BaseModel):
    id: str
    code: str
    display_name: str
    garment_type_code: str
    versions: list[GarmentModelVersionResponse]


class FabricConfigurationResponse(BaseModel):
    id: str
    version_code: str
    display_name: str
    physical_width_cm: float
    usable_width_cm: float
    fabric_family: str
    directional: bool
    lay_mode: str
    lay_face_mode: str
    marker_direction_policy: str
    fabric_directionality: str
    piece_clearance_cm: float
    left_margin_cm: float
    right_margin_cm: float
    start_margin_cm: float
    end_margin_cm: float
    content_hash: str


class CuttingTableConfigurationResponse(BaseModel):
    id: str
    version_code: str
    display_name: str
    physical_length_cm: float
    usable_length_cm: float
    max_layers: int
    content_hash: str


class DemandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_code: str = Field(min_length=1, max_length=16)
    quantity: NonNegativeStrictInt

    @field_validator("size_code")
    @classmethod
    def normalize_size_code(cls, value: str) -> str:
        return value.strip().upper()


class ProductionOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    garment_model_version_id: str
    fabric_configuration_id: str
    cutting_table_configuration_id: str
    demand: list[DemandInput]

    @model_validator(mode="after")
    def reject_duplicate_sizes(self) -> "ProductionOrderCreate":
        codes = [line.size_code for line in self.demand]
        if len(codes) != len(set(codes)):
            raise ValueError("No se permiten tallas duplicadas.")
        return self


class OrderDemandResponse(BaseModel):
    size_code: str
    quantity: int


class ProductionOrderResponse(BaseModel):
    id: str
    status: str
    garment_model_version_id: str
    pattern_set_version_id: str | None = None
    fabric_configuration_id: str
    cutting_table_configuration_id: str
    total_quantity: int
    demand: list[OrderDemandResponse]
    garment_model: dict
    fabric_configuration: dict
    cutting_table_configuration: dict
    optimization_policy: dict
    snapshot_hash: str
    created_at: datetime


class PatternPieceSummaryResponse(BaseModel):
    id: str
    size_code: str
    piece_code: str
    quantity: int
    grainline: dict
    allowed_rotations_degrees: list[int]
    mirror_allowed: bool
    edge_allowances_cm: dict[str, float]
    technical_measurements: dict[str, float]
    geometry_metrics: dict
    geometry_hash: str


class PatternSetResponse(BaseModel):
    id: str
    garment_model_version_id: str
    parameter_profile_id: str
    version: int
    version_code: str
    algorithm_version: str
    measurement_snapshot_hash: str
    content_hash: str
    lifecycle_status: str
    validation_status: str
    unit: str
    geometry_units_per_cm: int
    warning: str
    created_at: datetime
    pieces: list[PatternPieceSummaryResponse]


class PatternPieceGeometryResponse(PatternPieceSummaryResponse):
    pattern_set_version_id: str
    source_geometry: dict
    seamline_geometry: dict
    cutline_geometry: dict
    operational_geometry: dict


class MarkerCompositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_code: str = Field(min_length=1, max_length=16)
    quantity: Annotated[StrictInt, Field(ge=1, le=100)]

    @field_validator("size_code")
    @classmethod
    def normalize_marker_size(cls, value: str) -> str:
        return value.strip().upper()


class MarkerPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern_set_version_id: str
    composition: list[MarkerCompositionInput] = Field(min_length=1)
    fabric_configuration_id: str
    cutting_table_configuration_id: str | None = None
    deterministic: bool = True
    seed: int = 1
    evaluation_budget: Annotated[StrictInt, Field(ge=100, le=500_000)] = 100_000
    debug: bool = False
    transform_lab_mode: bool = False

    @model_validator(mode="after")
    def unique_marker_sizes(self) -> "MarkerPreviewRequest":
        codes = [item.size_code for item in self.composition]
        if len(codes) != len(set(codes)):
            raise ValueError("No se permiten tallas duplicadas en la composición.")
        return self


class MarkerPreviewResponse(BaseModel):
    status: str
    search_status: str
    warning: str
    marker_length_cm: float | None
    usable_width_cm: float
    physical_width_cm: float
    piece_count: int
    placements: list[dict]
    piece_area_total_cm2: float
    marker_area_cm2: float | None
    waste_area_cm2: float | None
    efficiency_percentage: float | None
    waste_percentage: float | None
    lower_bound_length_cm: float
    area_lower_bound_cm: float
    gap_to_area_lower_bound_cm: float | None
    validation: dict
    algorithm: str
    algorithm_version: str
    nfp_status: str
    seed: int
    piece_order_strategy: str
    candidate_order: str
    transform_order: list[int]
    evaluation_count: int
    stopping_reason: str
    elapsed_time_ms: float
    input_hash: str
    result_hash: str
    cache: dict
    diagnostics: list[str]
    margins_cm: dict[str, float]
    clearance_cm: float
    max_length_cm: float
    debug_geometry: dict | None


class OptimizationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_overproduction: bool = True
    overproduction_rate: float = Field(default=0.03, ge=0, le=1)
    time_limit_seconds: int = Field(default=120, ge=5, le=600)
    max_garments_per_marker: int = Field(default=15, ge=1, le=15)
    max_distinct_sizes_per_marker: int = Field(default=5, ge=1, le=7)
    max_candidate_compositions: int = Field(default=48, ge=1, le=200)
    max_rounds: int = Field(default=2, ge=1, le=5)
    max_marker_candidates: int = Field(default=24, ge=1, le=100)
    geometry_evaluation_budget_per_candidate: int = Field(default=25_000, ge=100, le=500_000)
    global_geometry_budget_seconds: float = Field(default=75, ge=1, le=500)
    planner_time_limit_seconds: float = Field(default=30, ge=1, le=300)
    fast_plan_budget_seconds: float = Field(default=5, ge=0.1, le=60)
    candidate_generation_budget_seconds: float = Field(default=5, ge=0.1, le=120)
    geometry_budget_seconds: float = Field(default=45, ge=1, le=500)
    planning_budget_seconds: float = Field(default=25, ge=0.1, le=300)
    total_budget_seconds: float = Field(default=120, ge=5, le=600)
    geometry_top_k_initial: int = Field(default=20, ge=1, le=100)
    geometry_top_k_per_round: int = Field(default=10, ge=1, le=100)
    beam_width: int = Field(default=10, ge=1, le=100)
    no_improvement_rounds: int = Field(default=1, ge=1, le=10)
    planner_refinement_engine: Literal["heuristic", "cp_sat", "hybrid"] | None = None
    joint_optimization_enabled: bool | None = None
    persistent_catalog_bootstrap_enabled: bool | None = None
    seed: int = 1


class OptimizationRunResponse(BaseModel):
    id: str
    production_order_id: str
    status: str
    phase: str
    input_hash: str
    configuration: dict
    cancel_requested: bool
    progress: dict
    elapsed: dict
    solution_count: int
    error_detail: str | None
    error_code: str | None = None
    failure_phase: str | None = None
    request_id: str | None = None
    updated_at: datetime
    elapsed_ms: float
    round_current: int = 0
    round_total_if_known: int | None = None
    best_solution_available: bool = False
    incumbent: dict | None = None
    use_current_plan_requested: bool = False
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class OptimizationSolutionSummaryResponse(BaseModel):
    id: str
    solution_hash: str
    profiles: list[str]
    rank: int
    planning_status: str
    planning_optimality: str
    solution_origin: str
    metrics: dict
    validation: dict
    explanation: str
    recommended: bool = False


class PageResponse(BaseModel):
    items: list[dict]
    page: int
    page_size: int
    total: int
    pages: int


class OptimizationSolutionResponse(OptimizationSolutionSummaryResponse):
    run_id: str
    size_results: list[dict]
    spreads: list[dict]


class SpreadResponse(BaseModel):
    id: str
    solution_id: str
    marker_hash: str
    spread_hash: str
    sequence: int
    layers: int
    repeats: int
    composition: dict
    production_by_size: dict
    marker_length_cm: float
    fabric_consumption_m: float
    marker_efficiency_percentage: float
    marker_search_status: str
    validation_status: str
    useful_garments: int = 0
    order_coverage_percentage: float = 0
    remaining_demand_after: dict = Field(default_factory=dict)
    is_primary: bool = False


class MarkerArtifactResponse(BaseModel):
    marker_hash: str
    composition: dict
    geometry_engine_version: str
    marker_search_status: str
    validation_status: str
    marker: dict


class HealthResponse(BaseModel):
    status: str
