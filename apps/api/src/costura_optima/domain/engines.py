from typing import Any, Protocol

from costura_optima.domain.geometry import PatternPieceGeometry
from costura_optima.domain.nesting_models import MarkerRequest, MarkerResult


class GeometryEngine(Protocol):
    """Boundary for a future geometry implementation; no vendor types allowed."""

    def validate_pattern(self, pattern: PatternPieceGeometry) -> dict[str, Any]: ...

    def nest(self, request: MarkerRequest) -> MarkerResult: ...


class OptimizationEngine(Protocol):
    """Boundary for a future planner; intentionally not implemented in Phase 2A/2C."""

    def optimize(self, order_snapshot: dict[str, Any]) -> dict[str, Any]: ...
