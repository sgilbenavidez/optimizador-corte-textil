from __future__ import annotations

from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.domain.production_models import PlanValidationReport, PlanningSolution, ValidatedMarkerCandidate


class IndependentProductionPlanValidator:
    def validate(
        self,
        solution: PlanningSolution,
        markers: tuple[ValidatedMarkerCandidate, ...],
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        max_layers: int,
    ) -> PlanValidationReport:
        marker_map = {marker.marker_hash: marker for marker in markers}
        errors: list[str] = []
        produced = {size: 0 for size in demand}
        fabric = waste = piece_area = spread_count = 0
        checks = {
            "demand_covered": True, "overproduction_policy": True, "markers_validated": True,
            "layers_valid": True, "hashes_match": True, "metrics_match": True,
            "marker_length_within_table": True, "audit_complete": True,
        }
        for spread in solution.spreads:
            marker = marker_map.get(spread.marker_hash)
            if marker is None:
                checks["hashes_match"] = False; errors.append(f"unknown_marker:{spread.marker_hash}")
                continue
            if marker.validation_certificate.get("status") != "VALIDATED":
                checks["markers_validated"] = False; errors.append(f"marker_not_validated:{spread.marker_hash}")
            if not 1 <= spread.layers <= max_layers or spread.repeats < 1:
                checks["layers_valid"] = False; errors.append(f"invalid_layers:{spread.spread_hash}")
            expected_hash = canonical_json_hash({"marker": spread.marker_hash, "layers": spread.layers, "repeats": spread.repeats, "production": spread.production_by_size})
            if expected_hash != spread.spread_hash:
                checks["hashes_match"] = False; errors.append(f"spread_hash:{spread.spread_hash}")
            for size, quantity in dict(marker.composition).items():
                produced[size] += quantity * spread.layers * spread.repeats
            fabric += marker.marker_length_units * spread.layers * spread.repeats
            waste += marker.waste_area_units2 * spread.layers * spread.repeats
            piece_area += marker.piece_area_units2 * spread.layers * spread.repeats
            spread_count += spread.repeats
        over = {size: produced[size] - demand[size] for size in demand}
        for size in demand:
            if produced[size] < demand[size]:
                checks["demand_covered"] = False; errors.append(f"shortage:{size}")
            if over[size] > maximum_overproduction[size]:
                checks["overproduction_policy"] = False; errors.append(f"overproduction:{size}")
        efficiency = round(piece_area / (piece_area + waste) * 100, 6) if piece_area + waste else 0.0
        metrics_ok = (
            produced == solution.produced_by_size and over == solution.overproduction_by_size
            and fabric == solution.total_fabric_units and waste == solution.total_waste_units2
            and spread_count == solution.spread_count and efficiency == solution.global_efficiency_percentage
        )
        checks["metrics_match"] = metrics_ok
        if not metrics_ok:
            errors.append("aggregate_metrics_mismatch")
        status = "VALIDATED_PLAN" if all(checks.values()) else "INVALID_PLAN"
        return PlanValidationReport(status, checks, tuple(sorted(set(errors))), {
            "produced_by_size": produced, "overproduction_by_size": over, "fabric_units": fabric,
            "waste_units2": waste, "spread_count": spread_count, "global_efficiency_percentage": efficiency,
        })
