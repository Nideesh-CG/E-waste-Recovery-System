-- =============================================================================
-- Circular E-Waste Recovery & Refurbishment Intelligence Platform
-- SCHEMA INITIALIZATION & MIGRATION: collection schema
-- =============================================================================
-- Safe, non-destructive migration script for Application 1 (Collection & Recovery).
-- Target: Shared Supabase PostgreSQL database.
-- Isolation: Application 1 exclusively owns the 'collection' schema.
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

-- Step 3: Create Collection Tables (Idempotent)

-- 1. Devices Table
CREATE TABLE IF NOT EXISTS collection.devices (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    type VARCHAR(50) NOT NULL,
    brand VARCHAR(50),
    model VARCHAR(50),
    purchase_year INTEGER,
    weight_kg DOUBLE PRECISION,
    condition VARCHAR(50),
    power_status VARCHAR(50),
    screen_condition VARCHAR(50),
    battery_condition VARCHAR(50),
    damage_description VARCHAR(255),
    status VARCHAR(50) NOT NULL DEFAULT 'Pending assessment',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    image VARCHAR(255),
    document VARCHAR(255)
);

-- 2. Transfers Table
CREATE TABLE IF NOT EXISTS collection.transfers (
    id VARCHAR(32) PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    time VARCHAR(30),
    file VARCHAR(120),
    sender VARCHAR(80),
    receiver VARCHAR(80),
    status VARCHAR(50),
    device_id VARCHAR(32) REFERENCES collection.devices(id) ON DELETE SET NULL,
    payload TEXT,
    idempotency_key VARCHAR(128) UNIQUE
);

-- 3. Assessment Results Table
CREATE TABLE IF NOT EXISTS collection.assessment_results (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(32) NOT NULL UNIQUE REFERENCES collection.devices(id) ON DELETE CASCADE,
    repairability_score DOUBLE PRECISION,
    circular_recovery_score DOUBLE PRECISION,
    repair_cost DOUBLE PRECISION,
    component_value DOUBLE PRECISION,
    recommendation VARCHAR(50),
    co2_avoided_kg DOUBLE PRECISION,
    assessed_by VARCHAR(120),
    notes TEXT,
    factors TEXT,
    assessed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Device Files Table
CREATE TABLE IF NOT EXISTS collection.device_files (
    id SERIAL PRIMARY KEY,
    device_id VARCHAR(32) NOT NULL REFERENCES collection.devices(id) ON DELETE CASCADE,
    file_type VARCHAR(20) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    storage_key VARCHAR(255) NOT NULL UNIQUE,
    file_path VARCHAR(512) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INTEGER NOT NULL,
    storage_provider VARCHAR(50) DEFAULT 'supabase',
    is_private BOOLEAN DEFAULT TRUE,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Metadata Table
CREATE TABLE IF NOT EXISTS collection.metadata (
    key VARCHAR(50) PRIMARY KEY,
    value VARCHAR(255)
);

-- Step 4: Performance & Integrity Indexes
CREATE INDEX IF NOT EXISTS ix_collection_devices_status ON collection.devices(status);
CREATE INDEX IF NOT EXISTS ix_collection_devices_created_at ON collection.devices(created_at);
CREATE INDEX IF NOT EXISTS ix_collection_transfers_device_id ON collection.transfers(device_id);
CREATE INDEX IF NOT EXISTS ix_collection_transfers_timestamp ON collection.transfers(timestamp);
CREATE INDEX IF NOT EXISTS ix_collection_device_files_device_id ON collection.device_files(device_id);
CREATE INDEX IF NOT EXISTS ix_collection_assessment_results_assessed_at ON collection.assessment_results(assessed_at);

-- Step 5: Grant Privileges to Supabase Roles
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

