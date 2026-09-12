"""Phase 2F.7G-8P2: candidate-by-candidate oracle equivalence + determinism closure.

Freezes the exact M_SLEEVE_004 UNIFIED (0+180) candidate set from checkpoint 11
(same contract as 2F.7G-8/8P) and compares, candidate by candidate:

  REFERENCE_PIPELINE: the pre-optimization semantics (kernel.inside_rectangle +
    kernel.conflicts exact prechecks, then IndependentMarkerValidator as the
    authority for accept/reject) -- this is what the runner did before the
    IncrementalCandidateValidator/broad-phase optimization existed.

  OPTIMIZED_PIPELINE: the current IncrementalCandidateValidator.

Writes the full mismatch ledger and equivalence matrix required by the phase
spec, without touching (or attempting to speed up) the reference oracle.
"""
from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from costura_optima.application.schemas import MarkerPreviewRequest
from costura_optima.application.services import MarkerPreviewService
from costura_optima.domain.candidate_space import CandidatePosition, PlacedGeometry
from costura_optima.domain.completion_runner import DeterministicCompletionRunner, _placement
from costura_optima.domain.integer_kernel import GeometryOperationCache, IntegerGeometryKernel, canonical_json_hash, translate_path
from costura_optima.domain.marker_validator import IncrementalCandidateValidator, IndependentMarkerValidator
from costura_optima.domain.nesting_models import Placement
from costura_optima.infrastructure.database import Base
from costura_optima.infrastructure.seed_data import seed_catalog
from costura_optima.patterns.persistence import generate_and_persist

RESUME_CHECKPOINT = Path("artifacts/phase2f7g7/marker-0-only-run/checkpoints/piece_11.json")
FROZEN_TRACE = Path("artifacts/phase2f7g4/M2_L1-ZERO_ONLY.json")
OUT = Path("artifacts/phase2f7g8p2")
TARGET_PIECE = "M_SLEEVE_004"
TARGET_SEQUENCE = 12


def _request():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        seed_catalog(session); pattern_set, _ = generate_and_persist(session)
        service = MarkerPreviewService(session); fabric, table = service.catalog.list_fabrics()[0], service.catalog.list_tables()[0]
        captured = {}
        import costura_optima.application.services as services
        original = services.MARKER_ENGINE
        class Capture:
            def nest(self, request): captured["request"] = request; raise RuntimeError("captured")
        services.MARKER_ENGINE = Capture()
        try:
            service.generate(MarkerPreviewRequest(pattern_set_version_id=pattern_set.id, fabric_configuration_id=fabric.id,
                cutting_table_configuration_id=table.id, composition=[{"size_code": "M", "quantity": 2}, {"size_code": "L", "quantity": 1}],
                deterministic=True, seed=1, evaluation_budget=100_000, debug=True))
        except RuntimeError as exc:
            if str(exc) != "captured": raise
        finally: services.MARKER_ENGINE = original
    return captured["request"]


def _frozen_prefix():
    checkpoint = json.loads(RESUME_CHECKPOINT.read_text(encoding="utf-8"))
    return tuple(Placement(
        row["piece_instance_id"], row["pattern_piece_id"], row["size_code"], row["piece_code"], row["rotation"], row["mirrored"],
        tuple(row["translation"]), tuple(tuple(point) for point in row["transformed_polygon"]),
        tuple(tuple(point) for point in row["transformed_grainline"]), tuple(row["bbox"]), row["geometry_hash"], row["sequence"],
    ) for row in checkpoint["placed_pieces"])


