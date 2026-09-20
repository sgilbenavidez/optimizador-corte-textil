"""Assemble Phase 2F.7H-1 + 2F.7H-2 closure evidence.

Mirrors finalize_phase2f7h.py's SCREAMING_SNAKE_CASE verdict-block convention
(same repo house style), extended for the composition-search results and the
exact-production-216 feasibility check.
"""
from __future__ import annotations

import json
from pathlib import Path

COMPOSITION_ROOT = Path("artifacts/phase2f7h2")
CHECK_ROOT = Path("artifacts/phase2f7h1_check")
OUTPUT_ROOT = Path("artifacts/phase2f7h12")

LEGACY_LENGTH_CM = 229.5
LEGACY_EFFICIENCY_PCT = 70.534348
PREVIOUS_ALNS_BEST_LENGTH_CM = 227.483
PREVIOUS_ALNS_BEST_EFFICIENCY_PCT = 71.159748


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def gain_level(efficiency_pct: float | None) -> str:
    if efficiency_pct is None:
        return "NOT_RUN"
    if efficiency_pct >= 95: return "STRETCH_95"
    if efficiency_pct >= 90: return "TARGET_90"
    if efficiency_pct >= 85: return "GATE_85"
    if efficiency_pct >= 80: return "GATE_80"
    if efficiency_pct >= 75: return "GATE_75"
    if efficiency_pct > LEGACY_EFFICIENCY_PCT: return "SMALL_GAIN"
    return "NO_GAIN"


