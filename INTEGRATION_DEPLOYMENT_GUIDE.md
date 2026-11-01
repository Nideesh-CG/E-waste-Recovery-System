# Integration Deployment & Operations Guide

**Platform:** Circular E-Waste Recovery & Refurbishment Intelligence  
**Target Environment:** Vercel Serverless + Supabase PostgreSQL + Supabase Private Storage  
**Audience:** DevOps Engineers, Backend Engineers, System Administrators

---

## 1. Shared Infrastructure Architecture

Both applications run as independent serverless microservices on Vercel while sharing a single Supabase backend:

```
                    ┌──────────────────────────────────────────────┐
                    │            SUPABASE POSTGRESQL               │
                    │                                              │
                    │  ┌────────────────────┐ ┌──────────────────┐ │
                    │  │ collection schema  │ │ refurbishment    │ │
                    │  │ (Application 1)    │ │ schema (App 2)   │ │
                    │  │                    │ │                  │ │
                    │  │ • devices          │ │ • devices        │ │
                    │  │ • transfers        │ │ • transfers      │ │
                    │  │ • assessment_res.. │ │ • assessments    │ │
                    │  │ • device_files     │ │ • assess_comp..  │ │
                    │  │ • metadata         │ │ • recovery_ana.. │ │
                    │  │ • alembic_version  │ │ • reports        │ │
                    │  │                    │ │ • idemp_events   │ │
                    │  │                    │ │ • device_files   │ │
                    │  │                    │ │ • alembic_vers.. │ │
                    │  └────────────────────┘ └──────────────────┘ │
                    └───────────────────────┬──────────────────────┘
                                            │
                                            │ Shared Private Bucket
                                            ▼
                    ┌──────────────────────────────────────────────┐
                    │            SUPABASE STORAGE                  │
                    │          Bucket: `ewaste-files`              │
                    └──────────────────────────────────────────────┘
```

---

## 2. Step 1: Database Schema Initialization on Supabase

Both applications require dedicated database schemas in the shared Supabase PostgreSQL database.

### 1. Execute Collection Schema Init (Application 1)
If not already executed, run `backend/migrations/collection_schema_init.sql` in the Supabase SQL Editor:
- Creates schema `collection`.
- Moves any existing legacy tables non-destructively from `public` to `collection`.
- Creates `collection.devices`, `collection.transfers`, `collection.assessment_results`, `collection.device_files`, `collection.metadata`.
- Grants permissions to `anon`, `authenticated`, and `service_role`.
- Stamps `collection.alembic_version` to head revision `'b7a8c9d0e1f2'`.

### 2. Execute Refurbishment Schema Init (Application 2)
In the Supabase SQL Editor, execute the new idempotent script:
`D:\circular-refurbishment-intelligence\backend\migrations\refurbishment_schema_init.sql`

```sql
-- Highlights of refurbishment_schema_init.sql:
CREATE SCHEMA IF NOT EXISTS refurbishment;

GRANT USAGE ON SCHEMA refurbishment TO anon, authenticated, service_role;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA refurbishment TO anon, authenticated, service_role;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA refurbishment TO anon, authenticated, service_role;

-- Creates 8 tables in refurbishment schema:
-- 1. refurbishment.devices
-- 2. refurbishment.idempotency_events
-- 3. refurbishment.assessments
-- 4. refurbishment.device_files
-- 5. refurbishment.transfers
-- 6. refurbishment.assessment_components
-- 7. refurbishment.recovery_analyses
-- 8. refurbishment.reports

-- Stamps Alembic version
CREATE TABLE IF NOT EXISTS refurbishment.alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
INSERT INTO refurbishment.alembic_version (version_num) VALUES ('7f9cd19afe3f') ON CONFLICT DO NOTHING;
```

### 3. Verify Database Isolation
Run the verification scripts locally or in CI:

For Application 1:
```bash
python D:\E-waste-Recovery\backend\scripts\check_database_schema.py
python D:\E-waste-Recovery\backend\scripts\verify_migrations.py
```

For Application 2:
```bash
python D:\circular-refurbishment-intelligence\backend\scripts\check_database_schema.py
python D:\circular-refurbishment-intelligence\backend\scripts\verify_migrations.py
```
Expected output:
```
[SUCCESS] All required schemas, tables, and columns are present and compatible.
[OK] Database migration version matches head.
```

---

## 3. Step 2: Supabase Storage Configuration

Both applications access the private bucket `ewaste-files`.

1. **Bucket Verification:**
   - Go to **Supabase Dashboard** > **Storage** > **Buckets**.
   - Ensure a bucket named `ewaste-files` exists.
   - If not present, create it with **Public bucket = OFF** (private bucket).

