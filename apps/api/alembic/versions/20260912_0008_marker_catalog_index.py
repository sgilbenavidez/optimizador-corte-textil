"""additive index for cross-order marker catalog bootstrap queries

Revision ID: 20260912_0008
Revises: 20260906_0007
"""

from alembic import op

revision = "20260912_0008"
down_revision = "20260906_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_marker_artifacts_compatibility",
        "marker_artifacts",
        ["pattern_hash", "fabric_hash", "table_hash"],
    )


def downgrade():
    op.drop_index("ix_marker_artifacts_compatibility", table_name="marker_artifacts")
