from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from redis import Redis
from rq import Worker
from sqlalchemy import func, select, text
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
from costura_optima.infrastructure.db_models import OptimizationCandidateORM, OptimizationRunORM, ProductionOrderORM
from costura_optima.operational import metrics
from costura_optima.settings import get_settings
from costura_optima.worker.queue import optimization_queue, redis_connection


router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_db_session)]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/health/ready")
def readiness(session: SessionDependency):
    checks = {"database": False, "redis": False}
    try:
        session.execute(text("SELECT 1")); checks["database"] = True
    except Exception:
        pass
    try:
        checks["redis"] = bool(redis_connection().ping())
    except Exception:
        pass
    ready = all(checks.values())
    return Response(
        content=__import__("json").dumps({"status": "ready" if ready else "not_ready", "checks": checks}),
        media_type="application/json", status_code=200 if ready else 503,
    )


@router.get("/health/worker")
def worker_health():
    try:
        connection = redis_connection()
        workers = Worker.all(connection=connection, queue=optimization_queue())
        active = [worker.name for worker in workers if worker.get_state() in {"idle", "busy"}]
        payload = {"status": "ok" if active else "unavailable", "workers": active, "queue_depth": len(optimization_queue())}
        return Response(content=__import__("json").dumps(payload), media_type="application/json", status_code=200 if active else 503)
    except Exception:
        return Response(content='{"status":"unavailable","workers":[],"queue_depth":null}', media_type="application/json", status_code=503)


@router.get("/metrics")
def prometheus_metrics(session: SessionDependency):
    counts = dict(session.execute(select(OptimizationRunORM.status, func.count()).group_by(OptimizationRunORM.status)).all())
    gauges = {"queue_depth": 0.0}
    try:
        gauges["queue_depth"] = float(len(optimization_queue()))
    except Exception:
        gauges["queue_depth"] = -1.0
    for state, metric in (("SUCCEEDED", "optimization_runs_succeeded"), ("FAILED", "optimization_runs_failed"),
                          ("TIMED_OUT", "optimization_runs_timed_out"), ("CANCELLED", "optimization_runs_cancelled")):
        gauges[metric] = float(counts.get(state, 0) + (counts.get("SUCCEEDED_EARLY", 0) if state == "SUCCEEDED" else 0))
    gauges["optimization_runs_total"] = float(sum(counts.values()))
    gauges["candidate_count"] = float(session.scalar(select(func.count()).select_from(OptimizationCandidateORM)) or 0)
    cache_hits = float(session.scalar(select(func.count()).select_from(OptimizationCandidateORM).where(OptimizationCandidateORM.cache_hit.is_(True))) or 0)
    cache_misses = float(session.scalar(select(func.count()).select_from(OptimizationCandidateORM).where(
        OptimizationCandidateORM.cache_hit.is_(False), OptimizationCandidateORM.marker_hash.is_not(None)
    )) or 0)
    gauges["marker_cache_hits"] = cache_hits
    gauges["marker_cache_misses"] = cache_misses
    completed = session.execute(
        select(
            func.avg(OptimizationRunORM.elapsed_total_ms),
            func.avg(OptimizationRunORM.elapsed_geometry_ms),
            func.avg(OptimizationRunORM.elapsed_planner_ms),
        ).where(OptimizationRunORM.status.in_(("SUCCEEDED", "SUCCEEDED_EARLY", "FAILED", "CANCELLED", "TIMED_OUT", "INFEASIBLE")))
    ).one()
    gauges["optimization_duration_seconds"] = float(completed[0] or 0) / 1000
    gauges["geometry_duration_seconds"] = float(completed[1] or 0) / 1000
    gauges["planning_duration_seconds"] = float(completed[2] or 0) / 1000
    return Response(content=metrics.render(gauges), media_type="text/plain; version=0.0.4")


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


@router.get("/production-orders")
def list_production_orders(session: SessionDependency, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return OrderService(session).list_page(page, page_size)


@router.post("/production-orders/{order_id}/optimization-runs", response_model=OptimizationRunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_optimization_run(
    order_id: str, payload: OptimizationRunCreate, session: SessionDependency,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
):
    return OptimizationRunService(session).create(order_id, payload, idempotency_key)


@router.get("/optimization-runs/{run_id}", response_model=OptimizationRunResponse)
def get_optimization_run(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).get(run_id)


@router.get("/production-orders/{order_id}/optimization-runs")
def list_order_runs(order_id: str, session: SessionDependency, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return OptimizationRunService(session).list_for_order(order_id, page, page_size)


@router.post("/optimization-runs/{run_id}/retry", response_model=OptimizationRunResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_optimization_run(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).retry(run_id)


@router.post("/optimization-runs/{run_id}/cancel", response_model=OptimizationRunResponse)
def cancel_optimization_run(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).cancel(run_id)


@router.post("/optimization-runs/{run_id}/use-current-plan", response_model=OptimizationRunResponse)
def use_current_optimization_plan(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).use_current_plan(run_id)


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


@router.get("/optimization-runs/{run_id}/export")
def export_optimization_result(run_id: str, session: SessionDependency):
    return OptimizationRunService(session).export(run_id)


@router.get("/markers/{marker_hash}/svg")
def download_marker_svg(marker_hash: str, session: SessionDependency):
    svg, filename = OptimizationRunService(session).marker_svg(marker_hash)
    return Response(content=svg, media_type="image/svg+xml", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/production-orders/{order_id}", response_model=ProductionOrderResponse)
def get_production_order(order_id: str, session: SessionDependency):
    return OrderService(session).get(order_id)