2. **Access Policy:**
   - Both applications authenticate using `SUPABASE_SERVICE_ROLE_KEY`. The service-role key bypasses Row-Level Security (RLS) policies, allowing secure server-to-server object upload, download, and signed URL generation.
   - Device transfers without files (`files: []`) are fully supported by both applications.

---

## 4. Step 3: Vercel Environment Variables Configuration

Configure the following environment variables in the Vercel dashboard for each deployment:

### Application 1: E-Waste Collection & Recovery (`D:\E-waste-Recovery`)

| Variable Name | Required | Example / Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | **Yes** | `postgresql+psycopg://postgres:<password>@<supabase-host>:5432/postgres?options=-c%20search_path=collection,public` |
| `SECRET_KEY` | **Yes** | 64-character random hex string (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `CIRCULAR_API_KEY` | **Yes** | Inbound key for receiving assessment callbacks from Partner (e.g. `c1a2b3d4...`) |
| `PARTNER_API_URL` | **Yes** | Refurbishment Vercel URL (e.g. `https://circular-refurbishment.vercel.app`) |
| `PARTNER_API_KEY` | **Yes** | Must match `PARTNER_INBOUND_API_KEY` configured on Refurbishment |
| `SUPABASE_URL` | **Yes** | `https://<supabase-project-id>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | **Yes** | Supabase Service Role Secret Key |
| `SUPABASE_STORAGE_BUCKET` | Optional | `ewaste-files` (defaults to `ewaste-files`) |
| `IS_PRODUCTION` | **Yes** | `true` |
| `SEED_DEMO_DATA` | Optional | `false` |

### Application 2: Refurbishment Intelligence (`D:\circular-refurbishment-intelligence`)

| Variable Name | Required | Example / Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | **Yes** | `postgresql+psycopg://postgres:<password>@<supabase-host>:5432/postgres?options=-c%20search_path=refurbishment,public` |
| `SECRET_KEY` | **Yes** | 64-character random hex string (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `PARTNER_INBOUND_API_KEY` | **Yes** | Inbound key matching `PARTNER_API_KEY` on Collection |
| `COLLECTION_API_URL` | **Yes** | Collection Vercel URL (e.g. `https://circular-ewaste.vercel.app`) |
| `COLLECTION_API_KEY` | **Yes** | Outbound key matching `CIRCULAR_API_KEY` on Collection |
| `SUPABASE_URL` | **Yes** | `https://<supabase-project-id>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | **Yes** | Supabase Service Role Secret Key |
| `SUPABASE_STORAGE_BUCKET` | Optional | `ewaste-files` (defaults to `ewaste-files`) |
| `IS_PRODUCTION` | **Yes** | `true` |
| `DB_USE_NULLPOOL` | Optional | `true` (automatically active on Vercel serverless) |

---

## 5. Step 4: Deployment & Health Verification

Deploy both applications to Vercel:

### 1. Deploy Application 1
```bash
cd D:\E-waste-Recovery
vercel --prod
```

### 2. Deploy Application 2
```bash
cd D:\circular-refurbishment-intelligence
vercel --prod
```

### 3. Verify Health Probes
Execute GET requests against each deployment's health endpoint:

```bash
# Check Collection Health
curl -s https://<collection-app-url>/api/health
# Response must be:
# {"database": "connected", "service": "Circular E-Waste Recovery Platform API", "status": "healthy", ...}

# Check Refurbishment Health
curl -s https://<refurbishment-app-url>/api/health
# Response must be:
# {"application": "Refurbishment Intelligence", "database": "ok", "schema": "refurbishment", "status": "ok", "ui_build_available": true}
```

---

## 6. Step 5: Post-Deployment Smoke Test

To verify the integration end-to-end in the live production environment:

1. **Register a Test Device in Collection:**
   - Navigate to `https://<collection-app-url>/register`.
   - Submit a test laptop (e.g. "Dell Latitude 5420", Condition: "Good").
   - Confirm the device detail page renders with status **"Pending assessment"**.

2. **Trigger Outbound Device Transfer:**
   - Click the button **"Send to Refurbishment Application"**.
   - Verify that the banner displays:
     > *"Transfer successfully dispatched to partner application."*
   - Verify the device status updates to **"Processing"**.

3. **Verify Partner Ingestion:**
   - In Refurbishment Intelligence (`https://<refurbishment-app-url>/`), check the incoming queue.
   - Confirm the test device appears with status **"Received"**.

4. **Complete Assessment & Verify Callback:**
   - In Refurbishment Intelligence, complete the assessment for the device.
   - Click **"Send Assessment to Collection"** (or execute callback).
   - In Collection, refresh the device detail page:
     - Verify status transitions to **"Refurbishment"** (or the corresponding canonical status).
     - Verify the assessment card displays the repairability score, circular recovery score, repair cost, and component value.

