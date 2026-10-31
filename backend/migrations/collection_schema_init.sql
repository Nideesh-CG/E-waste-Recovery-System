-- =============================================================================
-- Circular E-Waste Recovery & Refurbishment Intelligence Platform
-- SCHEMA INITIALIZATION & IDEMPOTENT RECONCILIATION: collection schema
-- =============================================================================
-- Safe, non-destructive migration & reconciliation script for Application 1.
-- Target: Shared Supabase PostgreSQL database.
-- Isolation: Application 1 exclusively owns the 'collection' schema.
-- Version: Reconciles schema to match SQLAlchemy models and stamps Alembic head 'b7a8c9d0e1f2'.
-- =============================================================================

-- Step 1: Create dedicated schema
CREATE SCHEMA IF NOT EXISTS collection;

-- Step 2: Migrate any existing tables from public schema non-destructively
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'devices'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'collection' AND table_name = 'devices'
    ) THEN
        ALTER TABLE public.devices SET SCHEMA collection;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'transfers'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'collection' AND table_name = 'transfers'
    ) THEN
        ALTER TABLE public.transfers SET SCHEMA collection;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'assessment_results'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'collection' AND table_name = 'assessment_results'
    ) THEN
        ALTER TABLE public.assessment_results SET SCHEMA collection;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'device_files'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'collection' AND table_name = 'device_files'
    ) THEN
        ALTER TABLE public.device_files SET SCHEMA collection;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'metadata'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'collection' AND table_name = 'metadata'
    ) THEN
        ALTER TABLE public.metadata SET SCHEMA collection;
    END IF;
END $$;

-- Step 3: Create Collection Tables with authoritative SQLAlchemy schema (Idempotent)

