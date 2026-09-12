"""Persist cut coverage and residual metrics.

Revision ID: 20260905_0005
Revises: 20260904_0004
"""

from alembic import op
import sqlalchemy as sa

revision = "20260905_0005"
down_revision = "20260904_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("spreads") as batch:
        batch.add_column(sa.Column("useful_garments", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("order_coverage_percentage", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("remaining_demand_after", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("spreads") as batch:
        for column in ("is_primary", "remaining_demand_after", "order_coverage_percentage", "useful_garments"):
            batch.drop_column(column)
