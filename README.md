# Circular E-Waste Recovery & Refurbishment Intelligence Platform

A production-ready circular economy platform for electronic waste collection, recovery tracking, technical assessment intelligence, and partner refurbishment dispatch.

The platform pairs a 100% preserved frontend design system with a robust Python Flask backend, dual-database architecture (SQLite for local development/testing and PostgreSQL for production on Render), Alembic database migrations, local/persistent disk file storage abstraction (without any AWS S3 or Supabase dependencies), authenticated REST APIs, and automated reporting.

---

## 1. Architecture & Technology Stack

### Technology Stack
- **Backend**: Python 3.10+, Flask, Flask-SQLAlchemy, SQLAlchemy ORM, Flask-Migrate (Alembic), Gunicorn, requests.
- **Database**:
  - **Local Development & Testing**: SQLite (`instance/circular.sqlite3` or in-memory).
  - **Production on Render**: PostgreSQL (`circular-postgres`), connected via Render's managed connection string and `psycopg[binary]`.
- **File Storage**: Local and persistent disk storage abstraction (`LocalStorage`). In production, files are written to a Render persistent disk mounted at `/var/data/uploads`. (Zero AWS S3, zero Supabase).
- **Frontend**: Approved Flask Jinja2 templates, vanilla CSS, vanilla JavaScript, SVG icon catalog. Visual design, theme, colors, cards, and layouts are 100% preserved.
- **Reporting**: ReportLab for verified technical assessment PDFs and CSV summaries.
- **Deployment**: Render Web Service + Render Managed PostgreSQL Database configured via root `render.yaml`.

### System Architecture Diagram
```
                     +------------------------------------------------+
                     | Approved Web Frontend (Jinja2 / CSS / JS)       |
                     +------------------------------------------------+
                                              |
                                      HTTP / Form POST
                                              v
+-----------------------------------------------------------------------------+
|                           Flask Application Engine                          |
|  +--------------------+   +-----------------------+   +-------------------+ |
|  | Device Registration|   | Real Metrics Service  |   | File Security Mgr | |
|  +--------------------+   +-----------------------+   +-------------------+ |
|  +--------------------+   +-----------------------+   +-------------------+ |
|  | Outbound Dispatch  |   | Inbound Webhook API   |   | PDF/CSV Reporting | |
|  +--------------------+   +-----------------------+   +-------------------+ |
+-----------------------------------------------------------------------------+
         |                                |                        |
         v                                v                        v
+------------------+             +------------------+    +--------------------+
|  SQLAlchemy ORM  |             |  Storage Engine  |    | External Partner   |
|  (Flask-Migrate) |             |  (LocalStorage)  |    | Refurbishment API  |
+------------------+             +------------------+    +--------------------+
         |                                |                        |
  +------+-------+                        v                        v
  |              |               Local: instance/uploads   POST /devices
  v              v               Prod:  /var/data/uploads  (Idempotency-Key)
SQLite       PostgreSQL
(Local Dev)  (Render Prod)
```

---

## 2. Repository & Folder Structure

```text
E-waste-Recovery/
├── render.yaml             # Render deployment blueprint (Web Service + PostgreSQL + Disk)
├── Procfile                # Production process command with Gunicorn
├── .env.example            # Environment variables template (No S3 / Supabase)
├── .gitignore              # Git exclusions (caches, sqlite3, instance/, venv, .env)
├── README.md               # Complete platform documentation
│
├── backend/                # Collection & Recovery backend application
│   ├── app.py              # Application factory, route controllers & template wiring
│   ├── config.py           # Configuration, PostgreSQL URL normalizer & secret validator
│   ├── models.py           # SQLAlchemy models (Device, Transfer, AssessmentResult, DeviceFile, Metadata)
│   ├── metrics.py          # Central database-backed portfolio metrics service with date filters
│   ├── storage.py          # LocalStorage abstraction with magic-byte signature validation
│   ├── api.py              # Authenticated REST API blueprint, inbound webhook & notifications
│   ├── reports.py          # ReportLab PDF report generation & CSV export builders
│   ├── wsgi.py             # Production WSGI entrypoint with sys.path resolution
│   ├── requirements.txt    # Production dependencies (Flask, psycopg, SQLAlchemy, Migrate, etc.)
│   ├── .env.example        # Backend local environment template
│   ├── demo.json           # Seed demonstration dataset for local exploration
│   ├── migrations/         # Alembic database migrations repository
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       └── 41179279b201_initial_database_schema.py
│   └── tests/
│       └── test_app.py     # Comprehensive automated test suite (29 tests, 18 subtests)
│
└── frontend/               # Approved UI assets (100% PRESERVED & UNCHANGED)
    ├── templates/          # Approved Jinja2/HTML templates
    ├── static/             # Approved CSS, SVG icon catalog, interactions.js
    ├── demo.json           # Template demo dataset
    └── README.md           # Approved frontend asset notes
```

