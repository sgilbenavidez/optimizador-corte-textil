"""Materialize Phase 2F.7G-6 determinism, Docker state, and compact summary."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("artifacts/phase2f7g6")


def load(path): return json.loads(path.read_text(encoding="utf-8"))


def main():
    diagnosis = load(ROOT / "summary.json")
    first = load(ROOT / "completion-after-extraction-fix" / "summary.json")
    second = load(ROOT / "determinism-run2" / "summary.json")
    first_hashes = [row["candidate_set_hash"] for row in first["piece_metrics"]]
    second_hashes = [row["candidate_set_hash"] for row in second["piece_metrics"]]
    checkpoint_hashes = []
    for index in range(11, 16):
        left = load(ROOT / "completion-after-extraction-fix" / "checkpoints" / f"piece_{index:02d}.json")["layout_hash"]
        right = load(ROOT / "determinism-run2" / "checkpoints" / f"piece_{index:02d}.json")["layout_hash"]
        checkpoint_hashes.append({"piece_index": index, "first": left, "second": right, "equal": left == right})
    deterministic = first["layout_hash"] == second["layout_hash"] and first_hashes == second_hashes and all(row["equal"] for row in checkpoint_hashes)
    determinism = {"executed": True, "status": "PASS" if deterministic else "FAIL", "layout_hash": first["layout_hash"], "candidate_hashes": first_hashes, "checkpoint_hashes": checkpoint_hashes}
    docker = {"executed": False, "status": "NOT_RUN", "reason": "dockerDesktopLinuxEngine unavailable; Docker Desktop service is stopped and host policy rejected starting it."}
    (ROOT / "determinism.json").write_text(json.dumps(determinism, indent=2), encoding="utf-8")
    (ROOT / "docker-equivalence.json").write_text(json.dumps(docker, indent=2), encoding="utf-8")
    # A faithful isolated visual of the newly accepted blocking piece.
    checkpoint = load(ROOT / "completion-after-extraction-fix" / "checkpoints" / "piece_12.json")
    piece = next(row for row in checkpoint["placed_pieces"] if row["piece_instance_id"] == "M_SLEEVE_004")
    points = " ".join(f"{x},{176000-y}" for x, y in piece["transformed_polygon"])
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 700000 176000"><rect width="700000" height="176000" fill="#fffdf7"/><polygon points="{points}" fill="#d9e1ef" stroke="#102b32" stroke-width="450"/></svg>'
    (ROOT / "m_sleeve_004.svg").write_text(svg, encoding="utf-8")
    summary = {"phase_status": "PARTIAL" if not docker["executed"] else "PASS", "checkpoint_11_replay_status": diagnosis["checkpoint_11_replay_status"],
        "active_blocking_piece": "M_SLEEVE_004", "current_marker_length_cm": diagnosis["current_marker_length_cm"], "candidate_container_length_cm": diagnosis["candidate_container_length_cm"],
        "ifp_x_max_cm": diagnosis["ifp_x_max_cm"], "current": diagnosis["current"], "full": diagnosis["full"], "marker_envelope_failure": False,
        "candidate_extraction_failure": True, "exactly_infeasible_frozen_state": False, "rollback_depth_1_tested": False, "rollback_depth_2_tested": False,
        "greedy_dead_end_confirmed": False, "candidate_space_pipeline_status": "PASS", "greedy_completion_status": "PASS", "completion_after_fix": first,
        "determinism_status": determinism["status"], "cross_platform": docker, "validator_status": "PASS", "blocking_diagnosis": "CANDIDATE_EXTRACTION_FAILURE", "global_search_authorized": False}
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

if __name__ == "__main__": main()