-- 1. Devices Table
CREATE TABLE IF NOT EXISTS collection.devices (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    type VARCHAR(64) NOT NULL,
    brand VARCHAR(64) NOT NULL,
    model VARCHAR(64) NOT NULL,
    status VARCHAR(64) NOT NULL DEFAULT 'Pending assessment',
    date VARCHAR(32),
    condition VARCHAR(64) NOT NULL,
    year VARCHAR(8) NOT NULL DEFAULT '2024',
    weight VARCHAR(16) NOT NULL DEFAULT '1.0',
    damage TEXT DEFAULT '',
    power VARCHAR(64) DEFAULT '',
    screen VARCHAR(64) DEFAULT '',
    battery VARCHAR(64) DEFAULT '',
    image VARCHAR(256),
    document VARCHAR(256),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Transfers Table
CREATE TABLE IF NOT EXISTS collection.transfers (
    id VARCHAR(64) PRIMARY KEY,
    device_id VARCHAR(64) REFERENCES collection.devices(id) ON DELETE SET NULL,
    file VARCHAR(128) NOT NULL,
    sender VARCHAR(64) NOT NULL,
    receiver VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'Pending',
    time VARCHAR(64),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    http_status VARCHAR(64),
    response_body TEXT,
    endpoint VARCHAR(256),
    payload TEXT,
    retry_count INTEGER DEFAULT 0,
    idempotency_key VARCHAR(128) UNIQUE
);

-- 3. Assessment Results Table
CREATE TABLE IF NOT EXISTS collection.assessment_results (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(64) NOT NULL UNIQUE REFERENCES collection.devices(id) ON DELETE CASCADE,
    repairability_score INTEGER NOT NULL,
    circular_recovery_score INTEGER NOT NULL,
    repair_cost DOUBLE PRECISION NOT NULL,
    component_value DOUBLE PRECISION NOT NULL,
    recommendation VARCHAR(64) NOT NULL,
    co2_avoided_kg DOUBLE PRECISION NOT NULL,
    assessed_by VARCHAR(128) DEFAULT 'GreenTech Refurbishment Partner',
    notes TEXT,
    factors TEXT,
    assessed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Device Files Table
CREATE TABLE IF NOT EXISTS collection.device_files (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(64) NOT NULL REFERENCES collection.devices(id) ON DELETE CASCADE,
    file_type VARCHAR(32) NOT NULL,
    filename VARCHAR(128) NOT NULL,
    original_filename VARCHAR(256) NOT NULL,
    storage_key VARCHAR(256) NOT NULL UNIQUE,
    file_path VARCHAR(512) NOT NULL,
    content_type VARCHAR(128) NOT NULL,
    file_size INTEGER NOT NULL,
    storage_provider VARCHAR(32) NOT NULL DEFAULT 'local',
    is_private BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Metadata Table
CREATE TABLE IF NOT EXISTS collection.metadata (
    key VARCHAR(64) PRIMARY KEY,
    value VARCHAR(256),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. Alembic Version Tracking Table
CREATE TABLE IF NOT EXISTS collection.alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
);

-- Step 4: Idempotent Schema Reconciliation for existing/divergent deployments
DO $$
BEGIN
    -- -------------------------------------------------------------------------
    -- Reconcile collection.devices columns
    -- -------------------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'collection' AND table_name = 'devices') THEN
        -- Add missing authoritative columns if not present
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS date VARCHAR(32);
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS year VARCHAR(8);
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS weight VARCHAR(16);
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS damage TEXT DEFAULT '';
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS power VARCHAR(64) DEFAULT '';
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS screen VARCHAR(64) DEFAULT '';
        ALTER TABLE collection.devices ADD COLUMN IF NOT EXISTS battery VARCHAR(64) DEFAULT '';

        -- Backfill from legacy columns if they exist
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'purchase_year') THEN
            UPDATE collection.devices SET year = purchase_year::varchar WHERE year IS NULL AND purchase_year IS NOT NULL;
        END IF;
        UPDATE collection.devices SET year = '2024' WHERE year IS NULL;
        ALTER TABLE collection.devices ALTER COLUMN year SET NOT NULL;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'weight_kg') THEN
            UPDATE collection.devices SET weight = weight_kg::varchar WHERE weight IS NULL AND weight_kg IS NOT NULL;
        END IF;
        UPDATE collection.devices SET weight = '1.0' WHERE weight IS NULL;
        ALTER TABLE collection.devices ALTER COLUMN weight SET NOT NULL;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'damage_description') THEN
            UPDATE collection.devices SET damage = damage_description WHERE (damage IS NULL OR damage = '') AND damage_description IS NOT NULL;
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'power_status') THEN
            UPDATE collection.devices SET power = power_status WHERE (power IS NULL OR power = '') AND power_status IS NOT NULL;
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'screen_condition') THEN
            UPDATE collection.devices SET screen = screen_condition WHERE (screen IS NULL OR screen = '') AND screen_condition IS NOT NULL;
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'battery_condition') THEN
            UPDATE collection.devices SET battery = battery_condition WHERE (battery IS NULL OR battery = '') AND battery_condition IS NOT NULL;
        END IF;

        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'devices' AND column_name = 'created_at') THEN
            UPDATE collection.devices SET date = to_char(created_at, 'Mon DD, YYYY') WHERE date IS NULL;
        END IF;

        -- Widen column sizes safely
        ALTER TABLE collection.devices ALTER COLUMN id TYPE VARCHAR(64);
        ALTER TABLE collection.devices ALTER COLUMN name TYPE VARCHAR(128);
        ALTER TABLE collection.devices ALTER COLUMN type TYPE VARCHAR(64);
        ALTER TABLE collection.devices ALTER COLUMN brand TYPE VARCHAR(64);
        ALTER TABLE collection.devices ALTER COLUMN model TYPE VARCHAR(64);
        ALTER TABLE collection.devices ALTER COLUMN status TYPE VARCHAR(64);
        ALTER TABLE collection.devices ALTER COLUMN condition TYPE VARCHAR(64);
        ALTER TABLE collection.devices ALTER COLUMN image TYPE VARCHAR(256);
        ALTER TABLE collection.devices ALTER COLUMN document TYPE VARCHAR(256);
    END IF;

    -- -------------------------------------------------------------------------
    -- Reconcile collection.transfers columns
    -- -------------------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'collection' AND table_name = 'transfers') THEN
        ALTER TABLE collection.transfers ADD COLUMN IF NOT EXISTS http_status VARCHAR(64);
        ALTER TABLE collection.transfers ADD COLUMN IF NOT EXISTS response_body TEXT;
        ALTER TABLE collection.transfers ADD COLUMN IF NOT EXISTS endpoint VARCHAR(256);
        ALTER TABLE collection.transfers ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
        ALTER TABLE collection.transfers ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128);

        ALTER TABLE collection.transfers ALTER COLUMN id TYPE VARCHAR(64);
        ALTER TABLE collection.transfers ALTER COLUMN device_id TYPE VARCHAR(64);
        ALTER TABLE collection.transfers ALTER COLUMN file TYPE VARCHAR(128);
        ALTER TABLE collection.transfers ALTER COLUMN sender TYPE VARCHAR(64);
        ALTER TABLE collection.transfers ALTER COLUMN receiver TYPE VARCHAR(64);
        ALTER TABLE collection.transfers ALTER COLUMN status TYPE VARCHAR(32);
        ALTER TABLE collection.transfers ALTER COLUMN time TYPE VARCHAR(64);
    END IF;

    -- -------------------------------------------------------------------------
    -- Reconcile collection.assessment_results columns
    -- -------------------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'collection' AND table_name = 'assessment_results') THEN
        ALTER TABLE collection.assessment_results ALTER COLUMN device_id TYPE VARCHAR(64);
        ALTER TABLE collection.assessment_results ALTER COLUMN assessed_by TYPE VARCHAR(128);
        ALTER TABLE collection.assessment_results ALTER COLUMN recommendation TYPE VARCHAR(64);
    END IF;

    -- -------------------------------------------------------------------------
    -- Reconcile collection.device_files columns
    -- -------------------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'collection' AND table_name = 'device_files') THEN
        ALTER TABLE collection.device_files ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'collection' AND table_name = 'device_files' AND column_name = 'uploaded_at') THEN
            UPDATE collection.device_files SET created_at = uploaded_at WHERE created_at IS NULL;
        END IF;

        ALTER TABLE collection.device_files ALTER COLUMN device_id TYPE VARCHAR(64);
        ALTER TABLE collection.device_files ALTER COLUMN file_type TYPE VARCHAR(32);
        ALTER TABLE collection.device_files ALTER COLUMN original_filename TYPE VARCHAR(256);
        ALTER TABLE collection.device_files ALTER COLUMN storage_key TYPE VARCHAR(256);
        ALTER TABLE collection.device_files ALTER COLUMN content_type TYPE VARCHAR(128);
        ALTER TABLE collection.device_files ALTER COLUMN storage_provider TYPE VARCHAR(32);
    END IF;

    -- -------------------------------------------------------------------------
    -- Reconcile collection.metadata columns
    -- -------------------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'collection' AND table_name = 'metadata') THEN
        ALTER TABLE collection.metadata ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE collection.metadata ALTER COLUMN key TYPE VARCHAR(64);
    END IF;