---

## 3. Database Architecture & Dual-Database Support

The platform supports both SQLite and PostgreSQL without any application code branching:
- **Local Development**: Automatically falls back to SQLite (`sqlite:///instance/circular.sqlite3`) if `DATABASE_URL` is omitted.
- **Production on Render**: Uses Render Managed PostgreSQL via `DATABASE_URL`.

### PostgreSQL URL Normalization
Render provides PostgreSQL connection strings with schemes such as `postgres://` or `postgresql://`. The `Config.normalize_database_url()` method automatically converts `postgres://` to `postgresql://` so that SQLAlchemy loads the appropriate PostgreSQL driver (`psycopg[binary]`).

### Schema & Index Specifications
- `Device`: Primary key `id` (e.g. `EW-2026-XXXX`), indexed on `status` and `created_at`.
- `Transfer`: Primary key `id` (`TRF-XXXX`), foreign key `device_id -> devices.id`, unique index on `idempotency_key`, indexed on `status` and `timestamp`.
- `AssessmentResult`: Primary key `id`, unique foreign key `device_id -> devices.id`, indexed on `assessed_at`.
- `DeviceFile`: Primary key `id`, foreign key `device_id -> devices.id`, unique index on `storage_key`, `is_private` boolean flag.
- `Metadata`: Primary key `key` (tracks idempotency and migration/seed state).

---

## 4. Database Migrations (Flask-Migrate / Alembic)

Schema management is strictly managed via Flask-Migrate (Alembic). `db.create_all()` is never used in production.

### Migration Commands
```powershell
# Set FLASK_APP and run migrations
$env:FLASK_APP = "backend/app.py"
flask db upgrade --directory backend/migrations

# Create a new migration after editing models.py
flask db migrate -m "Add index or new field" --directory backend/migrations
flask db upgrade --directory backend/migrations
```

In production, Render executes `flask db upgrade` automatically before Gunicorn launches:
```bash
cd backend && flask db upgrade && gunicorn --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT wsgi:app
```

---

## 5. File Storage & Security

### Pure Local & Persistent Disk Storage
All file storage uses the unified `LocalStorage` engine in `backend/storage.py`:
- **Local Development**: Saved to `backend/instance/uploads/`.
- **Render Production**: Configured via `UPLOAD_STORAGE_PATH=/var/data/uploads` to persist files on a Render persistent disk mounted at `/var/data`.
- **Durability Notice**: If a persistent disk is not attached to the Render web service, uploaded files reside on Render's ephemeral filesystem and will reset on redeployment. Zero AWS S3 and zero Supabase configurations are used.

### File Validation & Security Guards
- **File Size Limit**: Maximum 5 MB per file; enforced client-side and server-side.
- **Magic-Byte Signature Verification**: Files are validated against magic bytes (PNG, JPEG, GIF, WebP, PDF) to prevent disguised executables.
- **Public vs. Private File Access**:
  - Public device images (`is_private=False`) are served via `GET /uploads/<name>`.
  - Private invoices and receipts (`is_private=True`) are blocked on `/uploads/<name>` with `403 Forbidden`.
  - Private files require authentication at `GET /api/v1/files/<storage_key>`.
- **Path Traversal Protection**: Any path containing `..`, `/`, or `\\` is immediately rejected. Absolute filesystem paths are never exposed.

---

## 6. Real Dashboard Metrics & Date Filtering

The dashboard contains zero hardcoded numbers. All metrics are aggregated from real database records in `backend/metrics.py`.

### E-Waste Diverted Definition
Devices in `Pending assessment` are **never** counted as diverted e-waste. Only devices that have entered the active recovery pipeline contribute to diverted e-waste weight (`total_weight_kg`) and circular recovery rates:
- `Processing`
- `Refurbishment`
- `Parts recovery`
- `Completed`

### Date Filtering
The dashboard date range filter supports:
- **All time** (full database history)
- **Last 7 days** (`created_at >= now - 7d`)
- **Last 30 days** (`created_at >= now - 30d`)

