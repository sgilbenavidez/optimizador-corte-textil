import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from costura_optima.application.schemas import (
    CuttingTableConfigurationResponse,
    FabricConfigurationResponse,
    GarmentModelSummaryResponse,
    GarmentModelVersionResponse,
    MeasurementResponse,
    OrderDemandResponse,
    PatternPieceGeometryResponse,
    PatternPieceSummaryResponse,
    PatternSetResponse,
    MarkerPreviewRequest,
    MarkerPreviewResponse,
    OptimizationRunCreate,
    OptimizationRunResponse,
    OptimizationSolutionResponse,
    OptimizationSolutionSummaryResponse,
    SpreadResponse,
    MarkerArtifactResponse,
    ProductionOrderCreate,
    ProductionOrderResponse,
    SizeResponse,
)
from costura_optima.domain.enums import LifecycleStatus, OrderStatus
from costura_optima.domain.errors import NotFoundError, ValidationError
from costura_optima.domain.order_rules import DemandLine, normalize_demand
from costura_optima.domain.integer_kernel import canonical_json_hash, canonical_path, close_path
from costura_optima.domain.nesting_engine import DeterministicNestingEngine
from costura_optima.domain.nesting_models import MarkerMargins, MarkerRequest, NestingPiece, PieceInstance, PrecisionConfiguration
from costura_optima.infrastructure.db_models import (
    CuttingTableConfigurationORM,
    FabricConfigurationORM,
    GarmentModelORM,
    GarmentModelVersionORM,
    ProductionOrderDemandORM,
    ProductionOrderORM,
    OptimizationRunORM,
    OptimizationSolutionORM,
    SpreadORM,
    MarkerArtifactORM,
    OptimizationCandidateORM,
)
from costura_optima.infrastructure.repositories import CatalogRepository, OrderRepository, PatternRepository
from costura_optima.worker.queue import cancel_queued_job, enqueue_optimization_run


EXPERIMENTAL_WARNING = "Patrón experimental de ingeniería — no validado para producción."
NESTING_WARNING = "Laboratorio geométrico — no es todavía un plan de producción."
SIZE_ORDER = {code: index for index, code in enumerate(["XS", "S", "M", "L", "XL", "XXL", "XXXL"])}
MARKER_ENGINE = DeterministicNestingEngine()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: dict) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decimal(value: Decimal) -> float:
    return float(value)


def serialize_measurement(measurement) -> dict:
    return {
        "measurement_code": measurement.measurement_code,
        "measurement_kind": measurement.measurement_kind,
        "value": _decimal(measurement.value),
        "unit": measurement.unit,
        "provenance_status": measurement.provenance_status,
        "source_reference": measurement.source_reference,
        "derivation": measurement.derivation,
        "version": measurement.version,
    }


def serialize_version(version: GarmentModelVersionORM, include_measurements: bool = True) -> dict:
    warning = EXPERIMENTAL_WARNING if version.lifecycle_status == LifecycleStatus.ENGINEERING else None
    sizes = []
    for size in sorted(version.sizes, key=lambda item: item.display_order):
        sizes.append(
            {
                "id": size.id,
                "code": size.code,
                "label": size.label,
                "display_order": size.display_order,
                "measurements": [serialize_measurement(item) for item in size.measurements] if include_measurements else [],
            }
        )
    return {
        "id": version.id,
        "model_code": version.model.code,
        "version_code": version.version_code,
        "display_name": version.display_name,
        "market_profile": version.market_profile,
        "lifecycle_status": version.lifecycle_status,
        "validation_status": version.validation_status,
        "is_orderable": version.is_orderable,
        "unit": version.unit,
        "content_hash": version.content_hash,
        "provenance": version.provenance,
        "warning": warning,
        "sizes": sizes,
    }


