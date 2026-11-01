# Multi-Application API Contract Documentation

**Platform:** Circular E-Waste Recovery & Refurbishment Intelligence  
**Version:** 1.0.0  
**Target Architecture:** Multi-service serverless on Vercel with shared Supabase PostgreSQL and private Object Storage (`ewaste-files`).

---

## 1. Architecture & Communication Overview

```
┌──────────────────────────────────┐                 ┌──────────────────────────────────────┐
│ APPLICATION 1                    │                 │ APPLICATION 2                        │
│ E-Waste Collection & Recovery    │                 │ Refurbishment Intelligence           │
│                                  │                 │                                      │
│ Schema: `collection`             │                 │ Schema: `refurbishment`              │
└───────────────┬──────────────────┘                 └──────────────────┬───────────────────┘
                │                                                       │
                │  1. POST /api/v1/refurbishment/devices                │
                │     Headers: X-API-Key: PARTNER_API_KEY               │
                │              Idempotency-Key: TRF-XXXXXXXX            │
                ├──────────────────────────────────────────────────────►│
                │                                                       │
                │  2. HTTP 201 Created                                  │
                │◄──────────────────────────────────────────────────────┤
                │                                                       │
                │  3. [Partner processes assessment]                   │
                │                                                       │
                │  4. POST /api/v1/assessment-results                   │
                │     Headers: X-API-Key: COLLECTION_API_KEY            │
                │              Idempotency-Key: ASM-XXXXXXXX            │
                │◄──────────────────────────────────────────────────────┤
                │                                                       │
                │  5. HTTP 200 OK                                       │
                ├──────────────────────────────────────────────────────►│
```

Both applications communicate over HTTPS REST APIs using strict credential separation, request-level idempotency headers, schema-isolated database transactions, and deterministic lifecycle transitions.

---

## 2. Authentication & Credential Separation Contract

| Interaction Direction | Calling Service | Target Service | Header Format | Variable Name in Caller | Variable Name in Receiver |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Outbound Transfer** | Collection App | Refurbishment App | `X-API-Key: <key>`<br>`Authorization: Bearer <key>` | `PARTNER_API_KEY` | `PARTNER_INBOUND_API_KEY` *(or `PARTNER_API_KEY`)* |
| **Reverse Callback** | Refurbishment App | Collection App | `X-API-Key: <key>`<br>`Authorization: Bearer <key>` | `COLLECTION_API_KEY` | `CIRCULAR_API_KEY` *(or `COLLECTION_API_KEY`)* |

### Security Rules
1. **Header-Only Authentication:** All credentials must be transmitted via HTTP headers (`X-API-Key` or `Authorization: Bearer <token>`). Passing credentials via query parameters is strictly forbidden and rejected with HTTP 401.
2. **Timing-Safe Comparison:** Both services use `secrets.compare_digest()` to prevent timing attacks.
3. **No Credential Echo:** Neither service echoes back provided or expected keys in responses or logs.

---

## 3. Endpoint 1: Outgoing Device Transfer

- **HTTP Method:** `POST`
- **Destination Path:** `/api/v1/refurbishment/devices` *(or `/api/v1/devices/receive`)*
- **Initiated By:** Collection Application (`dispatch_device_to_partner()`)
- **Received By:** Refurbishment Application (`receive_incoming_device()`)

### Request Headers
| Header Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `Content-Type` | String | Yes | Must be `application/json` |
| `X-API-Key` | String | Yes | Partner inbound API key |
| `Authorization` | String | Optional | `Bearer <PARTNER_API_KEY>` |
| `Idempotency-Key` | String | Yes | Unique transfer transaction ID (e.g. `TRF-1E6F900D`) |

### Request JSON Payload Schema
```json
{
  "schema_version": "1.0.0",
  "device_id": "EW-2026-CE1ED6FF",
  "transfer_id": "TRF-1E6F900D",
  "device": {
    "id": "EW-2026-CE1ED6FF",
    "name": "Apple MacBook Pro M2",
    "type": "Laptop",
    "brand": "Apple",
    "model": "MacBook Pro M2",
    "purchase_year": 2023,
    "weight_kg": 1.6,
    "condition": "Good",
    "power_status": "Powers on",
    "screen_condition": "Working",
    "battery_condition": "Good",
    "damage_description": "",
    "year": "2023",
    "weight": "1.6",
    "damage": "",
    "power": "Powers on",
    "screen": "Working",
    "battery": "Good",
    "registered_date": "Nov 01, 2026"
  },
  "files": [
    {
      "name": "diagnostic_report.pdf",
      "type": "document",
      "storage_key": "EW-2026-CE1ED6FF/diagnostic_report.pdf",
      "content_type": "application/pdf",
      "file_size": 245000,
      "url": "https://<supabase-url>/storage/v1/object/authenticated/ewaste-files/EW-2026-CE1ED6FF/diagnostic_report.pdf"
    }
  ],
  "dispatched_at": "2026-11-01T06:00:30.311357+00:00"
}
```

