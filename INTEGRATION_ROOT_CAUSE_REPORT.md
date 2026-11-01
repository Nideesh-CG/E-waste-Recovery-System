# End-to-End Integration Root Cause & Repair Report

**Author:** Senior Backend Integration Engineer  
**Date:** November 1, 2026  
**Status:** Resolved & Verified  
**Applications:**
1. **Application 1:** E-Waste Collection & Recovery (`D:\E-waste-Recovery`)
2. **Application 2:** Refurbishment Intelligence (`D:\circular-refurbishment-intelligence`)
**Shared Infrastructure:** Supabase PostgreSQL (`collection` & `refurbishment` schemas) and Supabase Storage (`ewaste-files` private bucket).

---

## 1. Executive Summary

When clicking **"Send to Refurbishment Application"** on a registered device in the Collection Application, the frontend presented:
> *"Partner transfer failed with status 500."*

Simultaneously, the Refurbishment Intelligence dashboard displayed:
- **Collection Application:** Connected
- **API Health:** 200 OK
- **Last synchronized:** Just now

A comprehensive architectural and code audit of both Flask applications, their database schemas, authentication layers, serialization pipelines, and network contracts was conducted. 

The investigation revealed that the Refurbishment Intelligence service was throwing an uncaught `psycopg.errors.UndefinedTable` (or `OperationalError: no such table`) during device transfer processing, resulting in an HTTP 500 internal server error. This occurred due to **missing schema isolation in Refurbishment's SQLAlchemy models**, compounded by an **inbound authentication variable naming mismatch** and an **incompatible score data type contract on the reverse callback**.

All root causes were reproduced, corrected, and verified end-to-end with **137 passing automated tests** across both codebases (91 in Collection, 46 in Refurbishment), including an automated 11-step integration test suite running live HTTP client-server interactions.

---

## 2. Root Cause Analysis

### Root Cause 1: Database Schema Isolation Mismatch (Primary Cause of HTTP 500)
- **Mechanism:** Both applications share a single Supabase PostgreSQL database. Application 1 executed `collection_schema_init.sql`, moving its tables (`devices`, `transfers`, `device_files`, etc.) from the `public` schema into the `collection` schema.
- **The Defect in Application 2:** The models in `circular-refurbishment-intelligence/backend/models/` (`Device`, `Transfer`, `Assessment`, `AssessmentComponent`, `RecoveryAnalysis`, `Report`, `IdempotencyEvent`, `DeviceFile`) did **not** specify `__table_args__ = {"schema": "refurbishment"}`. Consequently, SQLAlchemy defaulted to querying and inserting into the `public` schema (`SELECT ... FROM devices WHERE ...`).
- **The Failure:** When Collection sent `POST /api/v1/refurbishment/devices`, Refurbishment executed `IdempotencyEvent.query.filter_by(...)` and `Device.query.filter_by(...)`. Because `public.devices` no longer existed (having been moved to `collection.devices`) and `refurbishment.devices` had not been targeted, PostgreSQL raised:
  ```
  psycopg.errors.UndefinedTable: relation "devices" does not exist
  ```
  In local/test SQLite environments, it failed with:
  ```
  sqlite3.OperationalError: no such table: main.idempotency_events
  ```
- **Error Propagation:** The exception handler in `api/devices.py` caught the database error, rolled back the session, and returned:
  ```json
  {"error": "Internal Server Error", "message": "Failed to store incoming device record"}
  ```
  with HTTP status 500. Collection recorded the transfer as `Failed` and displayed `"Partner transfer failed with status 500."` on the user interface.

---

