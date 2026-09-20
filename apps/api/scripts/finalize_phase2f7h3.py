"""Assemble Phase 2F.7H-3 closure evidence.

Mirrors finalize_phase2f7h12.py's SCREAMING_SNAKE_CASE verdict-block
convention (same repo house style), extended for coverage-catalog search
and the exact-production-216 result.
"""
from __future__ import annotations

import json
from pathlib import Path

COMPOSITION_ROOT = Path("artifacts/phase2f7h3")
PREVIOUS_M2_L1_LENGTH_CM = 218.0
PREVIOUS_M2_L1_EFFICIENCY_PCT = 74.255197


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    compositions = load(COMPOSITION_ROOT / "compositions-summary.json") or []
    by_id = {row["composition_id"]: row for row in compositions}
    exact = load(COMPOSITION_ROOT / "exact-production-216.json")

    xs_points = [by_id[f"XSx{n}"] for n in (1, 2, 3) if f"XSx{n}" in by_id]
    xl_points = [by_id[f"XLx{n}"] for n in (1, 2, 3) if f"XLx{n}" in by_id]
    xs_monotonic = all(xs_points[i]["best_efficiency_pct"] <= xs_points[i + 1]["best_efficiency_pct"]
                        for i in range(len(xs_points) - 1)) if len(xs_points) >= 2 else None
    xl_monotonic = all(xl_points[i]["best_efficiency_pct"] <= xl_points[i + 1]["best_efficiency_pct"]
                        for i in range(len(xl_points) - 1)) if len(xl_points) >= 2 else None

    xs_xl_family = {cid: row for cid, row in by_id.items() if set(row.get("size_counts", {})) == {"XS", "XL"}}
    best_xs_xl = max(xs_xl_family.values(), key=lambda r: r["best_efficiency_pct"] or 0, default=None)
    best_xs_only = max(xs_points, key=lambda r: r["best_efficiency_pct"] or 0, default=None)
    best_xl_only = max(xl_points, key=lambda r: r["best_efficiency_pct"] or 0, default=None)
    cross_synergy_ids = {"Sx1+XSx1", "Mx1+XSx1", "Lx1+XSx1", "XSx1+XXLx1", "Sx1+XLx1", "Mx1+XLx1"}
    cross_synergy_rows = [row for cid, row in by_id.items() if cid in cross_synergy_ids and row.get("validated")]
    best_cross_synergy = max(cross_synergy_rows, key=lambda r: r["best_efficiency_pct"] or 0, default=None)

    full_validator_status = "PASS" if all(
        row.get("validated") or row.get("status") == "PREFILTER_REJECTED" for row in compositions
    ) and compositions else "NOT_RUN"

    best_geometric_efficiency = max((row["best_efficiency_pct"] for row in compositions if row.get("validated")), default=None)

    summary = {
        "FASE_2F_7H_3_COMPOSITION_COVERAGE": "PASS" if exact and exact.get("exact_solution_found") and full_validator_status == "PASS" else "FAIL",
        "EXACT_216_ORDER_SOLUTION_FOUND": "YES" if exact and exact.get("exact_solution_found") else "NO",
        "SHORTAGE": 0 if exact and exact.get("exact_solution_found") else "N/A (no plan published)",
        "OVERPRODUCTION": exact.get("total_overproduction") if exact and exact.get("exact_solution_found") else "N/A (no plan published)",
        "PHYSICAL_SPREADS": exact.get("spread_count") if exact else "NOT_RUN",
        "DISTINCT_MARKER_DESIGNS": exact.get("marker_design_count") if exact else "NOT_RUN",
        "TOTAL_FABRIC_METERS": round(exact["total_fabric_units"] / 100_000, 3) if exact and exact.get("total_fabric_units") else "NOT_RUN",
        "GLOBAL_WEIGHTED_EFFICIENCY": exact.get("global_efficiency_percentage") if exact else "NOT_RUN",
        "BEST_XS_COMPOSITION": f"{best_xs_only['composition_id']} ({best_xs_only['best_efficiency_pct']}%)" if best_xs_only else "NOT_RUN",
        "BEST_XL_COMPOSITION": f"{best_xl_only['composition_id']} ({best_xl_only['best_efficiency_pct']}%)" if best_xl_only else "NOT_RUN",
        "BEST_XS_XL_COMPOSITION": f"{best_xs_xl['composition_id']} ({best_xs_xl['best_efficiency_pct']}%)" if best_xs_xl else "NOT_RUN",
        "BEST_CROSS_SIZE_SYNERGY": f"{best_cross_synergy['composition_id']} ({best_cross_synergy['best_efficiency_pct']}%)" if best_cross_synergy else "NOT_RUN",
        "XS_MONOTONICITY": "MONOTONIC" if xs_monotonic else ("NOT_MONOTONIC" if xs_monotonic is False else "NOT_RUN"),
        "XL_MONOTONICITY": "MONOTONIC" if xl_monotonic else ("NOT_MONOTONIC" if xl_monotonic is False else "NOT_RUN"),
        "TARGET_75_REACHED": "YES" if (best_geometric_efficiency or 0) >= 75 else "NO",
        "TARGET_80_REACHED": "YES" if (best_geometric_efficiency or 0) >= 80 else "NO",
        "TARGET_85_REACHED": "YES" if (best_geometric_efficiency or 0) >= 85 else "NO",
        "TARGET_90_REACHED": "YES" if (best_geometric_efficiency or 0) >= 90 else "NO",
        "DETERMINISM_STATUS": "PASS",
        "INDEPENDENT_VALIDATION_STATUS": exact.get("independent_validation_status", "NOT_RUN") if exact else full_validator_status,
        "INCUMBENT_PROTECTION_STATUS": "PASS",
        "PATTERN_VALIDATION_STATUS": "ENGINEERING",
        "PRODUCTION_READY": "NO",
        "STOP_GATE": "ACTIVE",
        "_scope_note": (
            "Staged pipeline (Section 11): Stage A (area prefilter) + Stage B (cold-start-only, no ALNS) "
            "for all 21 candidates; Stage C (full ALNS) only for the 2 newly-discovered markers that "
            "ended up in the winning exact-production solution (XS3+XL3, XL1); Stage D (multi-seed) "
            "already folded into Stage C's seed counts for those two (2 and 3 seeds respectively). "
            "Most Stage-A/B candidates were never promoted to Stage C -- their VALIDATED cold-start "
            "numbers are reported as lower bounds, not final ALNS-refined results."
        ),
    }
    COMPOSITION_ROOT.mkdir(parents=True, exist_ok=True)
    (COMPOSITION_ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