### Field Definitions & Types

| Field Path | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `schema_version` | String | Optional | Payload contract version (defaults to `1.0.0`) |
| `device_id` | String | Yes | Unique device identifier (`EW-YYYY-XXXX` or `EW-YYYY-XXXXXXXX`) |
| `transfer_id` | String | Yes | Unique transfer audit identifier (`TRF-XXXX` or `TRF-XXXXXXXX`) |
| `device` | Object | Yes | Structured device specifications object |
| `device.type` | String | Yes | Device category: `Laptop`, `Smartphone`, `Tablet`, `Monitor`, `Desktop`, `Other` |
| `device.brand` | String | Yes | Manufacturer name |
| `device.model` | String | Yes | Model designation |
| `device.condition` | String | Yes | Physical condition: `Good`, `Fair`, `Poor`, `Broken` |
| `device.purchase_year` | Integer/String | Optional | Year of purchase (e.g. `2023`) |
| `device.weight_kg` | Float/String | Optional | Weight in kilograms (e.g. `1.6`) |
| `device.power_status` | String | Optional | Power state (`Powers on`, `Does not power on`) |
| `device.screen_condition` | String | Optional | Display status (`Working`, `Cracked`, `No display`) |
| `device.battery_condition`| String | Optional | Battery health (`Good`, `Degraded`, `Dead`) |
| `device.damage_description`| String | Optional | Technical damage notes |
| `files` | Array | Optional | Remote storage object references (empty array if no files uploaded) |
| `files[].name` | String | Yes (if file) | Original file name |
| `files[].storage_key` | String | Yes (if file) | Storage path in Supabase bucket `ewaste-files` |
| `files[].content_type`| String | Optional | MIME type |
| `files[].file_size` | Integer | Optional | Size in bytes |
| `dispatched_at` | String (ISO 8601) | Optional | Dispatch timestamp |

### Responses

#### 201 Created (Success)
Returned when device is received and persisted in `refurbishment` schema:
```json
{
  "status": "success",
  "message": "Device accepted and queued for refurbishment assessment",
  "device_id": "EW-2026-CE1ED6FF",
  "transfer_id": "TRF-1E6F900D",
  "received_at": "2026-11-01T06:00:30.486710"
}
```

#### 200 OK (Idempotent Replay)
Returned when a transfer with the same idempotency key or device and transfer ID is received again:
```json
{
  "status": "success",
  "idempotent": true,
  "message": "Device accepted and queued for refurbishment assessment",
  "device_id": "EW-2026-CE1ED6FF",
  "transfer_id": "TRF-1E6F900D",
  "received_at": "2026-11-01T06:00:30.486710"
}
```

#### 400 Bad Request
Returned when JSON payload is missing or malformed:
```json
{
  "error": "Validation Error",
  "message": "Missing required device metadata: 'brand' and 'model' are required."
}
```

#### 401 Unauthorized / 403 Forbidden
Returned when credentials are missing or invalid:
```json
{
  "error": "Forbidden",
  "message": "Invalid API key provided"
}
```

#### 409 Conflict
Returned when attempting to transfer a device that already exists under a different transfer ID:
```json
{
  "error": "Conflict",
  "message": "Device EW-2026-CE1ED6FF already exists in the system with source transfer TRF-PREV-01"
}
```

#### 500 Internal Server Error
Returned if internal database insertion fails:
```json
{
  "error": "Internal Server Error",
  "message": "Failed to store incoming device record: DatabaseError",
  "transfer_id": "TRF-1E6F900D",
  "device_id": "EW-2026-CE1ED6FF",
  "stage": "device_persistence"
}
```

---

## 4. Endpoint 2: Reverse Assessment Callback

- **HTTP Method:** `POST`
- **Destination Path:** `/api/v1/assessment-results` *(or `/api/v1/collection/assessments`)*
- **Initiated By:** Refurbishment Application (`send_assessment_result_to_collection()`)
- **Received By:** Collection Application (`receive_assessment()`)

### Request Headers
| Header Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `Content-Type` | String | Yes | Must be `application/json` |
| `X-API-Key` | String | Yes | Collection inbound API key (`COLLECTION_API_KEY`) |
| `Authorization` | String | Optional | `Bearer <COLLECTION_API_KEY>` |
| `Idempotency-Key` | String | Yes | Assessment UUID (e.g. `ASM-9A8B7C6D`) |

### Request JSON Payload Schema
```json
{
  "device_id": "EW-2026-CE1ED6FF",
  "repairability_score": 78,
  "circular_recovery_score": 90,
  "repair_cost": 4200.00,
  "component_value": 14500.00,
  "recommendation": "REFURBISH",
  "co2_avoided_kg": 24.5,
  "assessed_by": "Refurbishment Intelligence System",
  "notes": "Premium laptop suitable for fast-track refurbishment.",
  "factors": [
    "Motherboard: Excellent (Recoverable, ₹8,500)",
    "Display: Good (Recoverable, ₹4,200)",
    "Battery: Fair (Replace, ₹1,800)"
  ],
  "idempotency_key": "ASM-9A8B7C6D"
}
```

