"""Operational integration for run recovery and correlation.

Revision ID: 20260904_0004
Revises: 20260902_0003
"""

from alembic import op
import sqlalchemy as sa

revision = "20260904_0004"
down_revision = "20260902_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("optimization_runs") as batch:
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("round_current", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("round_total_if_known", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("request_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("failure_phase", sa.String(40), nullable=True))
        batch.create_index("ix_runs_order_created", ["production_order_id", "created_at"])
        batch.create_index("ix_runs_status_heartbeat", ["status", "heartbeat_at"])
    op.execute("UPDATE optimization_runs SET updated_at = COALESCE(finished_at, started_at, created_at)")


def downgrade() -> None:
    with op.batch_alter_table("optimization_runs") as batch:
        batch.drop_index("ix_runs_status_heartbeat")
        batch.drop_index("ix_runs_order_created")
        for column in ("failure_phase", "error_code", "request_id", "round_total_if_known", "round_current", "updated_at"):
            batch.drop_column(column)
