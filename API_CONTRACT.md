# Circular E-Waste Platform — Inter-Application API Contract

This document provides the authoritative REST API specification governing data exchange between the **Collection & Recovery Application** and the **Refurbishment Intelligence Service Partner Application**.

---

## 1. Authentication & Security Policy

1. **Authentication Schemes**:
   - All inter-application requests must supply a pre-shared API secret key via either:
     - Header: `X-API-Key: <SECRET_KEY>`
     - Header: `Authorization: Bearer <SECRET_KEY>`
2. **Web Administrative Interface**:
   - The web user interface is intended for internal administrative and operational use.
   - CSRF tokens (`csrf`) protect browser session forms and **must never** be accepted or treated as API authentication credentials.
3. **Network Transport**:
   - Production deployments must strictly enforce HTTPS (TLS 1.2+).
4. **Idempotency Guarantees**:
   - State-changing `POST` requests must transmit a unique `Idempotency-Key: <UUID>` header (or payload `idempotency_key` / `event_id`).
   - Duplicate transmissions return the previously persisted result with HTTP 200 without duplicate state modification.

---

## 2. Direction: Collection → Refurbishment

### Endpoint
`POST /api/v1/refurbishment/devices`

Dispatches registered e-waste device metadata and associated file reference keys from Collection to Refurbishment for technical evaluation and circular triage.

#### Request Headers
| Header | Type | Required | Description |
|---|---|---|---|
| `Content-Type` | String | Yes | Must be `application/json` |
| `X-API-Key` | String | Conditional | API key configured for outbound partner calls |
| `Authorization` | String | Conditional | `Bearer <PARTNER_API_KEY>` (alternative to `X-API-Key`) |
| `Idempotency-Key` | String | Yes | Transfer transaction UUID (e.g. `TRF-8F12A09B`) |

#### Request Payload Schema
```json
{
  "device": {
    "id": "EW-2026-9B41D82A",
    "name": "ThinkPad T480s",
    "type": "Laptop",
    "brand": "Lenovo",
    "model": "T480s",
    "year": "2021",
    "weight": "1.35",
    "condition": "Good",
    "damage": "Minor scuff marks",
    "power": "Powers on",
    "screen": "Working",
    "battery": "Good",
    "registered_date": "Oct 14, 2026"
  },
  "files": [
    {
      "file_type": "image",
      "original_filename": "laptop_front.jpg",
      "storage_key": "4c9e88bf7d84a0d92372cbe9b7.jpg",
      "content_type": "image/jpeg",
      "file_size": 2451000,
      "url": "/uploads/4c9e88bf7d84a0d92372cbe9b7.jpg"
    },
    {
      "file_type": "document",
      "original_filename": "invoice.pdf",
      "storage_key": "9a38f71120eb81cc3f90.pdf",
      "content_type": "application/pdf",
      "file_size": 1824000,
      "url": "/api/v1/files/9a38f71120eb81cc3f90.pdf"
    }
  ],
  "dispatched_at": "2026-10-14T16:45:00.000000+00:00"
}
```

*Note: Filesystem paths (`file_path`, drive roots, or local directories) are never exposed.*

#### Expected Partner Response Statuses
| Status Code | Reason | Meaning / Collection Behavior |
|---|---|---|
| **200 OK** | Synchronous Assessment Complete | Transfer marked `Completed`. Device status transitions to `Processing`. |
| **201 Created** | Device Created / Enqueued | Transfer marked `Completed`. Device status transitions to `Processing`. |
| **202 Accepted** | Asynchronous Processing Started | Transfer marked `Processing`. Device status transitions to `Processing`. Assessment awaited via webhook. |
| **400 Bad Request** | Validation Failure | Transfer marked `Failed`. Device remains in `Pending assessment`. |
| **401 Unauthorized** | Invalid Partner Key | Transfer marked `Failed`. Device remains in `Pending assessment`. |
| **404 Not Found** | Partner Route Missing | Transfer marked `Failed`. Device remains in `Pending assessment`. |
| **409 Conflict** | Duplicate Transfer Key | Handled per partner idempotency rules. |
| **422 Unprocessable** | Malformed Entity | Transfer marked `Failed`. Device remains in `Pending assessment`. |
| **500 Internal Error** | Partner System Crash | Transfer marked `Failed`. Device remains in `Pending assessment`. Retryable up to 5 attempts. |
| **503 Service Unavailable** | Partner Maintenance / Down | Transfer marked `Failed`. Device remains in `Pending assessment`. Retryable up to 5 attempts. |

---

## 3. Direction: Refurbishment → Collection

### Primary Endpoint
`POST /api/v1/assessment-results`

### Compatibility Endpoint
`POST /api/v1/collection/assessments`

Transmits completed assessment metrics, repairability grading, financial yield valuations, carbon avoidance calculations, and triage recommendation back to Collection.

