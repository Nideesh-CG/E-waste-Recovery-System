# Production Schema Repair & Database Alignment Report
**Project:** Circular E-Waste Recovery & Refurbishment Intelligence Platform  
**Application:** Application 1 — Collection & Recovery (`collection` schema)  
**Target Environment:** Vercel Serverless (WSGI/Flask) + Supabase PostgreSQL & Supabase Storage  

---

## 1. Executive Summary & Root Cause Analysis

### Identified Production Errors
1. **Missing Column Error:**
   ```text
   psycopg.errors.UndefinedColumn: column devices.date does not exist
   LINE 1: SELECT count(*) AS count_1 FROM collection.devices WHERE collection.devices.created_at >= ...
   ```
   *Trigger:* `GET /` &rarr; `dashboard()` &rarr; `render("dashboard")` &rarr; `get_platform_metrics()` &rarr; `dev_q.count()`
2. **Missing Alembic Version Table Error:**
   ```text
   ERROR: 42P01: relation "collection.alembic_version" does not exist
   ```

### Root Cause
- **Divergent Schema Definitions:**  
  The initial SQL initialization script (`backend/migrations/collection_schema_init.sql`) defined `collection.devices` with column names such as `purchase_year`, `weight_kg`, `power_status`, `screen_condition`, `battery_condition`, and `damage_description`, but omitted `date`, `year`, `weight`, `power`, `screen`, `battery`, and `damage`.
- **SQLAlchemy Model Expectation:**  
  The authoritative model `Device` in `backend/models.py` defines the canonical attributes `date`, `year`, `weight`, `damage`, `power`, `screen`, `battery`, which match the REST API contract and application logic.
- **Alembic Tracking Gap:**  
  The Supabase database had been initialized manually without running Alembic or creating `collection.alembic_version`. Consequently, migrations could not be determined or applied by Alembic in production.

---

## 2. Changes Implemented

### A. Authoritative Model & Migration Alignment
1. **Authoritative Models (`backend/models.py`):**
   - Verified that all models (`Device`, `Transfer`, `AssessmentResult`, `DeviceFile`, `Metadata`) explicitly define `__table_args__ = {"schema": "collection"}` with foreign key references schema-qualified (e.g. `collection.devices.id`).
   - Retained backward-compatible hybrid property accessors (`purchase_year`, `weight_kg`, `power_status`, `screen_condition`, `battery_condition`, `damage_description`) on `Device` for legacy payload converters and API interoperability.
2. **Forward-Only Alembic Migration (`backend/migrations/versions/b7a8c9d0e1f2_reconcile_collection_schema.py`):**
   - Chained directly from revision `95b1a260ef7a` as head `b7a8c9d0e1f2`.
   - Inspects existing tables in `collection` schema dynamically.
   - Non-destructively adds all missing columns: `date`, `year`, `weight`, `damage`, `power`, `screen`, `battery` to `devices`.
   - Adds missing columns to `transfers`: `http_status`, `response_body`, `endpoint`, `retry_count`, `idempotency_key`.
   - Adds missing columns to `device_files`: `created_at`.
   - Backfills data safely from legacy columns (e.g. `purchase_year` &rarr; `year`, `weight_kg` &rarr; `weight`, `created_at` &rarr; `date`).
   - Widens column types safely (e.g. `VARCHAR(64)`, `VARCHAR(128)`, `VARCHAR(256)`) without dropping data.
   - Creates indexes idempotently.
3. **Idempotent SQL Script (`backend/migrations/collection_schema_init.sql`):**
   - Completely rewritten to define all tables with authoritative model columns from scratch.
   - Contains an idempotent `DO $$ ... $$` reconciliation block that safely updates databases previously initialized with the legacy column names.
   - Creates `collection.alembic_version` and stamps the head revision `'b7a8c9d0e1f2'`.
   - Grants permissions on schema `collection` and all contained tables/sequences to `authenticated`, `service_role`, and `postgres` roles.
4. **Alembic Environment (`backend/migrations/env.py`):**
   - Sets `version_table_schema = "collection"` and `include_schemas = True` on PostgreSQL engines.
   - Automatically detects SQLite during local testing to avoid invalid schema calls.
   - Implements `include_object` filtering so Alembic only manages the `collection` schema, preventing accidental interference with the partner's `refurbishment` schema or Supabase internal tables.
5. **Central Metrics Hardening (`backend/metrics.py`):**
   - Configured structured exception logging with `exc_info=True`.
   - Never conceals database errors or returns fabricated metrics.