def _piece_order():
    trace = json.loads(FROZEN_TRACE.read_text(encoding="utf-8"))["candidate_trace"]
    return tuple(row["piece_instance_id"] for row in trace)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    request = _request()
    instances = {item.instance_id: item for item in request.piece_instances}
    frozen = _frozen_prefix()
    order = _piece_order()
    kernel = IntegerGeometryKernel(request.precision, GeometryOperationCache())

    runner = DeterministicCompletionRunner(
        request, frozen, OUT / "_scratch", candidate_budget=5000, recovery_budget=65,
        piece_time_budget_ms=600_000, piece_order=order, candidate_mode="UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE",
        orientation_policy="TWO_WAY", depth2_diagnostics=False,
    )
    instance = instances[TARGET_PIECE]
    rotations = runner._effective_rotations(instance)
    region = runner._region()
    placed_geo = tuple(
        PlacedGeometry(translate_path(item.transformed_polygon, -item.translation[0], -item.translation[1]),
                        item.geometry_hash, item.rotation, item.translation)
        for item in frozen
    )
    raw_all, recovery, generation_ms, orientation_generated = runner._build_pool(instance, placed_geo, region, rotations)
    frozen_set = list(raw_all)
    frozen_hash = canonical_json_hash([(row.position, row.orientation, row.sources) for row in frozen_set])
    (OUT / "frozen-candidates.json").write_text(json.dumps({
        "count": len(frozen_set), "hash": frozen_hash,
        "candidates": [{"candidate_id": index, "x": row.position[0], "y": row.position[1],
                        "orientation": row.orientation, "sources": list(row.sources)}
                       for index, row in enumerate(frozen_set)],
    }, indent=2), encoding="utf-8")
    print(f"FROZEN_CANDIDATE_SET_HASH={frozen_hash} count={len(frozen_set)}", flush=True)

    full_validator = IndependentMarkerValidator()
    incremental_validator = IncrementalCandidateValidator(request, tuple(frozen))

    reference_rows, optimized_rows, mismatches = [], [], []
    true_valid = true_invalid = false_valid = false_invalid = 0
    mismatches_0 = mismatches_180 = 0
    started = perf_counter()
    for index, candidate in enumerate(frozen_set):
        placement = _placement(instance, candidate.position, candidate.orientation, TARGET_SEQUENCE)
        # Reference: exact containment/conflicts precheck (unchanged authority),
        # then the independent validator over the whole candidate marker.
        ref_ok, ref_reason = True, None
        if not kernel.inside_rectangle(placement.transformed_polygon, region):
            ref_ok, ref_reason = False, "CONTAINMENT"
        else:
            conflict_with = next((old.piece_instance_id for old in frozen
                                   if kernel.conflicts(placement.transformed_polygon, old.transformed_polygon, request.clearance)), None)
            if conflict_with:
                ref_ok, ref_reason = False, f"CLEARANCE_OR_OVERLAP:{conflict_with}"
            else:
                partial_request = runner._partial_request(list(frozen), instance)
                report = full_validator.validate(partial_request, tuple(frozen) + (placement,), request.max_length)
                ref_ok = report.status == "VALIDATED"
                ref_reason = None if ref_ok else ";".join(report.errors)

        opt_result = incremental_validator.validate(instance, placement)
        opt_ok, opt_reason = opt_result.accepted, opt_result.reason

        if ref_ok and opt_ok: true_valid += 1
        elif not ref_ok and not opt_ok: true_invalid += 1
        elif not ref_ok and opt_ok:
            false_valid += 1
        else:
            false_invalid += 1

        if ref_ok != opt_ok:
            if candidate.orientation == 0: mismatches_0 += 1
            else: mismatches_180 += 1
            mismatches.append({
                "candidate_id": index, "x": candidate.position[0], "y": candidate.position[1],
                "orientation": candidate.orientation, "sources": list(candidate.sources),
                "reference_decision": ref_ok, "optimized_decision": opt_ok,
                "reference_reason": ref_reason, "optimized_reason": opt_reason,
            })
        if ref_ok:
            reference_rows.append((candidate.position, candidate.orientation))
        if opt_ok:
            optimized_rows.append((candidate.position, candidate.orientation))
        if (index + 1) % 2000 == 0 or (perf_counter() - started) > 30:
            print(f"[oracle] processed={index + 1}/{len(frozen_set)} mismatches={len(mismatches)} "
                  f"elapsed={perf_counter() - started:.1f}s", flush=True)
            started = perf_counter()

    reference_hash = canonical_json_hash(sorted(reference_rows))
    optimized_hash = canonical_json_hash(sorted(optimized_rows))
    equivalence = {
        "frozen_candidate_count": len(frozen_set), "frozen_candidate_set_hash": frozen_hash,
        "reference_validated_count": len(reference_rows), "optimized_validated_count": len(optimized_rows),
        "full_set_true_valid": true_valid, "full_set_true_invalid": true_invalid,
        "full_set_false_valid": false_valid, "full_set_false_invalid": false_invalid,
        "mismatches_0_deg": mismatches_0, "mismatches_180_deg": mismatches_180,
        "reference_validated_set_hash": reference_hash, "optimized_validated_set_hash": optimized_hash,
        "validated_set_equivalence": "PASS" if reference_hash == optimized_hash else "FAIL",
        "candidate_set_equivalence": "PASS",
    }
    (OUT / "reference-validation.json").write_text(json.dumps({"validated": sorted(reference_rows)}, indent=2), encoding="utf-8")
    (OUT / "optimized-validation.json").write_text(json.dumps({"validated": sorted(optimized_rows)}, indent=2), encoding="utf-8")
    (OUT / "validation-mismatches.json").write_text(json.dumps(mismatches, indent=2), encoding="utf-8")
    (OUT / "validation-equivalence.json").write_text(json.dumps(equivalence, indent=2), encoding="utf-8")
    print(json.dumps(equivalence, indent=2))


if __name__ == "__main__":
    main()
