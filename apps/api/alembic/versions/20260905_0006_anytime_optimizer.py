"""autonomous anytime optimizer state

Revision ID: 20260905_0006
Revises: 20260905_0005
"""

from alembic import op
import sqlalchemy as sa

revision = "20260905_0006"
down_revision = "20260905_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("optimization_runs", sa.Column("use_current_plan_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("optimization_runs", sa.Column("first_solution_elapsed_ms", sa.Float(), nullable=True))
    op.add_column("optimization_runs", sa.Column("first_solution_spreads", sa.Integer(), nullable=True))
    op.add_column("optimization_runs", sa.Column("first_solution_fabric_units", sa.BigInteger(), nullable=True))
    op.add_column("optimization_runs", sa.Column("final_solution_elapsed_ms", sa.Float(), nullable=True))
    op.add_column("optimization_runs", sa.Column("final_solution_spreads", sa.Integer(), nullable=True))
    op.add_column("optimization_runs", sa.Column("final_solution_fabric_units", sa.BigInteger(), nullable=True))


def downgrade():
    for name in (
        "final_solution_fabric_units", "final_solution_spreads", "final_solution_elapsed_ms",
        "first_solution_fabric_units", "first_solution_spreads", "first_solution_elapsed_ms",
        "use_current_plan_requested",
    ):
        op.drop_column("optimization_runs", name)
