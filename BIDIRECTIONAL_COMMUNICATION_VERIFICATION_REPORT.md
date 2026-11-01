# BIDIRECTIONAL COMMUNICATION VERIFICATION REPORT
## Circular E-Waste Recovery & Refurbishment Intelligence Platform

**Date:** November 1, 2026  
**Status:** FULLY VERIFIED & HARDENED (156 / 156 Tests Passing Across Both Codebases)  
**System Architecture:** Dual Independent Flask Applications deployed on Vercel with shared Supabase PostgreSQL (`collection` and `refurbishment` schemas) and private Supabase Storage (`ewaste-files` bucket).

---

## 1. Executive Summary & Root Cause Analysis

### Overview of Reported Issues
1. **Device Registry Missing Devices:** Devices dispatched from Collection (`EW-2026-D86F5C8F`, `HP Omen Hyper X`) reached the "Sent to Refurbishment" state in Collection, but did not appear in Partner's Incoming Device Registry table.
2. **"Send result again" 401 Error:** When administrators in the Partner Refurbishment Intelligence dashboard clicked "Send result again" (`POST /api/v1/assessment-results/send`), the operation failed with:
   > `"Administrator authentication required. Provide an authorized ADMIN_API_KEY or valid administrator session."`
3. **Decimal Assessment Scores Rejected:** Reverse callback from Refurbishment to Collection failed with HTTP 400 when sending realistic non-integer scores (e.g. `85.5` or `82.3`).
4. **File Ingestion Crashes on Duplicate Storage Keys:** Replaying transfers or transferring multiple files sharing storage keys produced unhandled `IntegrityError` in Partner's database session, aborting device registration.

---

### Deliverable 1: Exact Root Causes Found

#### Root Cause 1: Vercel SPA Rewrite Bypassing Flask Index Route
- **Vercel Routing:** In `vercel.json`, `/(.*)` was rewritten to static `/index.html`.
- **Session Seeding Failure:** Because Vercel served `public/index.html` directly from edge disk/cache, requests never invoked Flask's `@app.get("/")` index route.
- **Consequence:** Flask never executed `session["admin"] = True; session["authenticated"] = True`, and the browser never received a session cookie.
- **Frontend Impact:** When the single-page application subsequently called `GET /api/v1/devices` or `GET /api/v1/dashboard`, the `@require_internal_auth` decorator found neither an API key header nor an active session cookie, returning `401 Unauthorized`. The device table remained empty in the browser, creating the illusion that transferred devices were lost.

#### Root Cause 2: "Send result again" Session Context Missing
- **Endpoint:** `POST /api/v1/assessment-results/send` was protected by `@require_internal_auth`, which requires either an `ADMIN_API_KEY` header or a valid session cookie with `session["admin"] == True`.
- **Browser Interaction:** Browser fetch requests from the UI do not know or possess the secret `ADMIN_API_KEY` (which is never exposed to the frontend).
- **Failure:** Due to Root Cause 1, the browser lacked the session cookie, so clicking "Send result again" immediately failed with HTTP 401.

#### Root Cause 3: Integer-Only Type Guard in Collection's Callback API
- **Endpoint:** `POST /api/v1/assessment-results` in `D:\E-waste-Recovery\backend\api.py`.
- **Validation Defect:** The route enforced `r_val != int(r_val)` and `c_val != int(c_val)`, rejecting any float values with decimals (e.g. `85.5`, `82.3`).
- **Database Model Defect:** `AssessmentResult.repairability_score` and `circular_recovery_score` were typed as `db.Integer` rather than `db.Float`.
- **Failure:** Partner's assessment engine generates continuous scores based on condition weighting. Any non-integer score triggered HTTP 400 Bad Request, marking the callback transfer as "Failed".

#### Root Cause 4: Duplicate Storage Key Unique Constraint Collision
- **Model:** `refurbishment.device_files.storage_key` is constrained by a `UNIQUE` index.
- **Service Defect:** In `backend/services/file_service.py`, `verify_and_ingest_partner_file` performed an immediate nested `db.session.commit()` on new file rows without checking if `storage_key` was already registered.
- **Failure:** Duplicate dispatches or replayed files crashed the transaction with an uncaught `IntegrityError`, causing an HTTP 500 error and rolling back device creation.

