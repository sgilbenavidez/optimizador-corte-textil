from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from costura_optima.application.schemas import (
    CuttingTableConfigurationResponse,
    FabricConfigurationResponse,
    GarmentModelSummaryResponse,
    GarmentModelVersionResponse,
    HealthResponse,
    PatternPieceGeometryResponse,
    PatternSetResponse,
    MarkerPreviewRequest,
    MarkerPreviewResponse,
    ProductionOrderCreate,
    ProductionOrderResponse,
    OptimizationRunCreate,
    OptimizationRunResponse,
    OptimizationSolutionResponse,
    OptimizationSolutionSummaryResponse,
    SpreadResponse,
    MarkerArtifactResponse,
    SizeResponse,
)
from costura_optima.application.services import CatalogService, MarkerPreviewService, OptimizationRunService, OrderService, PatternService
from costura_optima.infrastructure.database import get_db_session


router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_db_session)]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/garment-models", response_model=list[GarmentModelSummaryResponse])
def list_garment_models(session: SessionDependency):
    return CatalogService(session).list_models()


@router.get("/garment-model-versions/{version_id}", response_model=GarmentModelVersionResponse)
def get_garment_model_version(version_id: str, session: SessionDependency):
    return CatalogService(session).get_version(version_id)


@router.get("/garment-model-versions/{version_id}/sizes", response_model=list[SizeResponse])
def get_garment_model_version_sizes(version_id: str, session: SessionDependency):
    return CatalogService(session).get_sizes(version_id)


@router.get("/fabric-configurations", response_model=list[FabricConfigurationResponse])
def list_fabric_configurations(session: SessionDependency):
    return CatalogService(session).list_fabrics()


@router.get("/cutting-table-configurations", response_model=list[CuttingTableConfigurationResponse])
def list_cutting_table_configurations(session: SessionDependency):
    return CatalogService(session).list_tables()


@router.get("/pattern-sets", response_model=list[PatternSetResponse])
def list_pattern_sets(session: SessionDependency, garment_model_version_id: str | None = None):
    return PatternService(session).list_sets(garment_model_version_id)


@router.get("/pattern-sets/{pattern_set_id}", response_model=PatternSetResponse)
def get_pattern_set(pattern_set_id: str, session: SessionDependency, size_code: str | None = None):
    return PatternService(session).get_set(pattern_set_id, size_code)


@router.get("/pattern-pieces/{piece_id}/geometry", response_model=PatternPieceGeometryResponse)
def get_pattern_piece_geometry(piece_id: str, session: SessionDependency):
    return PatternService(session).get_piece_geometry(piece_id)


@router.post("/geometry/markers/preview", response_model=MarkerPreviewResponse)
def preview_geometry_marker(payload: MarkerPreviewRequest, session: SessionDependency):
    return MarkerPreviewService(session).generate(payload)


@router.post("/production-orders", response_model=ProductionOrderResponse, status_code=status.HTTP_201_CREATED)
def create_production_order(payload: ProductionOrderCreate, session: SessionDependency):
    return OrderService(session).create(payload)


@router.get("/production-orders", response_model=list[ProductionOrderResponse])
def list_production_orders(session: SessionDependency):
    return OrderService(session).list()


@router.post("/production-orders/{order_id}/optimization-runs", response_model=OptimizationRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_optimization_run(
    order_id: str, payload: OptimizationRunCreate, session: SessionDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    return OptimizationRunService(session).create(order_id, payload, idempotency_key)


@router.get("/optimization-runs/{run_id}", response_model=OptimizationRunResponse)
def get_optimization_run(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).get(run_id)


@router.post("/optimization-runs/{run_id}/cancel", response_model=OptimizationRunResponse)
def cancel_optimization_run(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).cancel(run_id)


@router.get("/optimization-runs/{run_id}/solutions", response_model=list[OptimizationSolutionSummaryResponse])
def list_optimization_solutions(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).solutions(run_id)


@router.get("/optimization-solutions/{solution_id}", response_model=OptimizationSolutionResponse)
def get_optimization_solution(solution_id: str, session: SessionDependency):
    return OptimizationRunService(session).solution(solution_id)


@router.get("/optimization-solutions/{solution_id}/spreads", response_model=list[SpreadResponse])
def list_solution_spreads(solution_id: str, session: SessionDependency):
    return OptimizationRunService(session).spreads(solution_id)


@router.get("/spreads/{spread_id}", response_model=SpreadResponse)
def get_spread(spread_id: str, session: SessionDependency):
    return OptimizationRunService(session).spread(spread_id)


@router.get("/markers/{marker_hash}", response_model=MarkerArtifactResponse)
def get_marker(marker_hash: str, session: SessionDependency):
    return OptimizationRunService(session).marker(marker_hash)


@router.get("/optimization-runs/{run_id}/audit")
def get_optimization_audit(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).audit(run_id)


@router.get("/production-orders/{order_id}", response_model=ProductionOrderResponse)
def get_production_order(order_id: str, session: SessionDependency):
    return OrderService(session).get(order_id)