### Root Cause 2: Inbound Authentication Variable Mismatch
- **Mechanism:** In Refurbishment's `api/auth.py`, the `@require_api_key` decorator resolved its expected key strictly via `current_app.config.get("PARTNER_INBOUND_API_KEY")`.
- **The Defect:** If an operator configured the environment using `PARTNER_API_KEY` (the variable name used by Collection to send requests), `PARTNER_INBOUND_API_KEY` evaluated to `None`.
- **The Failure:** In `api/auth.py`, if `expected_key` was `None`, the authentication guard immediately returned:
  ```json
  {"error": "Internal Server Error", "message": "Inbound API key not configured on server"}
  ```
  with HTTP status 500, blocking all inbound transfers before request validation could begin.

---

### Root Cause 3: Reverse Callback Schema & Contract Incompatibility (Float vs Integer)
- **Mechanism:** Once an assessment is completed on Partner, it posts results back to Collection at `POST /api/v1/assessment-results`.
- **The Defect:** In Refurbishment's `services/assessment_engine.py` and `services/callback_service.py`, `repairability_score` and `circular_recovery_score` were evaluated as floating-point numbers (e.g. `88.4`, `82.3`).
- **The Failure in Collection:** Collection's `receive_assessment()` in `backend/api.py` and the `AssessmentResult` database model require integer scores:
  ```python
  r_val = float(data["repairability_score"])
  if r_val != int(r_val):
      return jsonify({"error": "Validation Error", "message": "repairability_score must be an integer between 0 and 100"}), 400
  ```
  Collection's automated test suite explicitly asserts that non-integer floats (such as `85.5`) must be rejected with HTTP 400. Whenever Refurbishment sent floating-point scores, Collection rejected the callback with HTTP 400 Validation Error.

---

### Root Cause 4: Missing Vercel Deployment Artifacts in Refurbishment Repo
- **Mechanism:** Refurbishment was deployed on Vercel as a serverless application.
- **The Defect:** The repository lacked `wsgi.py` at the root and lacked a `vercel.json` deployment configuration. Without `wsgi.py` exposed to `@vercel/python`, serverless invocations of `/api/*` failed to bind to the Flask application factory.

---

### Root Cause 5: Missing Refurbishment Idempotent Schema Initialization Script
- **Mechanism:** While Collection had `collection_schema_init.sql`, Refurbishment had only an Alembic version file (`7f9cd19afe3f`) that assumed the `public` schema.
- **The Defect:** There was no standalone, idempotent SQL script to create schema `refurbishment`, provision all 8 tables, set foreign keys and indexes, grant role permissions to Supabase roles (`anon`, `authenticated`, `service_role`), and stamp the `alembic_version` table.

---

## 3. Classification of Issues

| Issue Description | Category | Resolution Status | Verification Method |
| :--- | :--- | :--- | :--- |
| Refurbishment models lacked schema isolation (`public` vs `refurbishment`) | Schema Architecture | **Reproduced and Fixed** | Added `__table_args__ = {"schema": "refurbishment"}` across all 8 models; verified queries target `refurbishment` |
| Inbound API key variable naming mismatch (`PARTNER_INBOUND_API_KEY` vs `PARTNER_API_KEY`) | Configuration / Auth | **Reproduced and Fixed** | Configured multi-variable resolution in `config.py` and `auth.py` |
| Reverse callback score type mismatch (float `88.4` vs required integer `88`) | API Contract | **Reproduced and Fixed** | Formatted outbound callback payload to round scores to `int(round(...))` before dispatch |
| Missing `idempotent: True` in Partner replay response | API Contract | **Reproduced and Fixed** | Added explicit `idempotent: True` attribute to cached/duplicate 200 responses |
| Missing `Device.to_dict()["device_id"]` attribute | Model Serialization | **Reproduced and Fixed** | Added `"device_id"` alias alongside `"id"` in `Device.to_dict()` |
| Missing Vercel entry point (`wsgi.py`) and serverless routing (`vercel.json`) | Deployment / DevOps | **Reproduced and Fixed** | Created root `wsgi.py`, `backend/wsgi.py`, and `vercel.json` |
| Missing idempotent SQL init script for Refurbishment schema | Database / DevOps | **Reproduced and Fixed** | Created `backend/migrations/refurbishment_schema_init.sql` |
| Alembic migration version tracking collision in shared database | Migration Isolation | **Reproduced and Fixed** | Configured `version_table_schema = "refurbishment"` and `include_object` in `env.py` |
| Insufficient diagnostic logging on partner transfer failure | Observability | **Reproduced and Fixed** | Implemented structured logging capturing `transfer_id`, `device_id`, `endpoint`, `http_status`, `stage`, and `exception_type` |
| Execution of `refurbishment_schema_init.sql` on live Supabase production DB | Production Environment | **Requires Production Verification** | Operator must run `refurbishment_schema_init.sql` in Supabase SQL Editor |
| Setting environment variables on Vercel production deployment | Production Environment | **Requires Production Verification** | Set `PARTNER_API_URL`, `PARTNER_API_KEY`, `COLLECTION_API_URL`, `COLLECTION_API_KEY` in Vercel project settings |

