"""Production planning runs, marker artifacts, solutions and spreads.

Revision ID: 20260902_0003
Revises: 20260901_0002
"""

from alembic import op
import sqlalchemy as sa

revision = "20260902_0003"
down_revision = "20260901_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("marker_artifacts",
        sa.Column("marker_hash", sa.String(64), primary_key=True), sa.Column("content_key", sa.String(64), unique=True, nullable=False),
        sa.Column("pattern_hash", sa.String(64), nullable=False), sa.Column("fabric_hash", sa.String(64), nullable=False),
        sa.Column("table_hash", sa.String(64), nullable=False), sa.Column("composition", sa.JSON(), nullable=False),
        sa.Column("marker_payload", sa.JSON(), nullable=False), sa.Column("geometry_engine_version", sa.String(128), nullable=False),
        sa.Column("marker_search_status", sa.String(64), nullable=False), sa.Column("validation_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("optimization_runs",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("production_order_id", sa.String(36), sa.ForeignKey("production_orders.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False), sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False), sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("candidates_generated", sa.Integer(), nullable=False), sa.Column("candidates_evaluated", sa.Integer(), nullable=False),
        sa.Column("candidates_pending", sa.Integer(), nullable=False), sa.Column("candidates_feasible", sa.Integer(), nullable=False),
        sa.Column("candidates_infeasible", sa.Integer(), nullable=False), sa.Column("candidates_not_evaluated", sa.Integer(), nullable=False),
        sa.Column("best_feasible_found", sa.Boolean(), nullable=False), sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("audit", sa.JSON(), nullable=False), sa.Column("elapsed_candidate_generation_ms", sa.Float(), nullable=False),
        sa.Column("elapsed_geometry_ms", sa.Float(), nullable=False), sa.Column("elapsed_planner_ms", sa.Float(), nullable=False),
        sa.Column("elapsed_total_ms", sa.Float(), nullable=False), sa.Column("worker_job_id", sa.String(64), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True), sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("production_order_id", "idempotency_key", name="uq_run_order_idempotency"))
    op.create_table("optimization_candidates",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("optimization_run_id", sa.String(36), sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False), sa.Column("composition", sa.JSON(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False), sa.Column("origin", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("marker_hash", sa.String(64), sa.ForeignKey("marker_artifacts.marker_hash"), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False), sa.Column("geometry_elapsed_ms", sa.Float(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False), sa.UniqueConstraint("optimization_run_id", "candidate_hash", name="uq_run_candidate_hash"))
    op.create_table("optimization_solutions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("optimization_run_id", sa.String(36), sa.ForeignKey("optimization_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("solution_hash", sa.String(64), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("planning_status", sa.String(32), nullable=False), sa.Column("planning_optimality", sa.String(40), nullable=False),
        sa.Column("solution_origin", sa.String(64), nullable=False), sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("validation_certificate", sa.JSON(), nullable=False), sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("optimization_run_id", "fingerprint", name="uq_run_solution_fingerprint"))
    op.create_table("optimization_solution_profiles",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("optimization_solution_id", sa.String(36), sa.ForeignKey("optimization_solutions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile", sa.String(32), nullable=False), sa.Column("objective_stages", sa.JSON(), nullable=False),
        sa.UniqueConstraint("optimization_solution_id", "profile", name="uq_solution_profile"))
    op.create_table("spreads",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("optimization_solution_id", sa.String(36), sa.ForeignKey("optimization_solutions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("marker_hash", sa.String(64), sa.ForeignKey("marker_artifacts.marker_hash"), nullable=False),
        sa.Column("spread_hash", sa.String(64), nullable=False), sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("layers", sa.Integer(), nullable=False), sa.Column("repeats", sa.Integer(), nullable=False), sa.Column("composition", sa.JSON(), nullable=False),
        sa.Column("production_by_size", sa.JSON(), nullable=False), sa.Column("marker_length_units", sa.BigInteger(), nullable=False),
        sa.Column("fabric_consumption_units", sa.BigInteger(), nullable=False), sa.Column("marker_efficiency_percentage", sa.Float(), nullable=False),
        sa.Column("marker_search_status", sa.String(64), nullable=False), sa.Column("validation_status", sa.String(32), nullable=False),
        sa.UniqueConstraint("optimization_solution_id", "spread_hash", name="uq_solution_spread_hash"))
    op.create_table("size_results",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("optimization_solution_id", sa.String(36), sa.ForeignKey("optimization_solutions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("size_code", sa.String(16), nullable=False), sa.Column("requested", sa.Integer(), nullable=False),
        sa.Column("produced", sa.Integer(), nullable=False), sa.Column("overproduction", sa.Integer(), nullable=False),
        sa.UniqueConstraint("optimization_solution_id", "size_code", name="uq_solution_size"))


def downgrade() -> None:
    for table in ("size_results", "spreads", "optimization_solution_profiles", "optimization_solutions", "optimization_candidates", "optimization_runs", "marker_artifacts"):
        op.drop_table(table)
