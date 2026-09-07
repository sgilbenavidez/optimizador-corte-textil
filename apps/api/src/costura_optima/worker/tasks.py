from costura_optima.application.planning_coordinator import PlanningCoordinator
from costura_optima.infrastructure.database import SessionLocal
from costura_optima.infrastructure.db_models import OptimizationRunORM
from costura_optima.worker.recovery import recovery_status
from costura_optima.operational import configure_logging, request_id_context
from costura_optima.settings import get_settings


def execute_optimization_run(run_id: str) -> None:
    with SessionLocal() as session:
        run = session.get(OptimizationRunORM, run_id)
        configure_logging(get_settings().log_level)
        token = request_id_context.set(run.request_id if run else None)
        try:
            PlanningCoordinator(session).execute(run_id)
        finally:
            request_id_context.reset(token)


def record_worker_failure(job, connection, exc_type, exc_value, traceback) -> None:
    """RQ failure callback: preserve timeout semantics instead of flattening to FAILED."""
    run_id = job.id
    detail = getattr(exc_type, "__name__", str(exc_type))
    with SessionLocal() as session:
        run = session.get(OptimizationRunORM, run_id)
        if run is None or run.status in {"SUCCEEDED", "SUCCEEDED_EARLY", "CANCELLED", "TIMED_OUT"}:
            return
        run.status, reason = recovery_status(detail)
        failure_phase = run.phase
        run.phase = "FINALIZING"; run.error_detail = reason
        run.error_code = "WORKER_TIMEOUT" if run.status == "TIMED_OUT" else "WORKER_FAILED"
        run.failure_phase = failure_phase
        run.audit = {**(run.audit or {}), "failure": {"exception_class": detail, "worker_state": job.get_status(), "phase": failure_phase}}
        from datetime import datetime, timezone
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
