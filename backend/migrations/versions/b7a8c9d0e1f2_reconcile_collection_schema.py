"""reconcile_collection_schema

Revision ID: b7a8c9d0e1f2
Revises: 95b1a260ef7a
Create Date: 2026-10-31 23:30:00.000000

Forward-only corrective migration to reconcile the collection schema:
- Ensures all tables and columns match the authoritative SQLAlchemy models.
- Safely handles databases initialized with legacy/divergent schema definitions.
- Non-destructively adds missing columns ('date', 'year', 'weight', 'damage', 'power', 'screen', 'battery').
- Backfills data from legacy columns ('purchase_year', 'weight_kg', etc.) without dropping existing data.
- Ensures indexes and constraints exist in schema 'collection'.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = 'b7a8c9d0e1f2'
down_revision = '95b1a260ef7a'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    is_postgres = (dialect_name == "postgresql")
    target_schema = "collection" if is_postgres else None

    # Step 1: In PostgreSQL, ensure dedicated 'collection' schema exists
    if is_postgres:
        op.execute("CREATE SCHEMA IF NOT EXISTS collection;")

    inspector = inspect(bind)

    # Helper to check if a table exists in target schema
    def table_exists(table_name):
        try:
            return inspector.has_table(table_name, schema=target_schema)
        except Exception:
            return False

    # Helper to get existing column names for a table
    def get_column_names(table_name):
        try:
            cols = inspector.get_columns(table_name, schema=target_schema)
            return {c["name"] for c in cols}
        except Exception:
            return set()

    # -------------------------------------------------------------------------
    # 1. DEVICES TABLE
    # -------------------------------------------------------------------------
    if not table_exists("devices"):
        op.create_table(
            "devices",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("type", sa.String(64), nullable=False),
            sa.Column("brand", sa.String(64), nullable=False),
            sa.Column("model", sa.String(64), nullable=False),
            sa.Column("status", sa.String(64), nullable=False, server_default="Pending assessment"),
            sa.Column("date", sa.String(32), nullable=True),
            sa.Column("condition", sa.String(64), nullable=False),
            sa.Column("year", sa.String(8), nullable=False),
            sa.Column("weight", sa.String(16), nullable=False),
            sa.Column("damage", sa.Text(), nullable=True, server_default=""),
            sa.Column("power", sa.String(64), nullable=True, server_default=""),
            sa.Column("screen", sa.String(64), nullable=True, server_default=""),
            sa.Column("battery", sa.String(64), nullable=True, server_default=""),
            sa.Column("image", sa.String(256), nullable=True),
            sa.Column("document", sa.String(256), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            schema=target_schema,
        )
    else:
        existing_cols = get_column_names("devices")

        # Column 'date'
        if "date" not in existing_cols:
            op.add_column("devices", sa.Column("date", sa.String(32), nullable=True), schema=target_schema)
            if is_postgres and "created_at" in existing_cols:
                op.execute("UPDATE collection.devices SET date = to_char(created_at, 'Mon DD, YYYY') WHERE date IS NULL;")

        # Column 'year'
        if "year" not in existing_cols:
            op.add_column("devices", sa.Column("year", sa.String(8), nullable=True), schema=target_schema)
            if is_postgres:
                if "purchase_year" in existing_cols:
                    op.execute("UPDATE collection.devices SET year = purchase_year::varchar WHERE year IS NULL AND purchase_year IS NOT NULL;")
                op.execute("UPDATE collection.devices SET year = '2024' WHERE year IS NULL;")
                op.alter_column("devices", "year", nullable=False, schema=target_schema)

        # Column 'weight'
        if "weight" not in existing_cols:
            op.add_column("devices", sa.Column("weight", sa.String(16), nullable=True), schema=target_schema)
            if is_postgres:
                if "weight_kg" in existing_cols:
                    op.execute("UPDATE collection.devices SET weight = weight_kg::varchar WHERE weight IS NULL AND weight_kg IS NOT NULL;")
                op.execute("UPDATE collection.devices SET weight = '1.0' WHERE weight IS NULL;")
                op.alter_column("devices", "weight", nullable=False, schema=target_schema)

        # Column 'damage'
        if "damage" not in existing_cols:
            op.add_column("devices", sa.Column("damage", sa.Text(), nullable=True, server_default=""), schema=target_schema)
            if is_postgres and "damage_description" in existing_cols:
                op.execute("UPDATE collection.devices SET damage = damage_description WHERE (damage IS NULL OR damage = '') AND damage_description IS NOT NULL;")

        # Column 'power'
        if "power" not in existing_cols:
            op.add_column("devices", sa.Column("power", sa.String(64), nullable=True, server_default=""), schema=target_schema)
            if is_postgres and "power_status" in existing_cols:
                op.execute("UPDATE collection.devices SET power = power_status WHERE (power IS NULL OR power = '') AND power_status IS NOT NULL;")

        # Column 'screen'
        if "screen" not in existing_cols:
            op.add_column("devices", sa.Column("screen", sa.String(64), nullable=True, server_default=""), schema=target_schema)
            if is_postgres and "screen_condition" in existing_cols:
                op.execute("UPDATE collection.devices SET screen = screen_condition WHERE (screen IS NULL OR screen = '') AND screen_condition IS NOT NULL;")

        # Column 'battery'
        if "battery" not in existing_cols:
            op.add_column("devices", sa.Column("battery", sa.String(64), nullable=True, server_default=""), schema=target_schema)
            if is_postgres and "battery_condition" in existing_cols:
                op.execute("UPDATE collection.devices SET battery = battery_condition WHERE (battery IS NULL OR battery = '') AND battery_condition IS NOT NULL;")

        # Widen columns in PostgreSQL
        if is_postgres:
            op.alter_column("devices", "id", type_=sa.String(64), schema=target_schema)
            op.alter_column("devices", "name", type_=sa.String(128), schema=target_schema)
            op.alter_column("devices", "type", type_=sa.String(64), schema=target_schema)
            op.alter_column("devices", "brand", type_=sa.String(64), schema=target_schema)
            op.alter_column("devices", "model", type_=sa.String(64), schema=target_schema)
            op.alter_column("devices", "status", type_=sa.String(64), schema=target_schema)
            op.alter_column("devices", "condition", type_=sa.String(64), schema=target_schema)
            op.alter_column("devices", "image", type_=sa.String(256), schema=target_schema)
            op.alter_column("devices", "document", type_=sa.String(256), schema=target_schema)

    # -------------------------------------------------------------------------
    # 2. TRANSFERS TABLE
    # -------------------------------------------------------------------------
    fk_devices_ref = "collection.devices.id" if is_postgres else "devices.id"
    if not table_exists("transfers"):
        op.create_table(
            "transfers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("device_id", sa.String(64), sa.ForeignKey(fk_devices_ref), nullable=True),
            sa.Column("file", sa.String(128), nullable=False),
            sa.Column("sender", sa.String(64), nullable=False),
            sa.Column("receiver", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="Pending"),
            sa.Column("time", sa.String(64), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
            sa.Column("http_status", sa.String(64), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("endpoint", sa.String(256), nullable=True),
            sa.Column("payload", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"),
            sa.Column("idempotency_key", sa.String(128), unique=True, nullable=True),
            schema=target_schema,
        )
    else:
        existing_cols = get_column_names("transfers")
        if "http_status" not in existing_cols:
            op.add_column("transfers", sa.Column("http_status", sa.String(64), nullable=True), schema=target_schema)
        if "response_body" not in existing_cols:
            op.add_column("transfers", sa.Column("response_body", sa.Text(), nullable=True), schema=target_schema)
        if "endpoint" not in existing_cols:
            op.add_column("transfers", sa.Column("endpoint", sa.String(256), nullable=True), schema=target_schema)
        if "retry_count" not in existing_cols:
            op.add_column("transfers", sa.Column("retry_count", sa.Integer(), nullable=True, server_default="0"), schema=target_schema)
        if "idempotency_key" not in existing_cols:
            op.add_column("transfers", sa.Column("idempotency_key", sa.String(128), nullable=True), schema=target_schema)
            try:
                op.create_unique_constraint("uq_transfers_idempotency_key", "transfers", ["idempotency_key"], schema=target_schema)
            except Exception:
                pass

        if is_postgres:
            op.alter_column("transfers", "id", type_=sa.String(64), schema=target_schema)
            op.alter_column("transfers", "device_id", type_=sa.String(64), schema=target_schema)
            op.alter_column("transfers", "file", type_=sa.String(128), schema=target_schema)
            op.alter_column("transfers", "sender", type_=sa.String(64), schema=target_schema)
            op.alter_column("transfers", "receiver", type_=sa.String(64), schema=target_schema)
            op.alter_column("transfers", "status", type_=sa.String(32), schema=target_schema)
            op.alter_column("transfers", "time", type_=sa.String(64), schema=target_schema)

    # -------------------------------------------------------------------------
    # 3. ASSESSMENT RESULTS TABLE
    # -------------------------------------------------------------------------
    if not table_exists("assessment_results"):
        op.create_table(
            "assessment_results",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("device_id", sa.String(64), sa.ForeignKey(fk_devices_ref), unique=True, nullable=False),
            sa.Column("repairability_score", sa.Integer(), nullable=False),
            sa.Column("circular_recovery_score", sa.Integer(), nullable=False),
            sa.Column("repair_cost", sa.Float(), nullable=False),
            sa.Column("component_value", sa.Float(), nullable=False),
            sa.Column("recommendation", sa.String(64), nullable=False),
            sa.Column("co2_avoided_kg", sa.Float(), nullable=False),
            sa.Column("assessed_by", sa.String(128), nullable=True, server_default="GreenTech Refurbishment Partner"),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("factors", sa.Text(), nullable=True),
            sa.Column("assessed_at", sa.DateTime(), nullable=True),
            schema=target_schema,
        )
    else:
        if is_postgres:
            op.alter_column("assessment_results", "device_id", type_=sa.String(64), schema=target_schema)
            op.alter_column("assessment_results", "assessed_by", type_=sa.String(128), schema=target_schema)
            op.alter_column("assessment_results", "recommendation", type_=sa.String(64), schema=target_schema)

    # -------------------------------------------------------------------------
    # 4. DEVICE FILES TABLE
    # -------------------------------------------------------------------------
    if not table_exists("device_files"):
        op.create_table(
            "device_files",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("device_id", sa.String(64), sa.ForeignKey(fk_devices_ref), nullable=False),
            sa.Column("file_type", sa.String(32), nullable=False),
            sa.Column("filename", sa.String(128), nullable=False),
            sa.Column("original_filename", sa.String(256), nullable=False),
            sa.Column("storage_key", sa.String(256), unique=True, nullable=False),
            sa.Column("file_path", sa.String(512), nullable=False),
            sa.Column("content_type", sa.String(128), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("storage_provider", sa.String(32), nullable=False, server_default="local"),
            sa.Column("is_private", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            schema=target_schema,
        )
    else:
        existing_cols = get_column_names("device_files")
        if "created_at" not in existing_cols:
            op.add_column("device_files", sa.Column("created_at", sa.DateTime(), nullable=True), schema=target_schema)
            if is_postgres and "uploaded_at" in existing_cols:
                op.execute("UPDATE collection.device_files SET created_at = uploaded_at WHERE created_at IS NULL;")
        if is_postgres:
            op.alter_column("device_files", "device_id", type_=sa.String(64), schema=target_schema)
            op.alter_column("device_files", "file_type", type_=sa.String(32), schema=target_schema)
            op.alter_column("device_files", "original_filename", type_=sa.String(256), schema=target_schema)
            op.alter_column("device_files", "storage_key", type_=sa.String(256), schema=target_schema)
            op.alter_column("device_files", "content_type", type_=sa.String(128), schema=target_schema)
            op.alter_column("device_files", "storage_provider", type_=sa.String(32), schema=target_schema)

    # -------------------------------------------------------------------------
    # 5. METADATA TABLE
    # -------------------------------------------------------------------------
    if not table_exists("metadata"):
        op.create_table(
            "metadata",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("value", sa.String(256), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            schema=target_schema,
        )
    else:
        existing_cols = get_column_names("metadata")
        if "created_at" not in existing_cols:
            op.add_column("metadata", sa.Column("created_at", sa.DateTime(), nullable=True), schema=target_schema)
        if is_postgres:
            op.alter_column("metadata", "key", type_=sa.String(64), schema=target_schema)

    # -------------------------------------------------------------------------
    # 6. INDEXES
    # -------------------------------------------------------------------------
    def safe_create_index(idx_name, tbl_name, columns, unique=False):
        try:
            op.create_index(idx_name, tbl_name, columns, unique=unique, schema=target_schema)
        except Exception:
            pass

    safe_create_index("ix_devices_status", "devices", ["status"])
    safe_create_index("ix_devices_created_at", "devices", ["created_at"])
    safe_create_index("ix_transfers_status", "transfers", ["status"])
    safe_create_index("ix_transfers_timestamp", "transfers", ["timestamp"])
    safe_create_index("ix_transfers_idempotency_key", "transfers", ["idempotency_key"], unique=True)
    safe_create_index("ix_device_files_storage_key", "device_files", ["storage_key"], unique=True)


def downgrade():
    # Defensive non-destructive downgrade: do not drop columns or tables to preserve production data
    pass