def serialize_fabric(config: FabricConfigurationORM) -> dict:
    return {
        "id": config.id,
        "version_code": config.version_code,
        "display_name": config.display_name,
        "physical_width_cm": _decimal(config.physical_width_cm),
        "usable_width_cm": _decimal(config.usable_width_cm),
        "fabric_family": config.fabric_family,
        "directional": config.directional,
        "lay_mode": config.lay_mode,
        "piece_clearance_cm": _decimal(config.piece_clearance_cm),
        "left_margin_cm": _decimal(config.left_margin_cm),
        "right_margin_cm": _decimal(config.right_margin_cm),
        "start_margin_cm": _decimal(config.start_margin_cm),
        "end_margin_cm": _decimal(config.end_margin_cm),
        "content_hash": config.content_hash,
    }


def serialize_table(config: CuttingTableConfigurationORM) -> dict:
    return {
        "id": config.id,
        "version_code": config.version_code,
        "display_name": config.display_name,
        "physical_length_cm": _decimal(config.physical_length_cm),
        "usable_length_cm": _decimal(config.usable_length_cm),
        "max_layers": config.max_layers,
        "content_hash": config.content_hash,
    }


class CatalogService:
    def __init__(self, session: Session):
        self.repository = CatalogRepository(session)

    def list_models(self) -> list[GarmentModelSummaryResponse]:
        result = []
        for model in self.repository.list_models():
            versions = [GarmentModelVersionResponse.model_validate(serialize_version(version, False)) for version in model.versions]
            result.append(
                GarmentModelSummaryResponse(
                    id=model.id,
                    code=model.code,
                    display_name=model.display_name,
                    garment_type_code="T_SHIRT",
                    versions=versions,
                )
            )
        return result

    def get_version(self, version_id: str) -> GarmentModelVersionResponse:
        version = self.repository.get_version(version_id)
        if not version:
            raise NotFoundError("La versión de prenda no existe.")
        return GarmentModelVersionResponse.model_validate(serialize_version(version))

    def get_sizes(self, version_id: str) -> list[SizeResponse]:
        return self.get_version(version_id).sizes

    def list_fabrics(self) -> list[FabricConfigurationResponse]:
        return [FabricConfigurationResponse.model_validate(serialize_fabric(item)) for item in self.repository.list_fabrics()]

    def list_tables(self) -> list[CuttingTableConfigurationResponse]:
        return [CuttingTableConfigurationResponse.model_validate(serialize_table(item)) for item in self.repository.list_tables()]


