from __future__ import annotations

from costura_optima.domain.integer_kernel import canonical_json_hash
from costura_optima.domain.production_models import PlanValidationReport, PlanningSolution, ValidatedMarkerCandidate
from costura_optima.domain.candidate_generator import useful_coverage


class IndependentProductionPlanValidator:
    def validate(
        self,
        solution: PlanningSolution,
        markers: tuple[ValidatedMarkerCandidate, ...],
        demand: dict[str, int],
        maximum_overproduction: dict[str, int],
        max_layers: int,
        usable_table_length_units: int | None = None,
        audit_evidence: dict | None = None,
    ) -> PlanValidationReport:
        marker_map = {marker.marker_hash: marker for marker in markers}
        errors: list[str] = []
        produced = {size: 0 for size in demand}
        fabric = waste = piece_area = spread_count = 0
        checks = {
            "demand_covered": True, "overproduction_policy": True, "markers_validated": True,
            "layers_valid": True, "hashes_match": True, "metrics_match": True,
            "marker_length_within_table": True, "audit_complete": True,
            "coverage_metrics_match": True, "physical_spreads_match": True,
        }
        required_audit = {
            "order_hash", "input_hash", "pattern_hash", "fabric_snapshot", "table_snapshot", "policy",
            "candidate_generation_config", "geometry_config", "geometry_version", "planner_config",
            "planner_version", "seed", "marker_hashes", "solver_status", "objective_stages",
            "validation_certificate",
        }
        if audit_evidence is not None:
            missing = sorted(key for key in required_audit if key not in audit_evidence or audit_evidence[key] is None)
            consistent = (
                audit_evidence.get("marker_hashes") == sorted(marker.marker_hash for marker in markers)
                and isinstance(audit_evidence.get("fabric_snapshot"), dict)
                and isinstance(audit_evidence.get("table_snapshot"), dict)
                and isinstance(audit_evidence.get("candidate_generation_config"), dict)
                and isinstance(audit_evidence.get("geometry_config"), dict)
                and isinstance(audit_evidence.get("planner_config"), dict)
                and isinstance(audit_evidence.get("objective_stages"), (list, tuple))
                and bool(audit_evidence.get("order_hash")) and bool(audit_evidence.get("input_hash"))
                and bool(audit_evidence.get("pattern_hash")) and bool(audit_evidence.get("geometry_version"))
                and bool(audit_evidence.get("planner_version")) and bool(audit_evidence.get("solver_status"))
            )
            checks["audit_complete"] = not missing and consistent
            if missing:
                errors.append("audit_missing:" + ",".join(missing))
            if not consistent:
                errors.append("audit_marker_hashes_inconsistent")
        else:
            checks["audit_complete"] = False
            errors.append("audit_missing:all")
        if usable_table_length_units is None:
            checks["marker_length_within_table"] = False
            errors.append("usable_table_length_missing")
        remaining = dict(demand)
        primary_covered = 0
        primary_percentage = 0.0
        for index, spread in enumerate(solution.spreads):
            marker = marker_map.get(spread.marker_hash)
            if marker is None:
                checks["hashes_match"] = False; errors.append(f"unknown_marker:{spread.marker_hash}")
                continue
            if marker.validation_certificate.get("status") != "VALIDATED":
                checks["markers_validated"] = False; errors.append(f"marker_not_validated:{spread.marker_hash}")
            if usable_table_length_units is not None and marker.marker_length_units > usable_table_length_units:
                checks["marker_length_within_table"] = False; errors.append(f"marker_exceeds_table:{spread.marker_hash}")
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
            spread_useful = useful_coverage(remaining, spread.composition, spread.layers * spread.repeats)
            remaining_total = sum(remaining.values())
            spread_percentage = round(spread_useful / remaining_total * 100, 6) if remaining_total else 0.0
            for size, quantity in spread.production_by_size.items():
                remaining[size] = max(0, remaining.get(size, 0) - quantity)
            if (spread.useful_garments != spread_useful
                    or spread.order_coverage_percentage != spread_percentage
                    or spread.remaining_demand_after != remaining
                    or spread.is_primary != (index == 0)):
                checks["coverage_metrics_match"] = False
                errors.append(f"coverage_metrics:{spread.spread_hash}")
            if index == 0:
                primary_covered = spread_useful
                primary_percentage = spread_percentage
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
            and primary_covered == solution.primary_spread_covered_garments
            and primary_percentage == solution.primary_spread_coverage_percentage
        )
        checks["metrics_match"] = metrics_ok
        if not metrics_ok:
            errors.append("aggregate_metrics_mismatch")
        checks["physical_spreads_match"] = spread_count == sum(spread.repeats for spread in solution.spreads)
        status = "VALIDATED_PLAN" if all(checks.values()) else "INVALID_PLAN"
        return PlanValidationReport(status, checks, tuple(sorted(set(errors))), {
            "produced_by_size": produced, "overproduction_by_size": over, "fabric_units": fabric,
            "waste_units2": waste, "spread_count": spread_count, "global_efficiency_percentage": efficiency,
            "primary_spread_covered_garments": primary_covered,
            "primary_spread_coverage_percentage": primary_percentage,
            "remaining_demand": remaining,
        })
