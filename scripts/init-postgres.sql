-- PostgreSQL Initialization Script (issue #49)
-- Runs automatically when postgres container starts for the first time
-- Creates database, user, and sets up permissions

-- Create database if not exists (handled by POSTGRES_DB env var)
-- CREATE DATABASE hh_applicant_tool;

-- Create user if not exists (handled by POSTGRES_USER/POSTGRES_PASSWORD env vars)
-- CREATE USER hh_user WITH PASSWORD 'hh_password';

-- Grant all privileges on database to user
GRANT ALL PRIVILEGES ON DATABASE hh_applicant_tool TO hh_user;

-- Connect to the database
\c hh_applicant_tool;

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO hh_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO hh_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO hh_user;

-- Set default privileges for future objects
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO hh_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO hh_user;

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Note: The application schema will be created by the application's init_db function
-- when it first connects. This script only sets up the database and permissions.