class OrderService:
    def __init__(self, session: Session):
        self.catalog = CatalogRepository(session)
        self.orders = OrderRepository(session)
        self.patterns = PatternRepository(session)

    def create(self, payload: ProductionOrderCreate) -> ProductionOrderResponse:
        version = self.catalog.get_version(payload.garment_model_version_id)
        if not version:
            raise NotFoundError("La versión de prenda no existe.")
        if not version.is_orderable:
            raise ValidationError("La versión de prenda no está habilitada para órdenes.")

        fabric = self.catalog.get_fabric(payload.fabric_configuration_id)
        if not fabric or not fabric.is_active:
            raise NotFoundError("La configuración de tela no existe o está inactiva.")
        table = self.catalog.get_table(payload.cutting_table_configuration_id)
        if not table or not table.is_active:
            raise NotFoundError("La configuración de mesa no existe o está inactiva.")

        allowed_sizes = {size.code for size in version.sizes}
        positive_demand = normalize_demand(
            [DemandLine(line.size_code, line.quantity) for line in payload.demand], allowed_sizes
        )
        garment_snapshot = serialize_version(version)
        pattern_set = self.patterns.latest_for_model_version(version.id)
        pattern_snapshot = None
        if pattern_set:
            pattern_snapshot = {
                "id": pattern_set.id,
                "version_code": pattern_set.version_code,
                "content_hash": pattern_set.content_hash,
                "measurement_snapshot_hash": pattern_set.measurement_snapshot_hash,
                "algorithm_version": pattern_set.algorithm_version,
                "lifecycle_status": pattern_set.lifecycle_status,
                "validation_status": pattern_set.validation_status,
                "warning": pattern_set.warning,
                "piece_hashes": {f"{p.size_code}:{p.piece_code}": p.geometry_hash for p in pattern_set.pieces},
            }
        snapshot = {
            "schema_version": "production-order-snapshot-v1",
            "demand": dict(sorted(positive_demand.items())),
            "garment_model": garment_snapshot,
            "pattern_set_version": pattern_snapshot,
            "fabric_configuration": serialize_fabric(fabric),
            "cutting_table_configuration": serialize_table(table),
            "transformation_policy": {
                "allowed_rotations_degrees": [0, 180],
                "rotation_180_requires_piece_permission": True,
                "rotation_180_requires_non_directional_fabric": True,
                "rotation_90_allowed": False,
                "mirror_default": False,
            },
            "optimization_policy": {
                "allow_overproduction": True,
                "max_overproduction_rule": "max(2, ceil(demand * 0.03))",
                "zero_demand_production_allowed": False,
                "objective_strategy": "LEXICOGRAPHIC",
                "future_profiles": ["MIN_FABRIC", "MIN_SPREADS", "BALANCED"],
                "time_limit_seconds": 120,
                "timeout_feasible_status": "FEASIBLE_NOT_PROVEN_BEST",
            },
        }
        order = ProductionOrderORM(
            id=str(uuid4()),
            garment_model_version_id=version.id,
            pattern_set_version_id=pattern_set.id if pattern_set else None,
            fabric_configuration_id=fabric.id,
            cutting_table_configuration_id=table.id,
            status=OrderStatus.PREPARED_FOR_OPTIMIZATION,
            total_quantity=sum(positive_demand.values()),
            catalog_snapshot=snapshot,
            snapshot_hash=_hash(snapshot),
        )
        order.demands = [
            ProductionOrderDemandORM(id=str(uuid4()), size_code=code, quantity=quantity)
            for code, quantity in positive_demand.items()
        ]
        return self._to_response(self.orders.add(order))

    def get(self, order_id: str) -> ProductionOrderResponse:
        order = self.orders.get(order_id)
        if not order:
            raise NotFoundError("La orden no existe.")
        return self._to_response(order)

    def list(self) -> list[ProductionOrderResponse]:
        return [self._to_response(order) for order in self.orders.list()]

    @staticmethod
    def _to_response(order: ProductionOrderORM) -> ProductionOrderResponse:
        snapshot = order.catalog_snapshot
        quantities = {line.size_code: line.quantity for line in order.demands}
        demand = [
            OrderDemandResponse(size_code=size["code"], quantity=quantities.get(size["code"], 0))
            for size in sorted(snapshot["garment_model"]["sizes"], key=lambda item: item["display_order"])
        ]
        return ProductionOrderResponse(
            id=order.id,
            status=order.status,
            garment_model_version_id=order.garment_model_version_id,
            pattern_set_version_id=order.pattern_set_version_id,
            fabric_configuration_id=order.fabric_configuration_id,
            cutting_table_configuration_id=order.cutting_table_configuration_id,
            total_quantity=order.total_quantity,
            demand=demand,
            garment_model=snapshot["garment_model"],
            fabric_configuration=snapshot["fabric_configuration"],
            cutting_table_configuration=snapshot["cutting_table_configuration"],
            optimization_policy=snapshot["optimization_policy"],
            snapshot_hash=order.snapshot_hash,
            created_at=order.created_at,
        )


def _piece_summary(piece) -> PatternPieceSummaryResponse:
    return PatternPieceSummaryResponse(
        id=piece.id,
        size_code=piece.size_code,
        piece_code=piece.piece_code,
        quantity=piece.quantity,
        grainline=piece.grainline,
        allowed_rotations_degrees=piece.allowed_rotations_degrees,
        mirror_allowed=piece.mirror_allowed,
        edge_allowances_cm=piece.edge_allowances_cm,
        technical_measurements=piece.technical_measurements,
        geometry_metrics=piece.geometry_metrics,
        geometry_hash=piece.geometry_hash,
    )