Queries for devices (`created_at`), transfers (`timestamp`), and assessment results (`assessed_at`) are dynamically filtered by the selected period.

---

## 7. Outbound Partner API & Idempotency

When a device is dispatched to the Refurbishment Intelligence service:
1. **Validation**: Confirms device exists and is in `Pending assessment`.
2. **Transfer Entity**: Creates a `Transfer` record with a unique `transfer_id`.
3. **Idempotency**: Sends header `Idempotency-Key: <transfer_id>` and stores it in `Transfer.idempotency_key`.
4. **Partner Configuration Guard**:
   - If `PARTNER_API_URL` is set but `PARTNER_API_KEY` is missing: Transfer is aborted and marked `Failed` (401), and the device remains in `Pending assessment` (does not transition to `Processing`).
   - If partner is unconfigured: Dispatches as a queued demonstration transfer (`status="Pending"`).
   - If partner responds `200` or `201`: Transfer is `Completed` and device transitions to `Processing`.
   - If partner responds `202`: Transfer is `Processing` and device transitions to `Processing`.
   - On network timeout or HTTP error: Transfer is marked `Failed` and device remains in its previous state.
5. **Retry Logic**: Retrying reuses the existing transfer record and idempotency key, increments `retry_count`, and updates `timestamp`.

---

## 8. Inbound Assessment API Contract

### Authentication
Inbound partner API requests require:
- `X-API-Key: <CIRCULAR_API_KEY>` or
- `Authorization: Bearer <CIRCULAR_API_KEY>`

The health probe `GET /api/health` and notification list `GET /api/v1/notifications` are publicly accessible.

### Endpoints

| Method | Endpoint | Auth Required | Description |
|---|---|---|---|
| `GET` | `/api/health` | No | Health check and database connectivity probe |
| `GET` | `/api/v1/devices` | Yes | List devices (supports `?status=` and `?q=`) |
| `GET` | `/api/v1/devices/<id>` | Yes | Retrieve device details and assessment result |
| `GET` | `/api/v1/transfers` | Yes | List transfer logs |
| `GET` | `/api/v1/transfers/<id>` | Yes | Retrieve individual transfer log |
| `GET` | `/api/v1/notifications` | No | List recent database activities (notifications) |
| `GET` | `/api/v1/files/<key>` | Yes / Session | Authenticated private file download |
| `POST` | `/api/v1/assessment-results` | Yes | Inbound partner assessment webhook with idempotency |
| `POST` | `/api/v1/collection/assessments` | Yes | Backward-compatible alias for assessment results |

### Assessment Webhook Payload
```json
{
  "device_id": "EW-2024-00128",
  "repairability_score": 88,
  "circular_recovery_score": 85,
  "repair_cost": 2500.0,
  "component_value": 9000.0,
  "recommendation": "REFURBISH",
  "co2_avoided_kg": 18.0,
  "assessed_by": "GreenTech Refurbishment Partner",
  "notes": "Verified operational motherboard and screen.",
  "factors": ["Screen intact", "Battery health 88%"]
}
```

*Strict Validation Rules:*
- `repairability_score` & `circular_recovery_score`: Integers between 0 and 100.
- `repair_cost`, `component_value` (or `component_recovery_value`), and `co2_avoided_kg`: Finite numbers >= 0.0 evaluated via `math.isfinite()`. Values such as `NaN`, `Infinity`, or negative numbers are rejected with `400 Bad Request`.
- `Idempotency-Key`: Supported via header or payload. Duplicate deliveries return the existing record with `"idempotent": true` without inserting duplicate transfer entries.

---

## 9. Environment Variables Specification

| Variable | Required in Prod | Local Default | Description |
|---|---|---|---|
| `IS_PRODUCTION` | Yes | `false` | Enables production strict mode (enforces keys & configs) |
| `SECRET_KEY` | Yes | *Random dev key* | Primary Flask session and CSRF signature key |
| `CIRCULAR_SECRET_KEY` | Yes | *Random dev key* | Secondary alias for session secret key |
| `CIRCULAR_API_KEY` | Yes | `""` | Secret token required to authenticate partner API requests |
| `DATABASE_URL` | Yes (Render) | `sqlite:///instance/circular.sqlite3` | Database connection string (PostgreSQL in production) |
| `UPLOAD_STORAGE_PATH` | No | `instance/uploads` | Path for uploaded files (`/var/data/uploads` on Render disk) |
| `PARTNER_API_URL` | No | `""` | Base URL of external refurbishment intelligence service |
| `PARTNER_API_KEY` | No | `""` | Secret token sent to partner service during dispatch |
| `SEED_DEMO_DATA` | No | `true` (dev) / `false` (prod) | Whether to seed demo devices on fresh database setup |
| `FLASK_PORT` | No | `5000` | Local development port |

