from costura_optima.application.planning_coordinator import PlanningCoordinator
from costura_optima.infrastructure.database import SessionLocal


def execute_optimization_run(run_id: str) -> None:
    with SessionLocal() as session:
        PlanningCoordinator(session).execute(run_id)
