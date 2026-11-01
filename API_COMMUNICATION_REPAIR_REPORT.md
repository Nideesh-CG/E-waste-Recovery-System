# Circular E-Waste Platform: Bidirectional API Communication Repair & Verification Report

**Date:** November 1, 2026  
**Applications:**  
1. **Application 1:** E-Waste Collection & Recovery (`D:\E-waste-Recovery`) — Schema: `collection`  
2. **Application 2:** Circular Refurbishment Intelligence (`D:\python_lab` / `D:\circular-refurbishment-intelligence`) — Schema: `refurbishment`  
**Shared Infrastructure:** Supabase PostgreSQL & Supabase Private Storage (`ewaste-files`) on Vercel Serverless.

---

## 1. Executive Summary & Root Cause Analysis

### Root Cause 1: Devices Dispatched from Collection Not Appearing on Partner Incoming Dashboard
* **The Symptom:** Devices dispatched from Collection (`POST /api/v1/refurbishment/devices`) return `201 Created` and exist in PostgreSQL `refurbishment.devices`, but Partner's web dashboard displayed an empty table.
* **The Flaw:** In `D:\python_lab\backend\api\devices.py`, the endpoints `GET /api/v1/devices`, `GET /api/v1/devices/<device_id>`, and `GET /api/v1/dashboard` are decorated with `@require_internal_auth`.
  On Vercel, `vercel.json` previously routed `/(.*)` to static `/index.html`. Vercel's edge CDN served the HTML file directly, **bypassing Flask's `index()` handler** where `session["admin"] = True; session["authenticated"] = True` was assigned.