### Field Definitions & Type Constraints

| Field Name | Type | Required | Range / Permitted Values | Contract Notes |
| :--- | :--- | :--- | :--- | :--- |
| `device_id` | String | Yes | Matches existing device in Collection | Must exist in `collection.devices` |
| `repairability_score` | Integer | Yes | `0` to `100` (inclusive) | Must be an integer or rounded float |
| `circular_recovery_score` | Integer | Yes | `0` to `100` (inclusive) | Must be an integer or rounded float |
| `repair_cost` | Float | Yes | `>= 0.00` | Non-negative finite currency value |
| `component_value` | Float | Yes | `>= 0.00` | Non-negative finite currency value *(aliased from `component_recovery_value`)* |
| `recommendation` | String | Yes | Canonical: `REFURBISH`, `PARTS_RECOVERY`, `RECYCLE`, `SAFE_DISPOSAL`, `REJECT` | Normalized to uppercase with underscores |
| `co2_avoided_kg` | Float | Yes | `>= 0.0` | Carbon offset in kg |
| `assessed_by` | String | Optional | Max 128 characters | Assessor identity |
| `notes` | String | Optional | Max 2000 characters | Summary findings |
| `factors` | Array/String | Optional | List of evaluated component descriptions | JSON-serialized or string list |
| `idempotency_key` | String | Yes | Assessment ID | Prevents duplicate callback processing |

### Canonical Recommendation Mapping

| Refurbishment Outcome | Canonical API Recommendation | New Collection Device Status |
| :--- | :--- | :--- |
| Refurbish / Refurbishment | `REFURBISH` | `Refurbishment` |
| Parts Recovery / Harvesting | `PARTS_RECOVERY` | `Parts recovery` |
| Recycling | `RECYCLE` | `Completed` |
| Safe Disposal / E-Waste | `SAFE_DISPOSAL` | `Completed` |
| Reject / Hazardous Material | `REJECT` *(or `SAFE_DISPOSAL`)* | `Completed` |

### Responses

#### 200 OK (Success)
```json
{
  "status": "success",
  "message": "Assessment result stored successfully",
  "transfer_id": "TRF-E2795C4A",
  "device_id": "EW-2026-CE1ED6FF",
  "new_device_status": "Refurbishment",
  "assessment": {
    "repairability_score": 78,
    "circular_recovery_score": 90,
    "repair_cost": 4200.0,
    "component_value": 14500.0,
    "recommendation": "REFURBISH",
    "co2_avoided_kg": 24.5,
    "assessed_by": "Refurbishment Intelligence System",
    "notes": "Premium laptop suitable for fast-track refurbishment."
  }
}
```

#### 200 OK (Idempotent Replay)
```json
{
  "status": "success",
  "idempotent": true,
  "message": "Assessment result already processed (idempotent response)",
  "transfer_id": "TRF-E2795C4A",
  "device_id": "EW-2026-CE1ED6FF",
  "new_device_status": "Refurbishment"
}
```

#### 400 Bad Request (Validation Error)
```json
{
  "error": "Validation Error",
  "message": "Missing required assessment fields: repairability_score, circular_recovery_score, repair_cost, component_value, recommendation, co2_avoided_kg"
}
```

#### 401 Unauthorized
```json
{
  "error": "Unauthorized",
  "message": "Invalid or missing API key. Provide via 'X-API-Key' or 'Authorization: Bearer <key>'."
}
```

#### 404 Not Found
```json
{
  "error": "Not Found",
  "message": "Device EW-DOES-NOT-EXIST not found"
}
```

#### 409 Conflict
```json
{
  "error": "Conflict",
  "message": "Device EW-2026-CE1ED6FF is in status 'Completed' and cannot transition to 'Refurbishment'. Device must be in 'Processing' before assessment can be applied."
}
```

---

## 5. Device Lifecycle State Machine

```
Collection:
[ Pending assessment ] ──(Initiate Dispatch)──► [ Processing ]
                                                       │
                           ┌───────────────────────────┴───────────────────────────┐
                           ▼                                                       ▼
     (Callback: REFURBISH) │                                 (Callback: PARTS_RECOVERY)   │
             [ Refurbishment ]                                     [ Parts recovery ]     │
                           │                                                       │      │
                           └───────────────────────────┬───────────────────────────┘      │
                                                       │                                  │
                                             (Complete Refurb/Harvest)                    │
                                                       ▼                                  │
                                                 [ Completed ] ◄──────────────────────────┘
                                                               (Callback: RECYCLE /
                                                                SAFE_DISPOSAL / REJECT)
```

- When registered: Status is **`Pending assessment`**.
- When dispatched: Status transitions to **`Processing`**.
- Only devices in **`Processing`** status can receive and apply an assessment callback.
- Attempting to send a device already in `Processing`, `Refurbishment`, `Parts recovery`, or `Completed` returns **`409 Conflict`**.

