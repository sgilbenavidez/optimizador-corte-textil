from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from costura_optima.infrastructure.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GarmentTypeORM(Base):
    __tablename__ = "garment_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GarmentModelORM(Base):
    __tablename__ = "garment_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    garment_type_id: Mapped[str] = mapped_column(ForeignKey("garment_types.id"))
    code: Mapped[str] = mapped_column(String(96), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    origin_type: Mapped[str] = mapped_column(String(32), default="STANDARD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    versions: Mapped[list["GarmentModelVersionORM"]] = relationship(back_populates="model")


class GarmentModelVersionORM(Base):
    __tablename__ = "garment_model_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    garment_model_id: Mapped[str] = mapped_column(ForeignKey("garment_models.id"))
    version_code: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    market_profile: Mapped[str] = mapped_column(String(96))
    lifecycle_status: Mapped[str] = mapped_column(String(40))
    validation_status: Mapped[str] = mapped_column(String(64))
    is_orderable: Mapped[bool] = mapped_column(Boolean, default=False)
    unit: Mapped[str] = mapped_column(String(16), default="cm")
    content_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    model: Mapped[GarmentModelORM] = relationship(back_populates="versions")
    sizes: Mapped[list["SizeDefinitionORM"]] = relationship(back_populates="version", order_by="SizeDefinitionORM.display_order")


class SizeDefinitionORM(Base):
    __tablename__ = "size_definitions"
    __table_args__ = (
        UniqueConstraint("garment_model_version_id", "code", name="uq_size_version_code"),
        UniqueConstraint("garment_model_version_id", "display_order", name="uq_size_version_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    garment_model_version_id: Mapped[str] = mapped_column(ForeignKey("garment_model_versions.id"))
    code: Mapped[str] = mapped_column(String(16))
    display_order: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(32))
    version: Mapped[GarmentModelVersionORM] = relationship(back_populates="sizes")
    measurements: Mapped[list["MeasurementValueORM"]] = relationship(back_populates="size", order_by="MeasurementValueORM.measurement_code")


class MeasurementValueORM(Base):
    __tablename__ = "measurement_values"
    __table_args__ = (UniqueConstraint("size_definition_id", "measurement_code", name="uq_measurement_size_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    size_definition_id: Mapped[str] = mapped_column(ForeignKey("size_definitions.id"))
    measurement_code: Mapped[str] = mapped_column(String(80))
    measurement_kind: Mapped[str] = mapped_column(String(24))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    unit: Mapped[str] = mapped_column(String(16))
    provenance_status: Mapped[str] = mapped_column(String(32))
    source_reference: Mapped[str] = mapped_column(String(255))
    derivation: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str] = mapped_column(String(32))
    size: Mapped[SizeDefinitionORM] = relationship(back_populates="measurements")


class FabricConfigurationORM(Base):
    __tablename__ = "fabric_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_code: Mapped[str] = mapped_column(String(96), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    physical_width_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    usable_width_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    fabric_family: Mapped[str] = mapped_column(String(64))
    directional: Mapped[bool] = mapped_column(Boolean)
    lay_mode: Mapped[str] = mapped_column(String(64))
    lay_face_mode: Mapped[str] = mapped_column(String(32), default="FACE_ONE_WAY")
    marker_direction_policy: Mapped[str] = mapped_column(String(32), default="TWO_WAY")
    fabric_directionality: Mapped[str] = mapped_column(String(32), default="NON_DIRECTIONAL")
    piece_clearance_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    left_margin_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    right_margin_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    start_margin_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    end_margin_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    content_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CuttingTableConfigurationORM(Base):
    __tablename__ = "cutting_table_configurations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version_code: Mapped[str] = mapped_column(String(96), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    physical_length_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    usable_length_cm: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    max_layers: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatternParameterProfileORM(Base):
    __tablename__ = "pattern_parameter_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True)
    version: Mapped[int] = mapped_column(Integer)
    parameters: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    lifecycle_status: Mapped[str] = mapped_column(String(40))
    validation_status: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatternSetVersionORM(Base):
    __tablename__ = "pattern_set_versions"
    __table_args__ = (
        UniqueConstraint("garment_model_version_id", "version", name="uq_pattern_set_model_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    garment_model_version_id: Mapped[str] = mapped_column(ForeignKey("garment_model_versions.id"))
    parameter_profile_id: Mapped[str] = mapped_column(ForeignKey("pattern_parameter_profiles.id"))
    version: Mapped[int] = mapped_column(Integer)
    version_code: Mapped[str] = mapped_column(String(160), unique=True)
    algorithm_version: Mapped[str] = mapped_column(String(128))
    measurement_snapshot_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    lifecycle_status: Mapped[str] = mapped_column(String(40))
    validation_status: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(16))
    geometry_units_per_cm: Mapped[int] = mapped_column(Integer)
    warning: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pieces: Mapped[list["PatternPieceORM"]] = relationship(back_populates="pattern_set", order_by="PatternPieceORM.size_code, PatternPieceORM.piece_code")


class PatternPieceORM(Base):
    __tablename__ = "pattern_pieces"
    __table_args__ = (
        UniqueConstraint("pattern_set_version_id", "size_code", "piece_code", name="uq_pattern_piece_set_size_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pattern_set_version_id: Mapped[str] = mapped_column(ForeignKey("pattern_set_versions.id"))
    size_code: Mapped[str] = mapped_column(String(16))
    piece_code: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[int] = mapped_column(Integer)
    source_geometry: Mapped[dict] = mapped_column(JSON)
    seamline_geometry: Mapped[dict] = mapped_column(JSON)
    cutline_geometry: Mapped[dict] = mapped_column(JSON)
    operational_geometry: Mapped[dict] = mapped_column(JSON)
    grainline: Mapped[dict] = mapped_column(JSON)
    allowed_rotations_degrees: Mapped[list] = mapped_column(JSON)
    mirror_allowed: Mapped[bool] = mapped_column(Boolean)
    edge_allowances_cm: Mapped[dict] = mapped_column(JSON)
    technical_measurements: Mapped[dict] = mapped_column(JSON)
    geometry_metrics: Mapped[dict] = mapped_column(JSON)
    geometry_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pattern_set: Mapped[PatternSetVersionORM] = relationship(back_populates="pieces")


class ProductionOrderORM(Base):
    __tablename__ = "production_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    garment_model_version_id: Mapped[str] = mapped_column(ForeignKey("garment_model_versions.id"))
    pattern_set_version_id: Mapped[str | None] = mapped_column(ForeignKey("pattern_set_versions.id"), nullable=True)
    fabric_configuration_id: Mapped[str] = mapped_column(ForeignKey("fabric_configurations.id"))
    cutting_table_configuration_id: Mapped[str] = mapped_column(ForeignKey("cutting_table_configurations.id"))
    status: Mapped[str] = mapped_column(String(40))
    total_quantity: Mapped[int] = mapped_column(Integer)
    catalog_snapshot: Mapped[dict] = mapped_column(JSON)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    demands: Mapped[list["ProductionOrderDemandORM"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="ProductionOrderDemandORM.size_code"
    )
    optimization_runs: Mapped[list["OptimizationRunORM"]] = relationship(back_populates="order")


class ProductionOrderDemandORM(Base):
    __tablename__ = "production_order_demands"
    __table_args__ = (UniqueConstraint("production_order_id", "size_code", name="uq_order_demand_size"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    production_order_id: Mapped[str] = mapped_column(ForeignKey("production_orders.id", ondelete="CASCADE"))
    size_code: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    order: Mapped[ProductionOrderORM] = relationship(back_populates="demands")


class MarkerArtifactORM(Base):
    __tablename__ = "marker_artifacts"

    marker_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    content_key: Mapped[str] = mapped_column(String(64), unique=True)
    pattern_hash: Mapped[str] = mapped_column(String(64))
    fabric_hash: Mapped[str] = mapped_column(String(64))
    table_hash: Mapped[str] = mapped_column(String(64))
    composition: Mapped[dict] = mapped_column(JSON)
    marker_payload: Mapped[dict] = mapped_column(JSON)
    geometry_engine_version: Mapped[str] = mapped_column(String(128))
    marker_search_status: Mapped[str] = mapped_column(String(64))
    validation_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OptimizationRunORM(Base):
    __tablename__ = "optimization_runs"
    __table_args__ = (UniqueConstraint("production_order_id", "idempotency_key", name="uq_run_order_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    production_order_id: Mapped[str] = mapped_column(ForeignKey("production_orders.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    config_hash: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(40))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    candidates_generated: Mapped[int] = mapped_column(Integer, default=0)
    candidates_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    candidates_pending: Mapped[int] = mapped_column(Integer, default=0)
    candidates_feasible: Mapped[int] = mapped_column(Integer, default=0)
    candidates_infeasible: Mapped[int] = mapped_column(Integer, default=0)
    candidates_not_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    best_feasible_found: Mapped[bool] = mapped_column(Boolean, default=False)
    use_current_plan_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    first_solution_elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_solution_spreads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_solution_fabric_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    final_solution_elapsed_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_solution_spreads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_solution_fabric_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    configuration: Mapped[dict] = mapped_column(JSON)
    audit: Mapped[dict] = mapped_column(JSON, default=dict)
    elapsed_candidate_generation_ms: Mapped[float] = mapped_column(Float, default=0)
    elapsed_geometry_ms: Mapped[float] = mapped_column(Float, default=0)
    elapsed_planner_ms: Mapped[float] = mapped_column(Float, default=0)
    elapsed_total_ms: Mapped[float] = mapped_column(Float, default=0)
    worker_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_phase: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    round_current: Mapped[int] = mapped_column(Integer, default=0)
    round_total_if_known: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    order: Mapped[ProductionOrderORM] = relationship(back_populates="optimization_runs")
    candidates: Mapped[list["OptimizationCandidateORM"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    solutions: Mapped[list["OptimizationSolutionORM"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class OptimizationCandidateORM(Base):
    __tablename__ = "optimization_candidates"
    __table_args__ = (UniqueConstraint("optimization_run_id", "candidate_hash", name="uq_run_candidate_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    optimization_run_id: Mapped[str] = mapped_column(ForeignKey("optimization_runs.id", ondelete="CASCADE"))
    candidate_hash: Mapped[str] = mapped_column(String(64))
    composition: Mapped[dict] = mapped_column(JSON)
    round_number: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    marker_hash: Mapped[str | None] = mapped_column(ForeignKey("marker_artifacts.marker_hash"), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    geometry_elapsed_ms: Mapped[float] = mapped_column(Float, default=0)
    diagnostics: Mapped[list] = mapped_column(JSON, default=list)
    run: Mapped[OptimizationRunORM] = relationship(back_populates="candidates")


class OptimizationSolutionORM(Base):
    __tablename__ = "optimization_solutions"
    __table_args__ = (UniqueConstraint("optimization_run_id", "fingerprint", name="uq_run_solution_fingerprint"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    optimization_run_id: Mapped[str] = mapped_column(ForeignKey("optimization_runs.id", ondelete="CASCADE"))
    solution_hash: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer)
    planning_status: Mapped[str] = mapped_column(String(32))
    planning_optimality: Mapped[str] = mapped_column(String(40))
    solution_origin: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON)
    validation_certificate: Mapped[dict] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run: Mapped[OptimizationRunORM] = relationship(back_populates="solutions")
    profiles: Mapped[list["OptimizationSolutionProfileORM"]] = relationship(back_populates="solution", cascade="all, delete-orphan")
    spreads: Mapped[list["SpreadORM"]] = relationship(back_populates="solution", cascade="all, delete-orphan")
    size_results: Mapped[list["SizeResultORM"]] = relationship(back_populates="solution", cascade="all, delete-orphan")


class OptimizationSolutionProfileORM(Base):
    __tablename__ = "optimization_solution_profiles"
    __table_args__ = (UniqueConstraint("optimization_solution_id", "profile", name="uq_solution_profile"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    optimization_solution_id: Mapped[str] = mapped_column(ForeignKey("optimization_solutions.id", ondelete="CASCADE"))
    profile: Mapped[str] = mapped_column(String(32))
    objective_stages: Mapped[list] = mapped_column(JSON)
    solution: Mapped[OptimizationSolutionORM] = relationship(back_populates="profiles")


class SpreadORM(Base):
    __tablename__ = "spreads"
    __table_args__ = (UniqueConstraint("optimization_solution_id", "spread_hash", name="uq_solution_spread_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    optimization_solution_id: Mapped[str] = mapped_column(ForeignKey("optimization_solutions.id", ondelete="CASCADE"))
    marker_hash: Mapped[str] = mapped_column(ForeignKey("marker_artifacts.marker_hash"))
    spread_hash: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    layers: Mapped[int] = mapped_column(Integer)
    repeats: Mapped[int] = mapped_column(Integer)
    composition: Mapped[dict] = mapped_column(JSON)
    production_by_size: Mapped[dict] = mapped_column(JSON)
    marker_length_units: Mapped[int] = mapped_column(BigInteger)
    fabric_consumption_units: Mapped[int] = mapped_column(BigInteger)
    marker_efficiency_percentage: Mapped[float] = mapped_column(Float)
    marker_search_status: Mapped[str] = mapped_column(String(64))
    validation_status: Mapped[str] = mapped_column(String(32))
    useful_garments: Mapped[int] = mapped_column(Integer, default=0)
    order_coverage_percentage: Mapped[float] = mapped_column(Float, default=0)
    remaining_demand_after: Mapped[dict] = mapped_column(JSON, default=dict)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    solution: Mapped[OptimizationSolutionORM] = relationship(back_populates="spreads")


class SizeResultORM(Base):
    __tablename__ = "size_results"
    __table_args__ = (UniqueConstraint("optimization_solution_id", "size_code", name="uq_solution_size"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    optimization_solution_id: Mapped[str] = mapped_column(ForeignKey("optimization_solutions.id", ondelete="CASCADE"))
    size_code: Mapped[str] = mapped_column(String(16))
    requested: Mapped[int] = mapped_column(Integer)
    produced: Mapped[int] = mapped_column(Integer)
    overproduction: Mapped[int] = mapped_column(Integer)
    solution: Mapped[OptimizationSolutionORM] = relationship(back_populates="size_results")