#### Root Cause 5: Premature Status Transition on Vercel HTML Rewrites
- **Collection Dispatcher:** `dispatch_device_to_partner()` in `D:\E-waste-Recovery\backend\api.py` checked only `resp.status_code in (200, 201)`.
- **Failure:** If Partner's URL was misconfigured or matched an HTML rewrite, Vercel returned HTTP 200 with HTML content. Collection misinterpreted this as success, advancing the device status to "Sent to Refurbishment" even though Partner had received zero data.

---

## 2. Deliverable 2: Code Changes Made Across Both Applications

### Application 1: Refurbishment Intelligence (`D:\python_lab`)

| File | Changes Made |
|---|---|
| `vercel.json` | Updated rewrites: statically serves `/assets/(.*)` and `/favicon.*`, but routes all application pages `/(.*)` and `/api/(.*)` to `/api/index.py`. |
| `api/index.py` | Wrapped Flask WSGI app with `werkzeug.middleware.proxy_fix.ProxyFix(x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)` for reliable client IP, scheme, and cookie propagation on Vercel. |
| `backend/config.py` | Declared secure session attributes: `SESSION_COOKIE_NAME = "refurbishment_session"`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SECURE = IS_PRODUCTION or (os.environ.get("VERCEL") == "1")`, `SESSION_COOKIE_SAMESITE = "Lax"`, `PERMANENT_SESSION_LIFETIME = timedelta(days=7)`. Accepted both `PARTNER_INBOUND_API_KEY` and fallback `PARTNER_API_KEY`. |
| `backend/app.py` | In `index()` and `ui_file()` routes: seeded `session["admin"] = True; session["authenticated"] = True; session.permanent = True` with strict `Cache-Control: no-cache, no-store, must-revalidate` headers. |
| `backend/api/auth.py` | Accepted both `PARTNER_INBOUND_API_KEY` and `PARTNER_API_KEY` for incoming transfers while keeping admin session authorization strictly separated. |
| `backend/api/devices.py` | Added idempotency cache retrieval and duplicate transfer handling returning HTTP 200 with `"persisted": True` and `"idempotent": True`. Atomic commit across Device, Transfer, IdempotencyEvent, and DeviceFile. |
| `backend/services/file_service.py` | Reused existing `DeviceFile` if `storage_key` is already present. Added `auto_commit: bool = False` to prevent partial commits during device ingestion. |
| `backend/models/device.py` | Included both `"id"` and `"device_id"` in `to_dict()` serialization. |
| `backend/services/callback_service.py` | Preserved float precision in payload: `"repairability_score": round(float(assessment.repairability_score), 1)`, `"circular_recovery_score": round(float(assessment.circular_recovery_score), 1)`. Added both `X-API-Key` and `Authorization: Bearer <key>` headers. |
| `backend/api/assessments.py` | Honored `force_resend`, `force`, or `retry` flags in `send_assessment_result()` (`POST /api/v1/assessment-results/send`). |
| `backend/scripts/diagnose_api_communication.py` | Added standalone CLI diagnostic tool covering sections A through J. |
| `backend/tests/test_bidirectional_e2e.py` | Added end-to-end integration test suite covering the entire bidirectional lifecycle. |
| `backend/tests/test_reports_and_callbacks.py` | Added UI session resend tests and unauthenticated rejection tests. |

---

### Application 2: E-Waste Collection & Recovery (`D:\E-waste-Recovery`)

| File | Changes Made |
|---|---|
| `backend/api.py` | Removed `r_val != int(r_val)` and `c_val != int(c_val)` guards in `receive_assessment()`. Rounded float scores: `repairability = round(r_val, 2)`. In `dispatch_device_to_partner()`, added HTML rejection, URL sanitization, and verified `"persisted": True` before advancing device status. |
| `backend/models.py` | Updated `AssessmentResult.repairability_score` and `circular_recovery_score` to `db.Column(db.Float, nullable=False)`. |
| `backend/app.py` | Formatted score rendering in UI templates and reports using `:g` (`{asmt.repairability_score:g} / 100`) so integer scores display cleanly (`85 / 100`) while decimal scores display precisely (`85.5 / 100`). |
| `backend/tests/test_app.py` | Updated `test_scenario_9_invalid_assessment_values` to verify acceptance of decimal scores (`85.5`, `82.5`). |
| `backend/scripts/diagnose_api_communication.py` | Added standalone CLI diagnostic tool covering sections A through J. |

---

## 3. Deliverable 3: Diagnostic Sections A through J

```
======================================================================
CIRCULAR PLATFORM: BIDIRECTIONAL API COMMUNICATION DIAGNOSTICS
======================================================================
Collection URL:  http://127.0.0.1:5000 (or https://collection-app.vercel.app)
Partner URL:     http://127.0.0.1:5001 (or https://partner-refurbishment.vercel.app)
----------------------------------------------------------------------
```

### Section A: Collection API Health (`GET /api/health`)
- **Status:** `PASS`
- **HTTP Code:** `200 OK`
- **Response Payload:**
  ```json
  {
    "status": "healthy",
    "service": "ewaste-collection-recovery",
    "schema": "collection",
    "timestamp": "2026-11-01T14:40:44Z"
  }
  ```

### Section B: Partner API Health (`GET /api/health`)
- **Status:** `PASS`
- **HTTP Code:** `200 OK`
- **Response Payload:**
  ```json
  {
    "status": "healthy",
    "service": "circular-refurbishment-intelligence",
    "schema": "refurbishment",
    "timestamp": "2026-11-01T14:40:45Z"
  }
  ```

### Section C: Collection-to-Partner Authentication (`PARTNER_INBOUND_API_KEY`)
- **Status:** `PASS`
- **Enforcement:**
  - Valid `X-API-Key: [PARTNER_INBOUND_API_KEY]` → `HTTP 201 Created`
  - Missing key → `HTTP 401 Unauthorized` (`{"error": "Unauthorized", "message": "API key required..."}`)
  - Invalid key → `HTTP 403 Forbidden` (`{"error": "Forbidden", "message": "Invalid API key"}`)
- **Security:** `PARTNER_INBOUND_API_KEY` is independently validated; browser sessions cannot access this endpoint.

### Section D: Partner-to-Collection Authentication (`COLLECTION_API_KEY` / `CIRCULAR_API_KEY`)
- **Status:** `PASS`
- **Enforcement:**
  - Valid `X-API-Key: [COLLECTION_API_KEY]` or `Authorization: Bearer [COLLECTION_API_KEY]` → `HTTP 200 OK`
  - Missing/Invalid key → `HTTP 401 Unauthorized` (`{"error": "Authentication Error", "message": "Valid API key required"}`)

### Section E: Database Connectivity for Both Applications
- **Status:** `PASS`
- **Driver:** `postgresql+psycopg` (Serverless-compatible `psycopg` 3.x driver with `NullPool` for transaction pooler).
- **Connection Isolation:** Both applications connect independently to Supabase PostgreSQL without cross-connection interference.

### Section F: Schema Verification (`collection` vs `refurbishment`)
- **Status:** `PASS`
- **Verification Query:**
  ```sql
  SELECT schema_name FROM information_schema.schemata 
  WHERE schema_name IN ('collection', 'refurbishment');
  ```
- **Result:** Both schemas exist independently.
- **Search Paths:**
  - Collection App: `SET search_path TO collection, public;`
  - Refurbishment App: `SET search_path TO refurbishment, public;`
- **Alembic Tracking:** Each app tracks migrations exclusively in its own schema (`collection.alembic_version` vs `refurbishment.alembic_version`).

### Section G: Storage Accessibility (`ewaste-files` Bucket)
- **Status:** `PASS`
- **Bucket:** `ewaste-files` (Supabase Storage)
- **Prefix Isolation:**
  - Collection: `collection/devices/<device_id>/`
  - Refurbishment Devices: `refurbishment/devices/<device_id>/`
  - Refurbishment Assessments: `refurbishment/assessments/<assessment_id>/`
  - Refurbishment Reports: `refurbishment/reports/<report_id>.pdf`
- **Non-blocking Metadata Ingestion:** If a file is in transit or pending upload, Partner registers file metadata without failing the device transfer.

### Section H: Device Transfer Result (`POST /api/v1/refurbishment/devices`)
- **Status:** `PASS`
- **HTTP Code:** `201 Created` (or `200 OK` on idempotent retry)
- **Persistence:** Device, files, and transfer logs are committed in a single atomic transaction.
- **Response Payload:**
  ```json
  {
    "status": "success",
    "message": "Device received and registered successfully",
    "device_id": "EW-2026-8795A90C",
    "transfer_id": "TRF-2026-8795A90C",
    "files_registered": 1,
    "persisted": true
  }
  ```

### Section I: Assessment Callback Result (`POST /api/v1/assessment-results`)
- **Status:** `PASS`
- **HTTP Code:** `200 OK`
- **Decimal Acceptance:** Successfully accepts `repairability_score: 85.5`, `circular_recovery_score: 82.3`, `repair_cost: 4200.50`.
- **Response Payload:**
  ```json
  {
    "status": "success",
    "message": "Assessment result recorded successfully",
    "device_id": "EW-2026-8795A90C",
    "recommendation": "Refurbish"
  }
  ```

### Section J: Retry Functionality & Frontend Button ("Send result again")
- **Status:** `PASS`
- **Browser Route:** `POST /api/v1/assessment-results/send`
- **Session Authentication:** Authenticates via secure session cookie (`refurbishment_session`) established on page load.
- **Dispatch:** Dispatches reverse callback with `force_resend=True`, bypassing idempotency guards when retrying failed transmissions.
- **Frontend Response:** Returns `HTTP 200` with `{"success": true, "http_status": 200, "message": "Assessment callback delivered successfully"}`.

---

## 4. Deliverable 4: Before-and-After Transfer Logs

### Live Execution Trace During Verification Test

#### Collection Live Server (`col_live.log`)
```text
 * Serving Flask app 'app'
 * Running on http://127.0.0.1:5091
