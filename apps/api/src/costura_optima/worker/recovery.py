from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from costura_optima.infrastructure.database import SessionLocal
from costura_optima.infrastructure.db_models import OptimizationRunORM


def main() -> None:
    lease_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    with SessionLocal() as session:
        stale = session.scalars(
            select(OptimizationRunORM).where(
                OptimizationRunORM.status == "RUNNING",
                OptimizationRunORM.heartbeat_at < lease_cutoff,
            )
        )
        for run in stale:
            run.status = "FAILED"
            run.phase = "FINALIZING"
            run.error_detail = "worker_lease_expired"
            run.finished_at = datetime.now(timezone.utc)
        session.commit()


if __name__ == "__main__":
    main()
