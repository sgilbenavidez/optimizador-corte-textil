from __future__ import annotations

from typing import Protocol

from costura_optima.domain.operational_heuristic_solver import OperationalHeuristicSolver
from costura_optima.domain.production_models import PlanningSolution, ValidatedMarkerCandidate


class ProductionSolver(Protocol):
    def solve(self, demand: dict[str, int], maximum_overproduction: dict[str, int],
              markers: tuple[ValidatedMarkerCandidate, ...]) -> PlanningSolution: ...


class CpSatRefinementSolver:
    """Lazy adapter: heuristic-only runtime never imports OR-Tools."""

    def __init__(self, config):
        self.config = config

    def solve_profiles(self, demand, maximum_overproduction, markers, cancellation_checkpoint=None, incumbent=None):
        from costura_optima.domain.production_planner import ProductionPlanner
        return ProductionPlanner(self.config).solve_profiles(
            demand, maximum_overproduction, markers, cancellation_checkpoint=cancellation_checkpoint,
            initial_incumbent=incumbent,
        )


__all__ = ["ProductionSolver", "OperationalHeuristicSolver", "CpSatRefinementSolver"]