---

## 10. Local Development Setup

### 1. Prerequisites
- Python 3.10 or higher
- PowerShell (Windows) or Bash (macOS/Linux)

### 2. Environment Setup
```powershell
# Navigate to repository root
cd d:\E-waste-Recovery

# Create and activate virtual environment
python -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r backend/requirements.txt
```

### 3. Database Migrations & Initial Setup
```powershell
$env:FLASK_APP = "backend/app.py"
flask db upgrade --directory backend/migrations
```

### 4. Running the Development Server
```powershell
python backend/wsgi.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your web browser.

---

## 11. Automated Testing

Run the automated test suite using `pytest`:

```powershell
pytest backend/tests/test_app.py -v
```

### Test Coverage Summary
- **Total Test Cases**: 29 test methods, 18 subtests (47 validation scenarios).
- **Pass Rate**: 100% passing.
- **Covered Scenarios**:
  - Flask application factory & WSGI entrypoint.
  - SQLite local fallback & PostgreSQL dialect query compilation.
  - Flask-Migrate database upgrade execution from scratch.
  - Server-side device registration validation, safe IDs (`EW-YYYY-XXXX`), and file binding.
  - Dashboard metrics calculation, date filters (7d, 30d, all time), and diverted e-waste exclusion.
  - Assessment score bounds, NaN, Infinity, -Infinity, and negative number rejection.
  - Public image download vs. private document access restriction (`403` / `401`).
  - Path traversal attack immunity (`400` / `404`).
  - Outbound partner dispatch with idempotency, timeout, and missing key guards.
  - Inbound webhook processing, atomic database transactions, and deduplication.
  - PDF assessment generation and CSV summaries with real database rows.
  - Public health check probe (`/api/health`) and notifications API (`/api/v1/notifications`).

---

## 12. Render Production Deployment

### 1. Blueprint Deployment (`render.yaml`)
The repository contains a production-ready `render.yaml` blueprint at the root:
- Creates the Web Service: `circular-ewaste-recovery`
- Creates the PostgreSQL Database: `circular-postgres`
- Mounts a 1 GB Persistent Disk: `ewaste-data` at `/var/data`
- Configures `UPLOAD_STORAGE_PATH=/var/data/uploads`

### 2. Deployment Steps
1. Push this repository to GitHub or GitLab.
2. In the Render Dashboard, select **New +** -> **Blueprint**.
3. Connect your repository. Render will detect `render.yaml` and configure:
   - **Build Command**: `pip install -r backend/requirements.txt`
   - **Start Command**: `cd backend && flask db upgrade && gunicorn --workers 2 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT wsgi:app`
   - **Health Check Path**: `/api/health`
4. Set any optional partner variables (`PARTNER_API_URL`, `PARTNER_API_KEY`).
5. Click **Apply**. Render will provision the PostgreSQL database, run Alembic migrations (`flask db upgrade`), and start Gunicorn.

---

## 13. Security Considerations

- **Secrets Management**: No production database passwords or API keys are committed in source code. All secrets are injected through environment variables.
- **CSRF Protection**: All state-changing browser routes (`/register`, `/devices/<id>/send`, `/transfers/<id>/retry`) enforce cryptographic CSRF token validation.
- **Internal Error Masking**: Database exceptions and stack traces are logged server-side and never returned to client responses.
- **File Access Segregation**: Private documents (e.g. proof of purchase, invoices) are isolated from public downloads and require authenticated API tokens or active browser sessions.

---

## 14. Known Limitations

- **Render Free Tier Storage**: If deployed on a Render plan that does not support persistent disks, uploaded files will be stored in the web service's ephemeral container filesystem and will reset on service redeployment. To ensure permanent file retention on Render, attach a persistent disk mounted at `/var/data`.
- **Separate Partner Service**: This repository contains the **Collection & Recovery Application**. The partner Refurbishment Intelligence Application runs as a separate service and communicates via the authenticated REST contract documented above.