def _pattern_set_response(pattern_set, size_code: str | None = None) -> PatternSetResponse:
    pieces = [piece for piece in pattern_set.pieces if size_code is None or piece.size_code == size_code]
    return PatternSetResponse(
        id=pattern_set.id,
        garment_model_version_id=pattern_set.garment_model_version_id,
        parameter_profile_id=pattern_set.parameter_profile_id,
        version=pattern_set.version,
        version_code=pattern_set.version_code,
        algorithm_version=pattern_set.algorithm_version,
        measurement_snapshot_hash=pattern_set.measurement_snapshot_hash,
        content_hash=pattern_set.content_hash,
        lifecycle_status=pattern_set.lifecycle_status,
        validation_status=pattern_set.validation_status,
        unit=pattern_set.unit,
        geometry_units_per_cm=pattern_set.geometry_units_per_cm,
        warning=pattern_set.warning,
        created_at=pattern_set.created_at,
        pieces=[_piece_summary(piece) for piece in pieces],
    )


class PatternService:
    def __init__(self, session: Session):
        self.patterns = PatternRepository(session)

    def list_sets(self, garment_model_version_id: str | None = None) -> list[PatternSetResponse]:
        return [_pattern_set_response(item) for item in self.patterns.list_sets(garment_model_version_id)]

    def get_set(self, pattern_set_id: str, size_code: str | None = None) -> PatternSetResponse:
        item = self.patterns.get_set(pattern_set_id)
        if item is None:
            raise NotFoundError("La versión del patrón no existe.")
        return _pattern_set_response(item, size_code.upper() if size_code else None)

    def get_piece_geometry(self, piece_id: str) -> PatternPieceGeometryResponse:
        piece = self.patterns.get_piece(piece_id)
        if piece is None:
            raise NotFoundError("La pieza del patrón no existe.")
        return PatternPieceGeometryResponse(
            **_piece_summary(piece).model_dump(),
            pattern_set_version_id=piece.pattern_set_version_id,
            source_geometry=piece.source_geometry,
            seamline_geometry=piece.seamline_geometry,
            cutline_geometry=piece.cutline_geometry,
            operational_geometry=piece.operational_geometry,
        )