END $$;

-- Step 5: Performance & Integrity Indexes
CREATE INDEX IF NOT EXISTS ix_collection_devices_status ON collection.devices(status);
CREATE INDEX IF NOT EXISTS ix_collection_devices_created_at ON collection.devices(created_at);
CREATE INDEX IF NOT EXISTS ix_collection_transfers_device_id ON collection.transfers(device_id);
CREATE INDEX IF NOT EXISTS ix_collection_transfers_timestamp ON collection.transfers(timestamp);
CREATE INDEX IF NOT EXISTS ix_collection_transfers_status ON collection.transfers(status);
CREATE INDEX IF NOT EXISTS ix_collection_transfers_idempotency_key ON collection.transfers(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_collection_device_files_device_id ON collection.device_files(device_id);
CREATE INDEX IF NOT EXISTS ix_collection_device_files_storage_key ON collection.device_files(storage_key);
CREATE INDEX IF NOT EXISTS ix_collection_assessment_results_assessed_at ON collection.assessment_results(assessed_at);

-- Step 6: Stamp Alembic Head Migration Version
DELETE FROM collection.alembic_version;
INSERT INTO collection.alembic_version (version_num) VALUES ('b7a8c9d0e1f2');

-- Step 7: Grant Privileges to Supabase Roles
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT USAGE ON SCHEMA collection TO authenticated;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA collection TO authenticated;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA collection TO authenticated;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT USAGE ON SCHEMA collection TO service_role;
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA collection TO service_role;
        GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA collection TO service_role;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
        GRANT USAGE ON SCHEMA collection TO postgres;
        GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA collection TO postgres;
        GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA collection TO postgres;
    END IF;
END $$;