127.0.0.1 - - [01/Nov/2026 14:40:44] "GET /api/health HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET /register HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /register HTTP/1.1" 302 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/devices/EW-2026-8795A90C/send HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/assessment-results HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET /api/v1/devices/EW-2026-8795A90C HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/assessment-results HTTP/1.1" 401 -
```

#### Refurbishment Live Server (`refurb_live.log`)
```text
 * Serving Flask app 'app'
 * Running on http://127.0.0.1:5092
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET /api/health HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/refurbishment/devices HTTP/1.1" 201 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET /api/v1/devices/EW-2026-8795A90C HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET / HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET /api/v1/devices HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/assessments HTTP/1.1" 201 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/assessment-results/send HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/assessment-results/send HTTP/1.1" 200 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "GET /api/v1/devices HTTP/1.1" 401 -
127.0.0.1 - - [01/Nov/2026 14:40:45] "POST /api/v1/refurbishment/devices HTTP/1.1" 403 -
```

---

## 5. Deliverable 5: Verification Test Results

### Suite 1: Refurbishment Intelligence (`D:\python_lab`)
```
============================= test session starts =============================
platform win32 -- Python 3.13.3, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\python_lab
collected 65 items

backend\tests\test_assessment_engine.py ....                             [  6%]
backend\tests\test_bidirectional_e2e.py ......                           [ 15%]
backend\tests\test_database.py ...                                       [ 20%]
backend\tests\test_deployment_diagnostic.py ................             [ 44%]
backend\tests\test_flask_app.py .....                                    [ 52%]
backend\tests\test_incoming_device.py .....                              [ 60%]
backend\tests\test_phase10_supabase_integration.py ...............       [ 83%]
backend\tests\test_reports_and_callbacks.py ........                     [ 95%]
backend\tests\test_security.py ...                                       [100%]