class MarkerPreviewService:
    def __init__(self, session: Session):
        self.patterns = PatternRepository(session)
        self.catalog = CatalogRepository(session)

    def generate(self, payload: MarkerPreviewRequest) -> MarkerPreviewResponse:
        pattern_set = self.patterns.get_set(payload.pattern_set_version_id)
        if pattern_set is None:
            raise NotFoundError("La versión del patrón no existe.")
        fabric = self.catalog.get_fabric(payload.fabric_configuration_id)
        if fabric is None or not fabric.is_active:
            raise NotFoundError("La configuración de tela no existe o está inactiva.")
        table = (
            self.catalog.get_table(payload.cutting_table_configuration_id)
            if payload.cutting_table_configuration_id
            else next(iter(self.catalog.list_tables()), None)
        )
        if table is None or not table.is_active:
            raise NotFoundError("La configuración de mesa no existe o está inactiva.")
        units = pattern_set.geometry_units_per_cm
        available_sizes = {piece.size_code for piece in pattern_set.pieces}
        requested_sizes = {item.size_code for item in payload.composition}
        unknown = requested_sizes - available_sizes
        if unknown:
            raise ValidationError(f"Tallas sin geometría en el patrón: {', '.join(sorted(unknown))}.")

        pieces_by_size: dict[str, list] = {}
        for piece in pattern_set.pieces:
            pieces_by_size.setdefault(piece.size_code, []).append(piece)
        instances: list[PieceInstance] = []
        for line in sorted(payload.composition, key=lambda item: SIZE_ORDER.get(item.size_code, 999)):
            for piece in sorted(pieces_by_size[line.size_code], key=lambda item: item.piece_code):
                count = line.quantity * piece.quantity
                cut_path = canonical_path(piece.operational_geometry["coordinates"][0])
                grainline = (
                    tuple(round(value * units) for value in piece.grainline["start"]),
                    tuple(round(value * units) for value in piece.grainline["end"]),
                )
                nesting_piece = NestingPiece(
                    pattern_piece_id=piece.id,
                    size_code=piece.size_code,
                    piece_code=piece.piece_code,
                    cut_polygon=cut_path,
                    grainline=grainline,
                    allowed_rotations=tuple(piece.allowed_rotations_degrees),
                    mirror_allowed=piece.mirror_allowed,
                    geometry_hash=piece.geometry_hash,
                )
                for index in range(1, count + 1):
                    instances.append(PieceInstance(f"{piece.size_code}_{piece.piece_code}_{index:03d}", nesting_piece))

        effective_transforms = (0,) if fabric.directional else (0, 180)
        signature = {
            "pattern": pattern_set.content_hash,
            "composition": [line.model_dump() for line in payload.composition],
            "fabric": fabric.content_hash,
            "table": table.content_hash,
            "seed": payload.seed,
        }
        request = MarkerRequest(
            request_id=f"preview-{canonical_json_hash(signature)[:16]}",
            piece_instances=tuple(instances),
            usable_width=round(float(fabric.usable_width_cm) * units),
            physical_width=round(float(fabric.physical_width_cm) * units),
            max_length=round(float(table.usable_length_cm) * units),
            clearance=round(float(fabric.piece_clearance_cm) * units),
            margins=MarkerMargins(
                left=round(float(fabric.left_margin_cm) * units),
                right=round(float(fabric.right_margin_cm) * units),
                start=round(float(fabric.start_margin_cm) * units),
                end=round(float(fabric.end_margin_cm) * units),
            ),
            allowed_transforms=effective_transforms,
            deterministic=payload.deterministic,
            seed=payload.seed,
            evaluation_budget=payload.evaluation_budget,
            debug=payload.debug,
            precision=PrecisionConfiguration(geometry_units_per_cm=units),
        )
        result = MARKER_ENGINE.nest(request)
        scale_area = units * units
        placements = []
        for placement in result.placements:
            placements.append({
                "piece_instance_id": placement.piece_instance_id,
                "pattern_piece_id": placement.pattern_piece_id,
                "size_code": placement.size_code,
                "piece_code": placement.piece_code,
                "transform": {"rotation": placement.rotation, "mirrored": placement.mirrored},
                "translation": {
                    "x_units": placement.translation[0], "y_units": placement.translation[1],
                    "x_cm": placement.translation[0] / units, "y_cm": placement.translation[1] / units,
                },
                "transformed_polygon": {"unit": "geometry_unit", "coordinates": [close_path(placement.transformed_polygon)]},
                "grainline": {"unit": "geometry_unit", "start": placement.transformed_grainline[0], "end": placement.transformed_grainline[1]},
                "bbox": {
                    "units": placement.bbox,
                    "cm": [round(value / units, 4) for value in placement.bbox],
                },
                "geometry_hash": placement.geometry_hash,
                "sequence": placement.sequence,
            })
        margins_cm = {key: value / units for key, value in result_asdict_margins(request.margins).items()}
        return MarkerPreviewResponse(
            status=result.status,
            search_status=result.search_status,
            warning=NESTING_WARNING,
            marker_length_cm=result.marker_length / units if result.marker_length is not None else None,
            usable_width_cm=result.usable_width / units,
            physical_width_cm=result.physical_width / units,
            piece_count=len(result.placements),
            placements=placements,
            piece_area_total_cm2=round(result.piece_area_total / scale_area, 4),
            marker_area_cm2=round(result.marker_area / scale_area, 4) if result.marker_area is not None else None,
            waste_area_cm2=round(result.waste_area / scale_area, 4) if result.waste_area is not None else None,
            efficiency_percentage=result.efficiency_percentage,
            waste_percentage=result.waste_percentage,
            lower_bound_length_cm=result.lower_bound_length / units,
            area_lower_bound_cm=result.area_lower_bound / units,
            gap_to_area_lower_bound_cm=result.gap_to_area_lower_bound / units if result.gap_to_area_lower_bound is not None else None,
            validation={"status": result.validation.status, "checks": result.validation.checks, "errors": result.validation.errors, "pair_checks": result.validation.pair_checks},
            algorithm=result.algorithm,
            algorithm_version=result.algorithm_version,
            nfp_status=result.nfp_status,
            seed=result.seed,
            piece_order_strategy=result.piece_order_strategy,
            candidate_order=result.candidate_order,
            transform_order=list(result.transform_order),
            evaluation_count=result.evaluation_count,
            stopping_reason=result.stopping_reason,
            elapsed_time_ms=result.elapsed_time_ms,
            input_hash=result.input_hash,
            result_hash=result.result_hash,
            cache={"hits": result.cache_hits, "misses": result.cache_misses},
            diagnostics=list(result.diagnostics),
            margins_cm=margins_cm,
            clearance_cm=request.clearance / units,
            max_length_cm=request.max_length / units,
            debug_geometry=result.debug_geometry,
        )