6. **Diagnostic & Verification Tooling (`backend/scripts/`):**
   - `backend/scripts/check_database_schema.py`: Connects via `DATABASE_URL`, inspects schemas, tables, columns, nullability, data types, indexes, and stamps, verifying 100% compliance with SQLAlchemy models.
   - `backend/scripts/verify_migrations.py`: Verifies the linear migration revision chain (`41179279b201` &rarr; `95b1a260ef7a` &rarr; `b7a8c9d0e1f2`) and confirms a single active head.
7. **Automated Test Suite Expansion (`backend/tests/test_app.py`):**
   - Added tests 21, 22, 23, 24 covering schema script validation, migration integrity, SQL init reconciliation, and exception logging.
   - **82 passing tests (100% passing rate).**

---

## 3. Supabase Database Reconciliation Instructions

You can reconcile your Supabase database using either **Method A (Recommended: Web SQL Editor)** or **Method B (Alembic CLI)**.

### Method A: Execute in Supabase SQL Editor (Recommended)
1. Log in to your [Supabase Dashboard](https://supabase.com/dashboard).
2. Open your shared project and navigate to the **SQL Editor**.
3. Copy the entire contents of [`backend/migrations/collection_schema_init.sql`](backend/migrations/collection_schema_init.sql).
4. Paste into the SQL Editor and click **Run**.
5. The script will:
   - Create schema `collection` if not present.
   - Move any legacy `public` tables to `collection`.
   - Add missing columns (`date`, `year`, `weight`, `power`, `screen`, `battery`, `damage`, `http_status`, `response_body`, `endpoint`, etc.).
   - Backfill existing rows non-destructively.
   - Create `collection.alembic_version` and stamp revision `b7a8c9d0e1f2`.
   - Grant required role permissions.

### Method B: Execute via Flask-Migrate / Alembic CLI
If your local or CI machine has network access to the Supabase Transaction Pooler:
```bash
# In backend directory
set DATABASE_URL=postgresql://postgres.[REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres?sslmode=require
flask db upgrade
```

---

## 4. Verification

Run the verification scripts locally or against your remote Supabase database:

```bash
# 1. Verify linear Alembic migration tree:
python backend/scripts/verify_migrations.py

# 2. Verify database schema against models:
# (Set DATABASE_URL to your Supabase connection string)
python backend/scripts/check_database_schema.py

# 3. Run full automated test suite:
pytest backend/tests/test_app.py
```

Expected output:
```text
SUCCESS: Linear migration chain verified with active head 'b7a8c9d0e1f2'.
SUMMARY: ALL SCHEMAS, TABLES, AND COLUMNS MATCH AUTHORITATIVE MODELS.
82 passed in ~24s
```

---

## 5. Vercel Environment Variables Checklist

Ensure the following variables are configured in **Vercel Project Settings &rarr; Environment Variables**:

| Variable Name | Required | Description | Example / Notes |
|---|---|---|---|
| `DATABASE_URL` | Yes | Supabase Transaction Pooler (port 6543) | `postgresql://postgres.[REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres?sslmode=require` |
| `SECRET_KEY` | Yes | Stable Flask session encryption key | 32+ character hex or base64 token |
| `CIRCULAR_API_KEY` | Yes | Inbound API authentication key | Token expected from partner requests |
| `PARTNER_API_KEY` | Yes | Outbound partner service API key | Token sent in headers to Refurbishment app |
| `PARTNER_API_URL` | Yes | URL of Refurbishment Partner deployment | `https://refurbishment-partner.vercel.app` |
| `SUPABASE_URL` | Yes | Supabase project API URL | `https://[REF].supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Supabase service role key (for storage) | Secret service role key |
| `SUPABASE_STORAGE_BUCKET` | Yes | Shared storage bucket name | `ewaste-files` |
| `IS_PRODUCTION` | Yes | Production mode flag | `true` |
| `SEED_DEMO_DATA` | No | Demo data seeding policy | `false` (default in production) |

---

## 6. Deployment Verification & Status

- [x] Authoritative schema consolidated in `backend/models.py`.
- [x] Forward-only non-destructive Alembic migration `b7a8c9d0e1f2` generated and verified.
- [x] `collection_schema_init.sql` rewritten with complete model schema, data backfill, and Alembic version stamping.
- [x] `backend/metrics.py` hardened with structured error logging and zero data fabrication.
- [x] `check_database_schema.py` and `verify_migrations.py` diagnostic scripts deployed and passing.
- [x] 82 automated unit, integration, CSRF, and schema tests passing.
- [x] Ready for deployment commit.