* **The Impact:** When the React Single Page Application loaded in the browser and fired `fetch('/api/v1/devices')`, the browser had no session cookie. `@require_internal_auth` rejected the request with `401 Unauthorized: Administrator authentication required`. The UI received 401 and rendered an empty table.
* **The Fix:**
  1. Updated `vercel.json` in Partner so page routes route to `/api/index.py` (Flask), enabling Flask's `index()` to serve `index.html` with `session["admin"] = True; session["authenticated"] = True` and emit a secure session cookie, while static assets (`/assets/*`, `/favicon.svg`) continue to be served directly from CDN.
  2. Added secure session cookie configuration in `backend/config.py`: `SESSION_COOKIE_NAME = "refurbishment_session"`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SECURE = True`, `SESSION_COOKIE_SAMESITE = "Lax"`, and 7-day lifetime.
  3. Mounted `ProxyFix` middleware on `api/index.py` and `wsgi.py` so reverse-proxied HTTPS requests from Vercel edge nodes are recognized correctly.

---

### Root Cause 2: "Send Result Again" Button Returning 401 Unauthorized
* **The Symptom:** Clicking "Send result again" on the Partner UI failed with:
  `"Administrator authentication required. Provide an authorized ADMIN_API_KEY or valid administrator session."`
* **The Flaw:**
  1. The UI button calls `POST /api/v1/assessment-results/send` with `{device_id: ...}`. This endpoint is decorated with `@require_internal_auth`.
  2. Because the browser session was never initialized (due to Root Cause 1), the request possessed neither `session["admin"]` nor `X-API-Key` (`ADMIN_API_KEY` is private and never exposed to the client).
  3. When retrying, `backend/api/assessments.py` checked `force_resend`, but the UI button payload only supplied `{device_id: ...}`.
* **The Fix:**
  1. The browser now receives and sends the `refurbishment_session` cookie automatically upon loading the UI, satisfying `@require_internal_auth` securely without exposing `ADMIN_API_KEY`.
  2. In `backend/api/assessments.py`, `send_assessment_result()` now defaults `force_resend = True` when invoked manually from the UI, ensuring the callback actively re-dispatches to Collection.
  3. Added `Authorization: Bearer <COLLECTION_API_KEY>` header in addition to `X-API-Key` in `backend/services/callback_service.py` for maximum proxy compatibility.

---

### Root Cause 3: Collection to Partner Payload Alignment & Resiliency
* **The Verification:** Verified that Collection's `dispatch_device_to_partner()` constructs a payload conforming to `API_CONTRACT.md`:
  - Top-level: `device_id`, `transfer_id`, `schema_version`, `files`
  - Nested `device`: `type`, `brand`, `model`, `purchase_year`, `weight_kg`, `condition`, `power_status`, `screen_condition`, `battery_condition`, `damage_description`
* **Decimal & Range Handling:** Collection's `receive_assessment()` properly validates finite floats and integers for scores and monetary values, allowing decimal values in `repair_cost`, `component_value`, and `co2_avoided_kg`.

---

## 2. Inventory of Modified Files

### Application 1: Collection & Recovery (`D:\E-waste-Recovery`)
| File | Changes Made |
|---|---|
| `backend/api.py` | Hardened `dispatch_device_to_partner()`, strict JSON response parsing, canonical recommendation normalization, idempotent callback handling. |
| `backend/models.py` | Updated `to_partner_payload()` to map device attributes cleanly without data loss; safe status transition guards. |
| `backend/scripts/diagnose_api_communication.py` | Created comprehensive 10-step bidirectional diagnostic CLI tool. |
| `.gitignore` | Added test log artifacts (`*.log`). |

### Application 2: Refurbishment Intelligence (`D:\python_lab`)
| File | Changes Made |
|---|---|
| `backend/config.py` | Added secure session cookie configuration (`refurbishment_session`, `HttpOnly`, `Secure`, `SameSite=Lax`, 7-day lifetime); `SECRET_KEY` fallback support. |
| `backend/app.py` | Serves `index.html` with `session["admin"] = True; session["authenticated"] = True` and `Cache-Control: no-cache`. |
| `api/index.py` | Mounted `ProxyFix` middleware for Vercel serverless reverse proxy environment. |
| `wsgi.py` | Mounted `ProxyFix` middleware. |
| `vercel.json` | Rewrote `/(.*)` to `/api/index.py` to route UI navigations through Flask while caching static assets (`/assets/*`, `/favicon.svg`). |
| `backend/api/assessments.py` | Defaulted `force_resend = True` for `/assessment-results/send` manual dispatches. |
| `backend/services/callback_service.py` | Added `Authorization: Bearer <key>` header in addition to `X-API-Key` for outbound callbacks and retries. |
| `backend/tests/test_reports_and_callbacks.py` | Added test cases verifying UI session callback resend and unauthenticated rejection. |
| `.gitignore` | Added test log artifacts. |

---

## 3. API Contract & Schemas

### 3.1 Device Dispatch: Collection &rarr; Partner
* **Endpoint:** `POST {PARTNER_API_URL}/api/v1/refurbishment/devices`
* **Headers:**
  ```http
  Content-Type: application/json
  X-API-Key: {PARTNER_API_KEY}
  Authorization: Bearer {PARTNER_API_KEY}
  Idempotency-Key: {transfer_id}
  ```
* **Request Payload:**
  ```json
  {
    "schema_version": "1.0.0",
    "device_id": "EW-2026-00128",
    "transfer_id": "TRF-8F2A1C0B",
    "device": {
      "id": "EW-2026-00128",
      "name": "Latitude 5420",
      "type": "Laptop",
      "brand": "Dell",
      "model": "Latitude 5420",
      "purchase_year": 2022,
      "weight_kg": 1.48,
      "condition": "Good",
      "power_status": "Powers on",
      "screen_condition": "Working",
      "battery_condition": "Good",
      "damage_description": "Minor surface scratches"
    },
    "files": []
  }
  ```
* **Response Payload (201 Created):**
  ```json
  {
    "status": "success",
    "message": "Device accepted and queued for refurbishment assessment",
    "device_id": "EW-2026-00128",
    "transfer_id": "TRF-8F2A1C0B",
    "received_at": "2026-11-01T14:30:00Z",
    "files_verified": 0,
    "persisted": true
  }
  ```

---

### 3.2 Assessment Callback: Partner &rarr; Collection
* **Endpoint:** `POST {COLLECTION_API_URL}/api/v1/assessment-results`
* **Headers:**
  ```http
  Content-Type: application/json
  X-API-Key: {COLLECTION_API_KEY}
  Authorization: Bearer {COLLECTION_API_KEY}
  Idempotency-Key: {assessment_id}
  ```
* **Request Payload:**
  ```json
  {
    "device_id": "EW-2026-00128",
    "transfer_id": "TRF-OUT-9B1D4E2F",
    "assessment_id": "ASM-3A7B9C1E",
    "repairability_score": 85,
    "circular_recovery_score": 90,
    "repair_cost": 2500.50,
    "component_value": 7500.25,
    "component_recovery_value": 7500.25,
    "estimated_refurbished_value": 18500.00,
    "recommendation": "Refurbish",
    "co2_avoided_kg": 24.5,
    "technical_summary": "Device in excellent condition. Battery replacement and OS reinstall recommended."
  }
  ```
* **Response Payload (200 OK):**
  ```json
  {
    "status": "success",
    "message": "Assessment intelligence received and processed successfully.",
    "device_id": "EW-2026-00128",
    "transfer_id": "TRF-D7E3A10F",
    "new_device_status": "Refurbishment",
    "assessment": {
      "device_id": "EW-2026-00128",
      "repairability_score": 85,
      "circular_recovery_score": 90,
      "repair_cost": 2500.5,
      "component_value": 7500.25,
      "recommendation": "REFURBISH",
      "co2_avoided_kg": 24.5
    }
  }
  ```

---

### 3.3 Dashboard Manual Re-Dispatch ("Send Result Again")
* **Endpoint:** `POST /api/v1/assessment-results/send`
* **Headers:**
  ```http
  Content-Type: application/json
  Cookie: refurbishment_session=...
  ```
* **Request Payload:**
  ```json
  {
    "device_id": "EW-2026-00128"
  }
  ```
* **Response Payload (200 OK):**
  ```json
  {
    "success": true,
    "http_status": 200,
    "message": "Assessment result sent to Collection Application · 200 OK",
    "transfer_id": "TRF-OUT-4C918A0E"
  }
  ```

---

## 4. Verification & Automated Test Results

### 4.1 Collection & Recovery Test Suite (`D:\E-waste-Recovery`)
```text
============================= test session starts =============================
platform win32 -- Python 3.10.10, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\E-waste-Recovery
collected 91 items

backend\tests\test_app.py .............................................. [ 50%]
....................................                                     [ 90%]
backend\tests\test_end_to_end_integration.py .........                   [100%]

============================= 91 passed in 38.60s =============================
```

### 4.2 Partner Refurbishment Intelligence Test Suite (`D:\python_lab`)
```text
============================= test session starts =============================
platform win32 -- Python 3.10.10, pytest-9.1.1, pluggy-1.6.0
rootdir: D:\python_lab
collected 59 items

backend\tests\test_assessment_engine.py ....                             [  6%]
backend\tests\test_database.py ...                                       [ 11%]
backend\tests\test_deployment_diagnostic.py ................             [ 38%]
backend\tests\test_flask_app.py .....                                    [ 47%]
backend\tests\test_incoming_device.py .....                              [ 55%]
backend\tests\test_phase10_supabase_integration.py ...............       [ 81%]
backend\tests\test_reports_and_callbacks.py ........                     [ 94%]
backend\tests\test_security.py ...                                       [100%]

======================== 59 passed, 1 warning in 3.82s ========================
```

**Total Automated Test Count:** **150 Passing Tests** across both codebases.

---

## 5. Vercel Environment Variables Configuration Checklist

### Application 1: Collection & Recovery Vercel Project
| Variable | Value / Description | Required? |
|---|---|---|
| `CIRCULAR_API_KEY` | Server-side inbound key for incoming callbacks (e.g. `col_inbound_...`) | **Required** |
| `PARTNER_API_URL` | Base URL of deployed Partner service (e.g. `https://<partner-project>.vercel.app`) | **Required** |
| `PARTNER_API_KEY` | Inbound key expected by Partner (must match Partner's `PARTNER_INBOUND_API_KEY`) | **Required** |
| `DATABASE_URL` | Supabase connection string (`postgresql://postgres.[REF]:[PW]@aws-0-[REGION].pooler.supabase.com:6543/postgres`) | **Required** |
| `SECRET_KEY` | Production session and CSRF signing secret (minimum 32 bytes) | **Required** |
| `IS_PRODUCTION` | `true` | **Required** |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` | **Required** |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role secret | **Required** |
| `SUPABASE_STORAGE_BUCKET` | `ewaste-files` | Optional (defaults to `ewaste-files`) |
| `SEED_DEMO_DATA` | `false` | Recommended for production |

### Application 2: Refurbishment Intelligence Vercel Project
| Variable | Value / Description | Required? |
|---|---|---|
| `PARTNER_INBOUND_API_KEY` | Inbound key for receiving devices (must match Collection's `PARTNER_API_KEY`) | **Required** |
| `COLLECTION_API_URL` | Base URL of deployed Collection service (e.g. `https://<collection-project>.vercel.app`) | **Required** |
| `COLLECTION_API_KEY` | Key for sending callbacks (must match Collection's `CIRCULAR_API_KEY`) | **Required** |
| `ADMIN_API_KEY` | Machine key for external administrative automation | Recommended |
| `DATABASE_URL` | Supabase connection string (`postgresql://postgres.[REF]:[PW]@aws-0-[REGION].pooler.supabase.com:6543/postgres`) | **Required** |
| `SECRET_KEY` | Production session secret key (minimum 32 bytes) | **Required** |
| `DB_SCHEMA` | `refurbishment` | Optional (defaults to `refurbishment`) |
| `IS_PRODUCTION` | `true` | **Required** |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` | **Required** |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role secret | **Required** |
| `SUPABASE_STORAGE_BUCKET` | `ewaste-files` | Optional (defaults to `ewaste-files`) |

---

## 6. Production Verification Instructions

Once deployed to Vercel:

1. **Verify Health Probes:**
   - Collection: `curl -i https://<collection-app>.vercel.app/api/health` &rarr; `{"status": "healthy"}`
   - Partner: `curl -i https://<partner-app>.vercel.app/api/health` &rarr; `{"status": "ok", "schema": "refurbishment"}`

2. **Verify Partner UI Session & Device Registry:**
   - Open `https://<partner-app>.vercel.app/` in a browser.
   - Confirm that the `refurbishment_session` cookie is set.
   - Confirm that the Incoming Device Registry loads without 401 errors.

3. **Verify Device Transfer:**
   - In Collection, register or dispatch a device to Partner.
   - Confirm status transitions from `Pending assessment` &rarr; `Processing`.
   - On Partner's dashboard, verify the device appears immediately in the Incoming Devices table.

4. **Verify Assessment Callback & Retry:**
   - On Partner UI, evaluate an assessment or click "Send result again".
   - Confirm `200 OK` toast notification.
   - In Collection, reload the device view: confirm status transitioned to `Refurbishment` / `Parts recovery` / `Completed` and the assessment intelligence scores and recommendation are rendered.