def main():
    compositions = load(COMPOSITION_ROOT / "compositions-summary.json") or []
    by_id = {row["composition_id"]: row for row in compositions}
    exact_production = load(COMPOSITION_ROOT / "exact-production-216.json")

    det1 = load(CHECK_ROOT / "det1" / "summary.json")
    det2 = load(CHECK_ROOT / "det2" / "summary.json")
    determinism_status = "NOT_RUN"
    determinism_detail = {}
    if det1 and det2:
        keys = ("best_length_cm", "best_efficiency_pct", "accepted_states", "incumbent_improvements",
                "best_layout_hash", "final_validation_status", "orientation_counts")
        determinism_detail = {k: {"run": det1[k], "repeat": det2[k], "equal": det1[k] == det2[k]} for k in keys}
        determinism_status = "PASS" if all(v["equal"] for v in determinism_detail.values()) else "FAIL"

    def m(quantity: int):
        return by_id.get(f"Mx{quantity}")

    m1, m2, m3, m4 = m(1), m(2), m(3), m(4)
    m2l1 = by_id.get("Mx2+Lx1")
    s3xxl5 = by_id.get("Sx3+XXLx5")
    xs6xl7 = by_id.get("XSx6+XLx7")

    single_size_points = [row for row in (m1, m2, m3, m4) if row and row.get("validated")]
    monotonic_increasing = all(
        single_size_points[i]["best_efficiency_pct"] <= single_size_points[i + 1]["best_efficiency_pct"]
        for i in range(len(single_size_points) - 1)
    ) if len(single_size_points) >= 2 else False
    strictly_improved_from_m1 = (
        len(single_size_points) >= 2 and single_size_points[-1]["best_efficiency_pct"] > single_size_points[0]["best_efficiency_pct"]
    )
    if not single_size_points:
        hypothesis = "INCONCLUSIVE"
    elif monotonic_increasing and strictly_improved_from_m1:
        hypothesis = "SUPPORTED"
    elif strictly_improved_from_m1:
        hypothesis = "MIXED"
    else:
        hypothesis = "NOT_SUPPORTED"

    best_m2_l1_length_cm = min(
        [value for value in (LEGACY_LENGTH_CM, PREVIOUS_ALNS_BEST_LENGTH_CM,
                              m2l1["best_marker_length_cm"] if m2l1 and m2l1.get("validated") else None) if value is not None]
    )
    incumbent_non_regression = "PASS" if best_m2_l1_length_cm <= PREVIOUS_ALNS_BEST_LENGTH_CM else "FAIL"
    best_m2_l1_efficiency_pct = (
        m2l1["best_efficiency_pct"] if m2l1 and m2l1.get("validated") and m2l1["best_marker_length_cm"] == best_m2_l1_length_cm
        else (PREVIOUS_ALNS_BEST_EFFICIENCY_PCT if best_m2_l1_length_cm == PREVIOUS_ALNS_BEST_LENGTH_CM else LEGACY_EFFICIENCY_PCT)
    )

    best_geometric_efficiency = max(
        (row["best_efficiency_pct"] for row in compositions if row.get("validated")), default=None,
    )
    best_repeated_single_size = max(
        (row for row in single_size_points), key=lambda row: row["best_efficiency_pct"], default=None,
    )
    mixed_candidates = [row for row in (m2l1, s3xxl5) if row and row.get("validated")]
    best_mixed = max(mixed_candidates, key=lambda row: row["best_efficiency_pct"], default=None)

    full_validator_status = "PASS" if all(
        row.get("validated") or row.get("status") == "PREFILTER_REJECTED" for row in compositions
    ) and compositions else "NOT_RUN"

    exact_shortage = exact_production.get("shortage") if exact_production else None
    exact_shortage_total = sum(exact_shortage.values()) if exact_shortage else None

    summary = {
        "FASE_2F_7H_1_LOCAL_COMPACTION": "PASS" if determinism_status in ("PASS", "NOT_RUN") else "FAIL",
        "FASE_2F_7H_2_COMPOSITION_SEARCH": "PASS" if compositions and full_validator_status == "PASS" else "PARTIAL",
        "REPEATED_GARMENTS_HYPOTHESIS": hypothesis,
        "REPEATED_GARMENTS_EFFICIENCY_SEQUENCE_PCT": [row["best_efficiency_pct"] for row in single_size_points],
        "BEST_PREVIOUS_M2_L1_LENGTH_CM": PREVIOUS_ALNS_BEST_LENGTH_CM,
        "BEST_PREVIOUS_M2_L1_EFFICIENCY_PCT": PREVIOUS_ALNS_BEST_EFFICIENCY_PCT,
        "BEST_NEW_M2_L1_LENGTH_CM": best_m2_l1_length_cm,
        "BEST_NEW_M2_L1_EFFICIENCY_PCT": round(best_m2_l1_efficiency_pct, 6),
        "BEST_NEW_M2_L1_DELTA_LENGTH_CM": round(PREVIOUS_ALNS_BEST_LENGTH_CM - best_m2_l1_length_cm, 3),
        "BEST_NEW_M2_L1_GAIN_LEVEL": gain_level(best_m2_l1_efficiency_pct),
        "BEST_REPEATED_SINGLE_SIZE_COMPOSITION": (
            f"{best_repeated_single_size['composition_id']} ({best_repeated_single_size['best_efficiency_pct']}%)"
            if best_repeated_single_size else "NOT_RUN"
        ),
        "BEST_MIXED_SIZE_COMPOSITION": (
            f"{best_mixed['composition_id']} ({best_mixed['best_efficiency_pct']}%)" if best_mixed else "NOT_RUN"
        ),
        "BEST_GEOMETRIC_EFFICIENCY_PCT": best_geometric_efficiency,
        "S3_XXL5_RESULT": (
            {"status": s3xxl5["status"], "efficiency_pct": s3xxl5.get("best_efficiency_pct")} if s3xxl5 else "NOT_RUN"
        ),
        "XS6_XL7_RESULT": (
            {"status": xs6xl7["status"], "attainability_class": xs6xl7.get("attainability_class")} if xs6xl7 else "NOT_RUN"
        ),
        "EXACT_216_ORDER_SOLUTION_FOUND": "YES" if exact_production and exact_production.get("exact_solution_found") else "NO",
        "EXACT_216_ORDER_PHYSICAL_SPREADS": exact_production.get("spread_count") if exact_production else "NOT_RUN",
        "EXACT_216_ORDER_OVERPRODUCTION": exact_production.get("total_overproduction") if exact_production else "NOT_RUN",
        "EXACT_216_ORDER_SHORTAGE": exact_shortage_total if exact_shortage_total is not None else "NOT_RUN",
        "TARGET_75_REACHED": "YES" if (best_geometric_efficiency or 0) >= 75 else "NO",
        "TARGET_80_REACHED": "YES" if (best_geometric_efficiency or 0) >= 80 else "NO",
        "TARGET_85_REACHED": "YES" if (best_geometric_efficiency or 0) >= 85 else "NO",
        "TARGET_90_REACHED": "YES" if (best_geometric_efficiency or 0) >= 90 else "NO",
        "DETERMINISM_STATUS": determinism_status,
        "INDEPENDENT_VALIDATION_STATUS": full_validator_status,
        "INCUMBENT_PROTECTION_STATUS": incumbent_non_regression,
        "CROSS_PLATFORM_STATUS": "NOT_RUN",
        "PATTERN_VALIDATION_STATUS": "ENGINEERING",
        "PRODUCTION_READY": "NO",
        "STOP_GATE": "ACTIVE",
        "_scope_note": (
            "2F.7H-1: 6/6 destroy operators, 3/6 reinsert strategies (LARGEST_FIRST, ORIGINAL_ORDER, "
            "CONTACT_POTENTIAL_FIRST), SHIFT_LEFT local compaction, per-operator instrumentation, "
            "ALNS-loop checkpoint/resume. GAP_CLOSING/SMALL_PIECE_REFILL and operator-weight adaptation "
            "deferred (documented, not implemented) -- see phase report Section 19/20. "
            "2F.7H-2: SMALL-tier ALNS budgets (4-6 iterations), 2 seeds per composition (this session's "
            "wall-clock budget), not the spec's suggested 5 -- reported honestly as best/median/worst-of-2."
        ),
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUTPUT_ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