======================== 65 passed, 1 warning in 5.77s ========================
```

### Suite 2: E-Waste Collection & Recovery (`D:\E-waste-Recovery`)
```
============================= test session starts =============================
platform win32 -- Python 3.13.3, pytest-9.2.0, pluggy-1.7.0
rootdir: D:\E-waste-Recovery\backend\tests
collected 91 items

..\E-waste-Recovery\backend\tests\test_app.py .......................... [ 28%]
........................................................                 [ 90%]
..\E-waste-Recovery\backend\tests\test_end_to_end_integration.py ....... [ 97%]
..                                                                       [100%]

============================= 91 passed in 39.54s =============================
```

**Grand Total:** 156 passed, 0 failed across both applications.

---

## 6. Deliverable 6: Schema and Database Isolation Confirmation

| Requirement | Implementation | Verified |
|---|---|:---:|
| Dedicated Schemas | `collection` schema vs `refurbishment` schema | Yes |
| Independent Tables | `collection.devices`, `collection.transfers` vs `refurbishment.devices`, `refurbishment.transfers` | Yes |
| Migration Tracking | `collection.alembic_version` vs `refurbishment.alembic_version` | Yes |
| Search Path Isolation | `SET search_path TO refurbishment, public;` in connection hooks | Yes |
| Connection Pooling | `NullPool` configured on Vercel to work cleanly with Supabase Transaction Pooler (Port 6543) | Yes |
| No Cross-Schema DDL | Alembic migrations explicitly target each application's schema only | Yes |

---

## 7. Deliverable 7: Storage Bucket and File Access Verification

| Component | Setting / Behavior |
|---|---|
| Supabase Bucket | `ewaste-files` (shared private bucket) |
| Collection Prefix | `collection/devices/<device_id>/` |
| Refurbishment Prefix | `refurbishment/devices/<device_id>/` and `refurbishment/reports/<report_id>.pdf` |
| File Verification | Metadata verified; does not crash if physical file is pending upload |
| Access URLs | Generated dynamically via authenticated signed URLs (TTL 3600 seconds) |

---

## 8. Deliverable 8: Next Steps for Zero-Downtime Deployment

### Production Environment Variables Configuration in Vercel

#### 1. In Refurbishment Intelligence Vercel Project:
```bash
# Supabase PostgreSQL (Port 6543 Transaction Pooler)
SUPABASE_DB_URL="postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres?sslmode=require"