def result_asdict_margins(margins: MarkerMargins) -> dict[str, int]:
    return {"left": margins.left, "right": margins.right, "start": margins.start, "end": margins.end}


class OptimizationRunService:
    def __init__(self, session: Session, enqueuer=None):
        self.session = session
        self.enqueuer = enqueuer or enqueue_optimization_run

    def create(self, order_id: str, payload: OptimizationRunCreate, idempotency_key: str) -> OptimizationRunResponse:
        if not idempotency_key.strip():
            raise ValidationError("Idempotency-Key es obligatorio.")
        order = self.session.scalar(
            select(ProductionOrderORM).where(ProductionOrderORM.id == order_id)
            .options(selectinload(ProductionOrderORM.demands))
        )
        if order is None:
            raise NotFoundError("La orden no existe.")
        configuration = {
            "allow_overproduction": payload.allow_overproduction,
            "overproduction_rate": payload.overproduction_rate,
            "time_limit_seconds": payload.time_limit_seconds,
            "candidate_generation": {
                "max_garments_per_marker": payload.max_garments_per_marker,
                "max_distinct_sizes_per_marker": payload.max_distinct_sizes_per_marker,
                "max_candidate_compositions": payload.max_candidate_compositions,
                "max_rounds": payload.max_rounds,
            },
            "max_marker_candidates": payload.max_marker_candidates,
            "geometry_evaluation_budget_per_candidate": payload.geometry_evaluation_budget_per_candidate,
            "global_geometry_budget_seconds": payload.global_geometry_budget_seconds,
            "planner_time_limit_seconds": payload.planner_time_limit_seconds,
            "objective_profiles": ["MIN_FABRIC", "MIN_SPREADS", "BALANCED"],
            "length_scale": "geometry_units_1000_per_cm",
            "seed": payload.seed,
        }
        config_hash = canonical_json_hash(configuration)
        existing = self.session.scalar(
            select(OptimizationRunORM).where(
                OptimizationRunORM.production_order_id == order_id,
                OptimizationRunORM.idempotency_key == idempotency_key,
            )
        )
        if existing:
            if existing.config_hash != config_hash:
                raise ValidationError("La Idempotency-Key ya fue usada con otra configuración.")
            return self._run_response(existing)
        snapshot = order.catalog_snapshot
        input_payload = {
            "order_snapshot_hash": order.snapshot_hash,
            "pattern_hash": (snapshot.get("pattern_set_version") or {}).get("content_hash"),
            "fabric_hash": snapshot["fabric_configuration"]["content_hash"],
            "table_hash": snapshot["cutting_table_configuration"]["content_hash"],
            "configuration": configuration,
        }
        run = OptimizationRunORM(
            id=str(uuid4()), production_order_id=order.id, idempotency_key=idempotency_key,
            config_hash=config_hash, input_hash=canonical_json_hash(input_payload), status="QUEUED", phase="GENERATING_CANDIDATES",
            cancel_requested=False, candidates_generated=0, candidates_evaluated=0, candidates_pending=0,
            candidates_feasible=0, candidates_infeasible=0, candidates_not_evaluated=0, best_feasible_found=False,
            configuration=configuration, audit={"order_hash": order.snapshot_hash, "seed": payload.seed},
            elapsed_candidate_generation_ms=0, elapsed_geometry_ms=0, elapsed_planner_ms=0, elapsed_total_ms=0,
        )
        self.session.add(run); self.session.commit()
        try:
            run.worker_job_id = self.enqueuer(run.id, payload.time_limit_seconds + 60)
            self.session.commit()
        except Exception as error:
            run.status = "FAILED"; run.error_detail = f"No fue posible encolar la corrida: {type(error).__name__}"
            self.session.commit()
            raise ValidationError("No fue posible iniciar el worker de optimización.") from error
        return self._run_response(run)

    def get(self, run_id: str) -> OptimizationRunResponse:
        run = self.session.get(OptimizationRunORM, run_id)
        if run is None:
            raise NotFoundError("La corrida de optimización no existe.")
        return self._run_response(run)

    def cancel(self, run_id: str) -> OptimizationRunResponse:
        run = self.session.get(OptimizationRunORM, run_id)
        if run is None:
            raise NotFoundError("La corrida de optimización no existe.")
        if run.status in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return self._run_response(run)
        run.cancel_requested = True
        if run.status == "QUEUED":
            cancel_queued_job(run.worker_job_id or run.id)
            run.status = "CANCELLED"; run.finished_at = datetime.now(timezone.utc)
        self.session.commit()
        return self._run_response(run)

    def solutions(self, run_id: str) -> list[OptimizationSolutionSummaryResponse]:
        if self.session.get(OptimizationRunORM, run_id) is None:
            raise NotFoundError("La corrida de optimización no existe.")
        rows = self.session.scalars(
            select(OptimizationSolutionORM).where(OptimizationSolutionORM.optimization_run_id == run_id)
            .options(selectinload(OptimizationSolutionORM.profiles)).order_by(OptimizationSolutionORM.rank)
        ).unique()
        return [OptimizationSolutionSummaryResponse.model_validate(self._solution_summary(row)) for row in rows]

    def solution(self, solution_id: str) -> OptimizationSolutionResponse:
        row = self.session.scalar(
            select(OptimizationSolutionORM).where(OptimizationSolutionORM.id == solution_id).options(
                selectinload(OptimizationSolutionORM.profiles), selectinload(OptimizationSolutionORM.spreads),
                selectinload(OptimizationSolutionORM.size_results),
            )
        )
        if row is None:
            raise NotFoundError("La solución no existe.")
        return OptimizationSolutionResponse.model_validate({
            **self._solution_summary(row), "run_id": row.optimization_run_id,
            "size_results": [
                {"size_code": item.size_code, "requested": item.requested, "produced": item.produced, "overproduction": item.overproduction}
                for item in sorted(row.size_results, key=lambda item: SIZE_ORDER.get(item.size_code, 999))
            ],
            "spreads": [self._spread_dict(item) for item in sorted(row.spreads, key=lambda item: item.sequence)],
        })

    def spreads(self, solution_id: str) -> list[SpreadResponse]:
        if self.session.get(OptimizationSolutionORM, solution_id) is None:
            raise NotFoundError("La solución no existe.")
        rows = self.session.scalars(select(SpreadORM).where(SpreadORM.optimization_solution_id == solution_id).order_by(SpreadORM.sequence))
        return [SpreadResponse.model_validate(self._spread_dict(row)) for row in rows]

    def spread(self, spread_id: str) -> SpreadResponse:
        row = self.session.get(SpreadORM, spread_id)
        if row is None:
            raise NotFoundError("El tendido no existe.")
        return SpreadResponse.model_validate(self._spread_dict(row))

    def marker(self, marker_hash: str) -> MarkerArtifactResponse:
        row = self.session.get(MarkerArtifactORM, marker_hash)
        if row is None:
            raise NotFoundError("El marker no existe.")
        return MarkerArtifactResponse(
            marker_hash=row.marker_hash, composition=row.composition, geometry_engine_version=row.geometry_engine_version,
            marker_search_status=row.marker_search_status, validation_status=row.validation_status, marker=row.marker_payload,
        )

    def audit(self, run_id: str) -> dict:
        run = self.session.get(OptimizationRunORM, run_id)
        if run is None:
            raise NotFoundError("La corrida de optimización no existe.")
        candidates = self.session.scalars(select(OptimizationCandidateORM).where(OptimizationCandidateORM.optimization_run_id == run_id))
        return {
            "run_id": run.id, "input_hash": run.input_hash, "status": run.status, "phase": run.phase,
            "configuration": run.configuration, "audit": run.audit,
            "candidates": [{"hash": item.candidate_hash, "composition": item.composition, "status": item.status,
                            "marker_hash": item.marker_hash, "cache_hit": item.cache_hit, "diagnostics": item.diagnostics}
                           for item in candidates],
            "elapsed": self._elapsed(run),
        }

    @staticmethod
    def _elapsed(run):
        return {"candidate_generation_ms": run.elapsed_candidate_generation_ms, "geometry_ms": run.elapsed_geometry_ms,
                "planner_ms": run.elapsed_planner_ms, "total_ms": run.elapsed_total_ms}

    def _run_response(self, run):
        solution_count = self.session.scalar(select(func.count(OptimizationSolutionORM.id)).where(OptimizationSolutionORM.optimization_run_id == run.id)) or 0
        return OptimizationRunResponse(
            id=run.id, production_order_id=run.production_order_id, status=run.status, phase=run.phase,
            input_hash=run.input_hash, configuration=run.configuration, cancel_requested=run.cancel_requested,
            progress={"candidates_generated": run.candidates_generated, "candidates_evaluated": run.candidates_evaluated,
                      "candidates_pending": run.candidates_pending, "candidates_feasible": run.candidates_feasible,
                      "candidates_infeasible": run.candidates_infeasible, "candidates_not_evaluated": run.candidates_not_evaluated,
                      "best_feasible_found": run.best_feasible_found},
            elapsed=self._elapsed(run), solution_count=solution_count, error_detail=run.error_detail,
            created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
        )

    @staticmethod
    def _solution_summary(row):
        return {"id": row.id, "solution_hash": row.solution_hash, "profiles": sorted(item.profile for item in row.profiles),
                "rank": row.rank, "planning_status": row.planning_status, "planning_optimality": row.planning_optimality,
                "solution_origin": row.solution_origin, "metrics": row.metrics, "validation": row.validation_certificate,
                "explanation": row.explanation}

    @staticmethod
    def _spread_dict(row):
        return {"id": row.id, "solution_id": row.optimization_solution_id, "marker_hash": row.marker_hash,
                "spread_hash": row.spread_hash, "sequence": row.sequence, "layers": row.layers, "repeats": row.repeats,
                "composition": row.composition, "production_by_size": row.production_by_size,
                "marker_length_cm": row.marker_length_units / 1000,
                "fabric_consumption_m": row.fabric_consumption_units / 1000 / 100,
                "marker_efficiency_percentage": row.marker_efficiency_percentage,
                "marker_search_status": row.marker_search_status, "validation_status": row.validation_status}
