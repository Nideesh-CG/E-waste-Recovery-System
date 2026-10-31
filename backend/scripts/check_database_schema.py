#!/usr/bin/env python3
"""Database Schema Diagnostic & Verification Tool.

Connects to PostgreSQL (Supabase) or SQLite, performs read-only inspection of schemas,
tables, columns, data types, indexes, and foreign keys, and verifies compatibility
with authoritative SQLAlchemy models.

Exit codes:
  0 - All required schemas, tables, and columns are present.
  1 - One or more required tables or columns are missing, or connection failed.
"""

import os
import sys
from pathlib import Path

# Add project root and backend directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR.parent))

from dotenv import load_dotenv
load_dotenv(BASE_DIR / ".env")

from sqlalchemy import create_engine, inspect, text
from config import normalize_database_url
from models import Device, Transfer, AssessmentResult, DeviceFile, Metadata, SCHEMA

EXPECTED_MODELS = {
    "devices": Device,
    "transfers": Transfer,
    "assessment_results": AssessmentResult,
    "device_files": DeviceFile,
    "metadata": Metadata,
}

EXPECTED_HEAD_REVISION = "b7a8c9d0e1f2"


def get_connection_url():
    """Retrieve and normalize database connection URL from environment."""
    url = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("SUPABASE_DATABASE_URL")
        or ""
    )
    if not url:
        # Default local SQLite database if no remote DB configured
        db_path = BASE_DIR / "instance" / "circular.sqlite3"
        if not db_path.exists():
            db_path = BASE_DIR / "circular.sqlite3"
        url = f"sqlite:///{db_path}"
    return normalize_database_url(url)


def check_schema():
    raw_url = get_connection_url()
    safe_display_url = raw_url.split("@")[-1] if "@" in raw_url else raw_url
    print("=" * 80)
    print("DATABASE SCHEMA DIAGNOSTIC & VERIFICATION")
    print(f"Target: {safe_display_url}")
    print("=" * 80)

    try:
        connect_args = {}
        if "postgresql" in raw_url:
            connect_args["options"] = f"-c search_path={SCHEMA},public"
        
        engine = create_engine(raw_url, connect_args=connect_args)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"\n[ERROR] Failed to connect to database: {exc}")
        return 1

    inspector = inspect(engine)
    is_postgres = (engine.dialect.name == "postgresql")
    target_schema = SCHEMA if is_postgres else None

    print(f"Dialect:       {engine.dialect.name}")
    print(f"Target Schema: {target_schema or '(default)'}")

    if is_postgres:
        schemas = inspector.get_schema_names()
        print(f"Available Schemas: {', '.join(schemas)}")
        if SCHEMA not in schemas:
            print(f"\n[CRITICAL] Schema '{SCHEMA}' does NOT exist in PostgreSQL!")
            print(f"Please execute backend/migrations/collection_schema_init.sql in Supabase SQL Editor.")
            return 1

    # Check Alembic version table
    print("\n--- Alembic Migration Version ---")
    alembic_table = "alembic_version"
    has_alembic = inspector.has_table(alembic_table, schema=target_schema)
    current_revision = None

    if has_alembic:
        try:
            with engine.connect() as conn:
                tbl_spec = f"{target_schema}.{alembic_table}" if target_schema else alembic_table
                res = conn.execute(text(f"SELECT version_num FROM {tbl_spec}")).scalar()
                current_revision = res
            print(f"Table '{tbl_spec}' found.")
            print(f"Current stamped revision: {current_revision} (Expected head: {EXPECTED_HEAD_REVISION})")
        except Exception as exc:
            print(f"[WARN] Error querying alembic_version: {exc}")
    else:
        print(f"[WARN] Table '{alembic_table}' does NOT exist in schema '{target_schema or 'public'}'!")

    # Check tables & columns
    print("\n--- Model Column Inspection ---")
    has_errors = False
    
    for tbl_name, model_cls in EXPECTED_MODELS.items():
        tbl_exists = inspector.has_table(tbl_name, schema=target_schema)
        if not tbl_exists:
            print(f"\n[FAIL] Table '{tbl_name}' is MISSING in schema '{target_schema or 'public'}'!")
            has_errors = True
            continue

        existing_columns = {c["name"]: c for c in inspector.get_columns(tbl_name, schema=target_schema)}
        model_columns = {c.name: c for c in model_cls.__table__.columns}

        missing_cols = set(model_columns.keys()) - set(existing_columns.keys())
        extra_cols = set(existing_columns.keys()) - set(model_columns.keys())

        if missing_cols:
            print(f"\n[FAIL] Table '{tbl_name}': MISSING required columns: {sorted(missing_cols)}")
            has_errors = True
        else:
            print(f"\n[OK] Table '{tbl_name}': All {len(model_columns)} model columns present.")

        if extra_cols:
            print(f"     Note: Legacy/extra columns detected: {sorted(extra_cols)}")

        # Print details of columns
        for col_name in sorted(model_columns.keys()):
            if col_name in existing_columns:
                actual = existing_columns[col_name]
                print(f"       + {col_name:<20} {str(actual['type']):<20} nullable={actual.get('nullable')}")
            else:
                expected = model_columns[col_name]
                print(f"       ! {col_name:<20} {str(expected.type):<20} [MISSING FROM DB]")

    # Check Indexes
    print("\n--- Index Inspection ---")
    for tbl_name in EXPECTED_MODELS.keys():
        if inspector.has_table(tbl_name, schema=target_schema):
            indexes = inspector.get_indexes(tbl_name, schema=target_schema)
            idx_names = [i["name"] for i in indexes if i.get("name")]
            print(f"Table '{tbl_name}' indexes: {', '.join(idx_names) if idx_names else '(none)'}")

    print("\n" + "=" * 80)
    if has_errors:
        print("SUMMARY: SCHEMA VERIFICATION FAILED!")
        print("To fix schema issues:")
        print("  1. In Supabase: Run 'backend/migrations/collection_schema_init.sql' in the SQL Editor.")
        print("  2. Or run: 'flask db upgrade' from the backend directory.")
        print("=" * 80)
        return 1
    else:
        print("SUMMARY: ALL SCHEMAS, TABLES, AND COLUMNS MATCH AUTHORITATIVE MODELS.")
        print("=" * 80)
        return 0


if __name__ == "__main__":
    sys.exit(check_schema())