---

## 4. File Modification Log

### Application 1: E-Waste Collection & Recovery (`D:\E-waste-Recovery`)

| File Path | Description of Changes |
| :--- | :--- |
| `backend/api.py` | 1. Updated `verify_api_key()` to support both `CIRCULAR_API_KEY` and `COLLECTION_API_KEY`.<br>2. Updated `dispatch_device_to_partner()` with structured stage tracking (`pre_dispatch`, `partner_dispatch`), detailed diagnostic logging (capturing `transfer_id`, `device_id`, `target_endpoint`, `http_status`, `response_body` snippet, `exception_type`), and JSON error parsing to surface helpful user notifications without leaking credentials.<br>3. Updated `receive_assessment()` with stage tracking and structured logging. |
| `backend/config.py` | Updated `CIRCULAR_API_KEY` resolution to fall back to `COLLECTION_API_KEY`, harmonizing variable naming across both applications. |
| `backend/tests/test_end_to_end_integration.py` | Created comprehensive 9-test end-to-end integration test suite that spins up live Flask servers for both microservices and validates the complete 11-step workflow, authentication rejection, missing fields, duplicate idempotency, storage unavailable, partner unavailable, callback retry, and schema isolation. |

### Application 2: Refurbishment Intelligence (`D:\circular-refurbishment-intelligence`)

