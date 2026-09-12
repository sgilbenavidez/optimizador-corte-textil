"""separate fabric face, marker direction and directionality

Revision ID: 20260906_0007
Revises: 20260905_0006
"""

from alembic import op
import sqlalchemy as sa

revision = "20260906_0007"
down_revision = "20260905_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fabric_configurations", sa.Column("lay_face_mode", sa.String(32), nullable=False, server_default="FACE_ONE_WAY"))
    op.add_column("fabric_configurations", sa.Column("marker_direction_policy", sa.String(32), nullable=False, server_default="TWO_WAY"))
    op.add_column("fabric_configurations", sa.Column("fabric_directionality", sa.String(32), nullable=False, server_default="NON_DIRECTIONAL"))
    op.execute("UPDATE fabric_configurations SET fabric_directionality = CASE WHEN directional THEN 'ONE_WAY_PRINT' ELSE 'NON_DIRECTIONAL' END")


def downgrade():
    op.drop_column("fabric_configurations", "fabric_directionality")
    op.drop_column("fabric_configurations", "marker_direction_policy")
    op.drop_column("fabric_configurations", "lay_face_mode")
