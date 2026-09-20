"""Regression tests for FASE 2F.8.1: cross-order validated-marker catalog
compatibility contract, bootstrap query, and historical evidence import.

Bootstrap/compatibility tests insert `MarkerArtifactORM` rows directly
(fast, no real geometry needed -- these test the SQL/compatibility logic,
not the geometry engine, which is already covered by 2F.7H/2F.8's test
suites). The import tests exercise the real re-validation path against a
real (fast-generated) pattern set, since faithfulness of that
reconstruction is exactly what matters there.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

# apps/api/scripts is not on the pytest collection path by default (only
# "src" is, per pyproject.toml); import_phase2f7h_marker_evidence.py is a
# script-level module reused here on purpose (Section 5's import logic
# should not be duplicated into a second copy just for tests).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from costura_optima.application.planning_coordinator import PlanningCoordinator
from costura_optima.infrastructure.db_models import MarkerArtifactORM, PatternSetVersionORM, ProductionOrderORM
from costura_optima.infrastructure.repositories import CatalogRepository
from costura_optima.patterns.persistence import generate_and_persist

from import_phase2f7h_marker_evidence import TARGET_MARKERS, import_all, import_marker  # noqa: E402
from test_joint_optimization import _create_order  # noqa: E402


def _insert_marker_row(session, *, marker_hash, content_key, pattern_hash, fabric_hash, table_hash,
                        composition, length_units=100_000, validation_status="VALIDATED"):
    units = 1000
    pieces = sum(composition.values()) * 5
    payload = {
        "geometry_units_per_cm": units, "marker_length_cm": length_units / units, "usable_width_cm": 176.0,
        "piece_area_total_cm2": pieces * 100, "marker_area_cm2": pieces * 120, "waste_area_cm2": pieces * 20,
        "efficiency_percentage": 83.333333, "placements": [{"piece": index} for index in range(pieces)],
        "validation": {"status": validation_status}, "algorithm_version": "test-fixture",
        "search_status": "VALIDATED_FEASIBLE", "input_hash": content_key, "lower_bound_length_cm": 1.0,
    }
    row = MarkerArtifactORM(
        marker_hash=marker_hash, content_key=content_key, pattern_hash=pattern_hash, fabric_hash=fabric_hash,
        table_hash=table_hash, composition=composition, marker_payload=payload,
        geometry_engine_version="test-fixture", marker_search_status="VALIDATED_FEASIBLE",
        validation_status=validation_status,
    )
    session.add(row)
    session.flush()
    return row


def _coordinator_and_session(database):
    session = database()
    return PlanningCoordinator(session), session


# ---- compatibility contract / bootstrap query ------------------------------

def test_compatible_marker_is_reused_via_bootstrap(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="h1", content_key="ck1", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"M": 2})
    session.commit()
    found = coordinator._bootstrap_compatible_markers("P", "F", "T")
    assert {item.marker_hash for item in found} == {"h1"}


def test_incompatible_pattern_hash_not_reused(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="h1", content_key="ck1", pattern_hash="P_OLD", fabric_hash="F",
                        table_hash="T", composition={"M": 2})
    session.commit()
    assert coordinator._bootstrap_compatible_markers("P_NEW", "F", "T") == ()


def test_incompatible_fabric_hash_not_reused(database):
    """fabric_hash already bakes in width/clearance/orientation/margins
    (infrastructure/seed_data.py) -- a mismatch here fail-closes reuse for
    any of those dimensions without needing separate columns.
    """
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="h1", content_key="ck1", pattern_hash="P", fabric_hash="F_OLD_CLEARANCE",
                        table_hash="T", composition={"M": 2})
    session.commit()
    assert coordinator._bootstrap_compatible_markers("P", "F_NEW_CLEARANCE", "T") == ()


def test_incompatible_table_hash_not_reused(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="h1", content_key="ck1", pattern_hash="P", fabric_hash="F",
                        table_hash="T_OLD", composition={"M": 2})
    session.commit()
    assert coordinator._bootstrap_compatible_markers("P", "F", "T_NEW") == ()


def test_unvalidated_marker_never_reused_fail_closed(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="h1", content_key="ck1", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"M": 2}, validation_status="INVALID")
    session.commit()
    assert coordinator._bootstrap_compatible_markers("P", "F", "T") == ()


def test_same_composition_dominance_keeps_only_the_shorter_marker(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="worse", content_key="ck1", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"M": 2}, length_units=200_000)
    _insert_marker_row(session, marker_hash="better", content_key="ck2", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"M": 2}, length_units=150_000)
    session.commit()
    found = coordinator._bootstrap_compatible_markers("P", "F", "T")
    assert {item.marker_hash for item in found} == {"better"}


def test_different_compositions_are_both_retained(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="m2", content_key="ck1", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"M": 2})
    _insert_marker_row(session, marker_hash="l1xs3", content_key="ck2", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"L": 1, "XS": 3})
    session.commit()
    found = coordinator._bootstrap_compatible_markers("P", "F", "T")
    assert {item.marker_hash for item in found} == {"m2", "l1xs3"}


def test_bootstrap_query_is_deterministic(database):
    coordinator, session = _coordinator_and_session(database)
    _insert_marker_row(session, marker_hash="a", content_key="ck1", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"M": 1})
    _insert_marker_row(session, marker_hash="b", content_key="ck2", pattern_hash="P", fabric_hash="F",
                        table_hash="T", composition={"L": 1})
    session.commit()
    first = tuple(sorted(item.marker_hash for item in coordinator._bootstrap_compatible_markers("P", "F", "T")))
    second = tuple(sorted(item.marker_hash for item in coordinator._bootstrap_compatible_markers("P", "F", "T")))
    assert first == second == ("a", "b")


# ---- concurrency -----------------------------------------------------------

def test_concurrent_equivalent_artifact_insertion_produces_no_duplicate(database):
    coordinator, session = _coordinator_and_session(database)
    payload = {
        "result_hash": "same-hash", "algorithm_version": "test", "search_status": "VALIDATED_FEASIBLE",
        "validation": {"status": "VALIDATED"},
    }
    from types import SimpleNamespace
    pattern_set = SimpleNamespace(content_hash="P")
    order = SimpleNamespace(catalog_snapshot={"fabric_configuration": {"content_hash": "F"},
                                               "cutting_table_configuration": {"content_hash": "T"}})
    first_artifact, first_cache_hit = coordinator._persist_marker_artifact("dup-key", pattern_set, order, {"M": 2}, payload)
    session.commit()
    second_artifact, second_cache_hit = coordinator._persist_marker_artifact("dup-key", pattern_set, order, {"M": 2}, payload)
    assert first_cache_hit is False
    assert second_cache_hit is True
    assert first_artifact.marker_hash == second_artifact.marker_hash
    rows = session.scalars(select(MarkerArtifactORM).where(MarkerArtifactORM.content_key == "dup-key")).all()
    assert len(rows) == 1


# ---- flag-off regression ----------------------------------------------------

def test_flag_off_ignores_compatible_catalog_entries(client, catalog_ids, database, monkeypatch):
    from costura_optima.application import services as services_module
    monkeypatch.setattr(services_module, "enqueue_optimization_run", lambda run_id, timeout: run_id)
    demand = {"M": 2}
    maximum_overproduction = {"M": 0}
    order = _create_order(client, database, catalog_ids, demand)
    run_response = client.post(
        f"/api/v1/production-orders/{order['id']}/optimization-runs",
        headers={"Idempotency-Key": "2f8-1-flag-off"},
        json={"planner_refinement_engine": "heuristic", "persistent_catalog_bootstrap_enabled": False},
    ).json()
    session = database()
    from costura_optima.infrastructure.db_models import OptimizationRunORM
    run = session.get(OptimizationRunORM, run_response["id"])
    order_row = session.get(ProductionOrderORM, order["id"])
    pattern_set = session.get(PatternSetVersionORM, order_row.pattern_set_version_id)
    fabric = CatalogRepository(session).get_fabric(order_row.fabric_configuration_id)
    table = CatalogRepository(session).get_table(order_row.cutting_table_configuration_id)
    _insert_marker_row(session, marker_hash="preexisting", content_key="ck-preexisting",
                        pattern_hash=pattern_set.content_hash, fabric_hash=fabric.content_hash,
                        table_hash=table.content_hash, composition={"M": 2}, length_units=50_000)
    session.commit()

    coordinator = PlanningCoordinator(session)
    coordinator.execute(run.id)
    session.refresh(run)
    assert "catalog_bootstrap" not in (run.audit or {})


# ---- historical import -------------------------------------------------------

def test_imported_historical_markers_are_revalidated_and_accepted(database):
    session = database()
    pattern_set, _ = generate_and_persist(session)
    catalog = CatalogRepository(session)
    fabric = catalog.list_fabrics()[0]
    table = catalog.list_tables()[0]
    results = import_all(session, pattern_set, fabric, table)
    session.commit()
    assert {row["tag"] for row in results} == set(TARGET_MARKERS)
    assert all(row["status"] == "IMPORTED" for row in results), results
    stored = session.scalars(select(MarkerArtifactORM)).all()
    assert len(stored) == len(TARGET_MARKERS)
    assert all(row.validation_status == "VALIDATED" for row in stored)


def test_corrupted_historical_marker_is_rejected_by_import(database, tmp_path):
    session = database()
    pattern_set, _ = generate_and_persist(session)
    catalog = CatalogRepository(session)
    fabric = catalog.list_fabrics()[0]
    table = catalog.list_tables()[0]

    real_path = TARGET_MARKERS["XL1"]["path"]
    evidence = json.loads(Path(real_path).read_text(encoding="utf-8"))
    # Corrupt the geometry hash of the first placement -> must be rejected
    # as a pattern-changed-since-capture mismatch, never silently imported.
    evidence["placements"][0]["geometry_hash"] = "deliberately-wrong-hash"
    corrupted_path = tmp_path / "corrupted-best-marker.json"
    corrupted_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = import_marker(session, pattern_set, fabric, table, "XL1-corrupted", [("XL", 1)], corrupted_path)
    session.commit()
    assert result["status"] == "REJECTED"
    assert "geometry_hash_mismatch" in result["reason"]
    stored = session.scalars(select(MarkerArtifactORM)).all()
    assert len(stored) == 0