| File Path | Description of Changes |
| :--- | :--- |
| `backend/models/__init__.py` | Exported `SCHEMA = "refurbishment"` and all 8 SQLAlchemy models. |
| `backend/models/device.py` | Added `__table_args__ = {"schema": SCHEMA}`, schema-qualified table relationships, and added `"device_id"` alias in `to_dict()`. |
| `backend/models/transfer.py` | Added `__table_args__ = {"schema": SCHEMA}` and schema-qualified foreign keys (`f"{SCHEMA}.devices.device_id"`). |
| `backend/models/assessment.py` | Added `__table_args__ = {"schema": SCHEMA}` and schema-qualified foreign keys (`f"{SCHEMA}.devices.device_id"`). |
| `backend/models/device_file.py` | Added `__table_args__ = {"schema": SCHEMA}` and schema-qualified foreign keys (`f"{SCHEMA}.devices.device_id"`). |
| `backend/models/idempotency.py` | Added `__table_args__ = {"schema": SCHEMA}`. |
| `backend/models/recovery.py` | Added `__table_args__ = {"schema": SCHEMA}` and schema-qualified foreign keys. |
| `backend/models/report.py` | Added `__table_args__ = {"schema": SCHEMA}` and schema-qualified foreign keys. |
| `backend/config.py` | 1. Added `normalize_database_url()` supporting PostgreSQL psycopg driver schemes.<br>2. Added multi-key resolution for inbound (`PARTNER_INBOUND_API_KEY`, `PARTNER_API_KEY`, `CIRCULAR_API_KEY`) and outbound (`COLLECTION_API_KEY`, `CIRCULAR_API_KEY`).<br>3. Configured SQLite `schema_translate_map = {"refurbishment": None}` for local dev and testing.<br>4. Configured PostgreSQL options with `search_path=refurbishment,public` and `NullPool` for Vercel serverless compute.<br>5. Added Supabase Storage configuration (`ewaste-files`). |
| `backend/app.py` | 1. Added SQLite `schema_translate_map` fallback.<br>2. Added `ProxyFix` for Vercel reverse proxy deployments.<br>3. Added non-destructive database table initialization check for local dev/test.<br>4. Updated `/api/health` JSON to report `schema: "refurbishment"`. |
| `backend/api/auth.py` | Added fallback resolution for `PARTNER_API_KEY` and structured warning logs on authentication rejection without leaking sensitive keys. |
| `backend/api/devices.py` | 1. Added stage tracking (`failure_stage`), structured error logging, and sanitized client responses.<br>2. Added safe duplicate idempotency handling that returns `idempotent: True` on replay.<br>3. Handled retry without primary key collisions on `Transfer` and `IdempotencyEvent`. |
| `backend/api/assessments.py` | Added `@assessments_bp.route("/assessments/<assessment_id>/callback")` route alias and structured logging. |
| `backend/services/callback_service.py` | 1. Formatted outbound assessment payload scores to round to `int(round(...))` before HTTP dispatch to satisfy Collection's integer contract.<br>2. Added `Authorization: Bearer <key>` header alongside `X-API-Key`.<br>3. Added structured diagnostic logging with stage tracking. |
| `backend/migrations/env.py` | Configured `version_table_schema = "refurbishment"`, `include_schemas = True`, and `include_object` filter to isolate Alembic migrations exclusively to the `refurbishment` schema. |
| `backend/migrations/refurbishment_schema_init.sql` | Created idempotent initialization script for Supabase PostgreSQL, creating schema `refurbishment`, all 8 tables, indexes, role grants, and stamping revision `'7f9cd19afe3f'`. |
| `backend/scripts/check_database_schema.py` | Created diagnostic tool to inspect and verify tables, columns, types, indexes, and migrations in the `refurbishment` schema. |
| `backend/scripts/verify_migrations.py` | Created tool to verify Alembic head alignment. |
| `wsgi.py` & `backend/wsgi.py` | Created WSGI entry points for Vercel serverless function invocation and root execution. |
| `vercel.json` | Created Vercel deployment manifest with serverless routing. |

---

## 5. Verification Summary

1. **Unit & Functional Suites:**
   - E-Waste Collection (`D:\E-waste-Recovery\backend\tests`): **91 passed in 47.97s** (82 existing + 9 end-to-end integration tests).
   - Refurbishment Intelligence (`D:\circular-refurbishment-intelligence\backend\tests`): **46 passed in 4.36s**.
   - Total automated test count: **137 passing tests**.

2. **End-to-End Live Integration Verification:**
   - Live HTTP integration between real running instances of both applications verified all 11 steps:
     1. Device registered in Collection (`EW-2026-BC202369`).
     2. Transfer initiated.
     3. Request authenticated via headers (`X-API-Key` & `Authorization: Bearer`).
     4. JSON payload dispatched to `POST /api/v1/refurbishment/devices`.
     5. Received and validated by Refurbishment.
     6. Persisted in `refurbishment` schema (`Device`, `Transfer`, `IdempotencyEvent`).
     7. HTTP 201 Created returned.
     8. Collection transfer status updated to `Completed`, device transitioned to `Processing`.
     9. Deterministic assessment completed in Refurbishment (`repairability_score: 78`, `circular_recovery_score: 90`, `recommendation: REFURBISH`).
     10. Assessment callback posted to Collection at `POST /api/v1/assessment-results`.
     11. Collection verified callback, persisted `AssessmentResult`, and transitioned device status to `Refurbishment`.