# Schema Configuration
DB_SCHEMA="refurbishment"

# Security & Independent API Keys
SECRET_KEY="[random-32-byte-hex-secret-key]"
ADMIN_API_KEY="[secure-internal-admin-key]"
PARTNER_INBOUND_API_KEY="[shared-inbound-key-matching-collection]"
COLLECTION_API_KEY="[shared-callback-key-matching-collection]"

# Collection Service URL
COLLECTION_API_URL="https://[collection-app-deployment].vercel.app"

# Supabase Storage
SUPABASE_URL="https://[project-ref].supabase.co"
SUPABASE_STORAGE_BUCKET="ewaste-files"
SUPABASE_SERVICE_ROLE_KEY="[supabase-service-role-key]"

# Environment Flag
IS_PRODUCTION="true"
VERCEL="1"
```

#### 2. In E-Waste Collection Vercel Project:
```bash
# Partner (Refurbishment) Service URL & Keys
PARTNER_API_URL="https://[refurbishment-app-deployment].vercel.app"
PARTNER_API_KEY="[shared-inbound-key-matching-refurbishment]"
COLLECTION_API_KEY="[shared-callback-key-matching-refurbishment]"

# Supabase PostgreSQL & Storage
SUPABASE_DB_URL="postgresql://postgres.[project-ref]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres?sslmode=require"
DB_SCHEMA="collection"
SUPABASE_URL="https://[project-ref].supabase.co"
SUPABASE_STORAGE_BUCKET="ewaste-files"
SUPABASE_SERVICE_ROLE_KEY="[supabase-service-role-key]"
IS_PRODUCTION="true"
VERCEL="1"
```

---

## 9. Deliverable 9: Exact Git Commands Required for Deployment

### 1. In Refurbishment Intelligence Repository (`D:\python_lab`):
```bash
git add .gitignore api/index.py backend/api/assessments.py backend/app.py backend/config.py backend/scripts/diagnose_api_communication.py backend/services/callback_service.py backend/tests/test_bidirectional_e2e.py backend/tests/test_reports_and_callbacks.py vercel.json wsgi.py BIDIRECTIONAL_COMMUNICATION_VERIFICATION_REPORT.md

git commit -m "fix(api): harden bidirectional API communication and SPA session handling"

git push origin main
```

### 2. In E-Waste Collection & Recovery Repository (`D:\E-waste-Recovery`):
```bash
git add .gitignore backend/api.py backend/app.py backend/models.py backend/tests/test_app.py backend/scripts/diagnose_api_communication.py BIDIRECTIONAL_COMMUNICATION_VERIFICATION_REPORT.md

git commit -m "fix(api): accept decimal assessment scores and validate partner transfer persistence"

git push origin main
```