#### Request Headers
| Header | Type | Required | Description |
|---|---|---|---|
| `Content-Type` | String | Yes | `application/json` |
| `X-API-Key` | String | Yes | `CIRCULAR_API_KEY` configured in Collection application |
| `Authorization` | String | Yes | Alternative: `Bearer <CIRCULAR_API_KEY>` |
| `Idempotency-Key` | String | Recommended | Inbound transaction UUID |

#### Request Payload Schema
```json
{
  "device_id": "EW-2026-9B41D82A",
  "repairability_score": 88,
  "circular_recovery_score": 85,
  "repair_cost": 2500.00,
  "component_value": 11000.00,
  "recommendation": "REFURBISH",
  "co2_avoided_kg": 19.5,
  "assessed_by": "GreenTech Refurbishment Partner",
  "notes": "Logic board and battery pass health checks. Display assembly operational.",
  "factors": [
    "Battery health exceeds 85% original capacity",
    "Modular keyboard replacement viable",
    "Secondary refurbished market demand high"
  ],
  "idempotency_key": "EVT-9924-A01"
}
```

#### Field Specifications & Canonical Enums
* `device_id` (String, required): Existing device identifier in Collection database.
* `repairability_score` (Integer, required): Range `0` to `100`. Must be finite integer.
* `circular_recovery_score` (Integer, required): Range `0` to `100`. Must be finite integer.
* `repair_cost` (Float, required): Finite number `>= 0.0`. Currency in INR (₹).
* `component_value` (Float, required): Finite number `>= 0.0`. Alias: `component_recovery_value`.
* `co2_avoided_kg` (Float, required): Finite number `>= 0.0`. Estimated GHG avoidance in kg.
* `recommendation` (String, required): Canonical values:
  - `REFURBISH` → Transitions device to `Refurbishment`.
  - `PARTS_RECOVERY` → Transitions device to `Parts recovery`. (Aliases accepted: `PARTS RECOVERY`, `PARTS`).
  - `RECYCLE` → Transitions device to `Completed`.
  - `SAFE_DISPOSAL` → Transitions device to `Completed`. (Aliases accepted: `SAFE DISPOSAL`, `DISPOSAL`).

#### State Machine Prerequisites
* The device **must be in status `Processing`** when the assessment arrives.
* If the device is in `Pending assessment`, direct transition to `Refurbishment`, `Parts recovery`, or `Completed` is **prohibited** (returns HTTP 409).
* If the device is already in `Completed`, further transitions are **prohibited** (returns HTTP 409).

#### Response Schemas

##### Success (200 OK)
```json
{
  "status": "success",
  "transfer_id": "TRF-9D4A1B2C",
  "device_id": "EW-2026-9B41D82A",
  "new_device_status": "Refurbishment",
  "assessment": {
    "repairability_score": 88,
    "circular_recovery_score": 85,
    "repair_cost": 2500.0,
    "component_value": 11000.0,
    "recommendation": "REFURBISH",
    "co2_avoided_kg": 19.5,
    "assessed_by": "GreenTech Refurbishment Partner",
    "assessed_at": "2026-10-14T16:50:00.000000+00:00"
  }
}
```

##### Idempotent Duplicate (200 OK)
```json
{
  "status": "success",
  "idempotent": true,
  "message": "Assessment result already processed (idempotent response)",
  "transfer_id": "TRF-9D4A1B2C",
  "device_id": "EW-2026-9B41D82A",
  "new_device_status": "Refurbishment",
  "assessment": { ... }
}
```

##### Validation Error (400 Bad Request)
```json
{
  "error": "Validation Error",
  "message": "repairability_score must be an integer between 0 and 100"
}
```

##### Authentication Error (401 Unauthorized)
```json
{
  "error": "Unauthorized",
  "message": "Invalid or missing API key. Provide via 'X-API-Key' or 'Authorization: Bearer <key>'."
}
```

##### Not Found (404 Not Found)
```json
{
  "error": "Not Found",
  "message": "Device EW-2026-99999999 not found"
}
```

##### Lifecycle Conflict (409 Conflict)
```json
{
  "error": "Conflict",
  "message": "Device EW-2026-9B41D82A is in status 'Pending assessment' and cannot transition to 'Refurbishment'. Device must be in 'Processing' before assessment can be applied."
}
```

##### Server Degradation / Error (500 Internal Server Error)
```json
{
  "error": "Internal Error",
  "message": "Failed to process assessment result."
}
```

---

## 4. Health Probe Specification

### Endpoint
`GET /api/health`

Public endpoint utilized by deployment orchestrators (Render, Kubernetes, load balancers) to monitor application and database liveness.

#### Response: Healthy (200 OK)
```json
{
  "status": "healthy",
  "database": "connected",
  "service": "Circular E-Waste Recovery Platform API",
  "timestamp": "2026-10-14T16:55:00.000000+00:00",
  "version": "1.0.0"
}
```

#### Response: Degraded (503 Service Unavailable)
*Internal database errors are logged server-side and never leaked in client response.*
```json
{
  "status": "degraded",
  "database": "unavailable",
  "service": "Circular E-Waste Recovery Platform API",
  "timestamp": "2026-10-14T16:55:00.000000+00:00",
  "version": "1.0.0"
}
```
