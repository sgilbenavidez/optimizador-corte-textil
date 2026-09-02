"""Versioned engineering pattern geometry.

Revision ID: 20260901_0002
Revises: 20260901_0001
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260901_0002"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pattern_parameter_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(128), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("lifecycle_status", sa.String(40), nullable=False),
        sa.Column("validation_status", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pattern_set_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("garment_model_version_id", sa.String(36), sa.ForeignKey("garment_model_versions.id"), nullable=False),
        sa.Column("parameter_profile_id", sa.String(36), sa.ForeignKey("pattern_parameter_profiles.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("version_code", sa.String(160), nullable=False, unique=True),
        sa.Column("algorithm_version", sa.String(128), nullable=False),
        sa.Column("measurement_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("lifecycle_status", sa.String(40), nullable=False),
        sa.Column("validation_status", sa.String(64), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("geometry_units_per_cm", sa.Integer(), nullable=False),
        sa.Column("warning", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("garment_model_version_id", "version", name="uq_pattern_set_model_version"),
    )
    op.create_table(
        "pattern_pieces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pattern_set_version_id", sa.String(36), sa.ForeignKey("pattern_set_versions.id"), nullable=False),
        sa.Column("size_code", sa.String(16), nullable=False),
        sa.Column("piece_code", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source_geometry", sa.JSON(), nullable=False),
        sa.Column("seamline_geometry", sa.JSON(), nullable=False),
        sa.Column("cutline_geometry", sa.JSON(), nullable=False),
        sa.Column("operational_geometry", sa.JSON(), nullable=False),
        sa.Column("grainline", sa.JSON(), nullable=False),
        sa.Column("allowed_rotations_degrees", sa.JSON(), nullable=False),
        sa.Column("mirror_allowed", sa.Boolean(), nullable=False),
        sa.Column("edge_allowances_cm", sa.JSON(), nullable=False),
        sa.Column("technical_measurements", sa.JSON(), nullable=False),
        sa.Column("geometry_metrics", sa.JSON(), nullable=False),
        sa.Column("geometry_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pattern_set_version_id", "size_code", "piece_code", name="uq_pattern_piece_set_size_code"),
    )
    with op.batch_alter_table("production_orders") as batch_op:
        batch_op.add_column(sa.Column("pattern_set_version_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_order_pattern_set_version", "pattern_set_versions", ["pattern_set_version_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("production_orders") as batch_op:
        batch_op.drop_constraint("fk_order_pattern_set_version", type_="foreignkey")
        batch_op.drop_column("pattern_set_version_id")
    op.drop_table("pattern_pieces")
    op.drop_table("pattern_set_versions")
    op.drop_table("pattern_parameter_profiles")
