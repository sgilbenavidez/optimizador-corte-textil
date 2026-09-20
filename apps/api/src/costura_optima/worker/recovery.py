from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from costura_optima.infrastructure.database import SessionLocal
from costura_optima.infrastructure.db_models import OptimizationRunORM


def recovery_status(error_detail: str | None, job_status: str | None = None) -> tuple[str, str]:
    detail = (error_detail or "").lower()
    if "timeout" in detail or "jobtimeoutexception" in detail:
        return "TIMED_OUT", "worker_hard_timeout"
    if job_status == "stopped" or "interrupted" in detail:
        return "FAILED", "worker_interrupted"
    return "FAILED", "worker_lease_expired"


def recover_stale_runs(session, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    lease_cutoff = now - timedelta(minutes=5)
    stale = list(session.scalars(
        select(OptimizationRunORM).where(
            OptimizationRunORM.status == "RUNNING",
            OptimizationRunORM.heartbeat_at < lease_cutoff,
        )
    ))
    for run in stale:
        run.status, reason = recovery_status(run.error_detail)
        failure_phase = run.phase
        run.phase = "FINALIZING"; run.error_detail = reason; run.finished_at = now
        run.error_code = "WORKER_LEASE_EXPIRED"; run.failure_phase = failure_phase
    session.commit()
    return len(stale)


def main() -> None:
    with SessionLocal() as session:
        recover_stale_runs(session)


if __name__ == "__main__":
    main()
