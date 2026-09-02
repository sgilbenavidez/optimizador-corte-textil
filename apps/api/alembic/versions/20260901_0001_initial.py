"""Initial catalog, cutting resources, and production orders.

Revision ID: 20260901_0001
Revises:
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "20260901_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "garment_types",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "garment_models",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("garment_type_id", sa.String(36), sa.ForeignKey("garment_types.id"), nullable=False),
        sa.Column("code", sa.String(96), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("origin_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "garment_model_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("garment_model_id", sa.String(36), sa.ForeignKey("garment_models.id"), nullable=False),
        sa.Column("version_code", sa.String(128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("market_profile", sa.String(96), nullable=False),
        sa.Column("lifecycle_status", sa.String(40), nullable=False),
        sa.Column("validation_status", sa.String(64), nullable=False),
        sa.Column("is_orderable", sa.Boolean(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "size_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("garment_model_version_id", sa.String(36), sa.ForeignKey("garment_model_versions.id"), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.UniqueConstraint("garment_model_version_id", "code", name="uq_size_version_code"),
        sa.UniqueConstraint("garment_model_version_id", "display_order", name="uq_size_version_order"),
    )
    op.create_table(
        "measurement_values",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("size_definition_id", sa.String(36), sa.ForeignKey("size_definitions.id"), nullable=False),
        sa.Column("measurement_code", sa.String(80), nullable=False),
        sa.Column("measurement_kind", sa.String(24), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("provenance_status", sa.String(32), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("derivation", sa.Text(), nullable=True),
        sa.Column("version", sa.String(32), nullable=False),
        sa.UniqueConstraint("size_definition_id", "measurement_code", name="uq_measurement_size_code"),
    )
    op.create_table(
        "fabric_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_code", sa.String(96), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("physical_width_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("usable_width_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("fabric_family", sa.String(64), nullable=False),
        sa.Column("directional", sa.Boolean(), nullable=False),
        sa.Column("lay_mode", sa.String(64), nullable=False),
        sa.Column("piece_clearance_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("left_margin_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("right_margin_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("start_margin_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("end_margin_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("usable_width_cm <= physical_width_cm", name="ck_fabric_usable_width"),
    )
    op.create_table(
        "cutting_table_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version_code", sa.String(96), nullable=False, unique=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("physical_length_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("usable_length_cm", sa.Numeric(10, 3), nullable=False),
        sa.Column("max_layers", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("usable_length_cm <= physical_length_cm", name="ck_table_usable_length"),
        sa.CheckConstraint("max_layers >= 1", name="ck_table_max_layers"),
    )
    op.create_table(
        "production_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("garment_model_version_id", sa.String(36), sa.ForeignKey("garment_model_versions.id"), nullable=False),
        sa.Column("fabric_configuration_id", sa.String(36), sa.ForeignKey("fabric_configurations.id"), nullable=False),
        sa.Column("cutting_table_configuration_id", sa.String(36), sa.ForeignKey("cutting_table_configurations.id"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("total_quantity", sa.Integer(), nullable=False),
        sa.Column("catalog_snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("total_quantity > 0", name="ck_order_total_positive"),
    )
    op.create_table(
        "production_order_demands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("production_order_id", sa.String(36), sa.ForeignKey("production_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("size_code", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_order_demand_positive"),
        sa.UniqueConstraint("production_order_id", "size_code", name="uq_order_demand_size"),
    )
    op.create_index("ix_order_created_at", "production_orders", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_order_created_at", table_name="production_orders")
    op.drop_table("production_order_demands")
    op.drop_table("production_orders")
    op.drop_table("cutting_table_configurations")
    op.drop_table("fabric_configurations")
    op.drop_table("measurement_values")
    op.drop_table("size_definitions")
    op.drop_table("garment_model_versions")
    op.drop_table("garment_models")
    op.drop_table("garment_types")
