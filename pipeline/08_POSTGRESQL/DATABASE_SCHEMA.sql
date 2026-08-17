-- CANONICAL STRUCTURED + RELATIONAL LAYER
-- Run this against POSTGRES_DB from .env before DB_LOADER.py
-- (DB_LOADER.ensure_database_and_schema() does this for you).

CREATE EXTENSION IF NOT EXISTS vector;  -- pgvector lives inside this same DB (see 10_PGVECTOR)

CREATE TABLE IF NOT EXISTS institution (
    institution_id      TEXT PRIMARY KEY,
    institution_name    TEXT NOT NULL,
    state                TEXT,
    district             TEXT,
    city                 TEXT,
    institute_type       TEXT,
    ownership            TEXT,
    approval_status     BOOLEAN,
    current_status       TEXT,
    is_autonomous       BOOLEAN,
    nba_accredited      BOOLEAN,
    accreditation_valid_until TEXT,
    year_established     INTEGER,
    aicte_code           TEXT,
    last_updated         TEXT,
    nirf_rank            INTEGER,
    naac_grade           TEXT
);

-- migrations for databases created before the Phase-4 scale-up
ALTER TABLE institution ADD COLUMN IF NOT EXISTS city TEXT;
ALTER TABLE institution ADD COLUMN IF NOT EXISTS ownership TEXT;
ALTER TABLE institution ADD COLUMN IF NOT EXISTS current_status TEXT;
ALTER TABLE institution ADD COLUMN IF NOT EXISTS is_autonomous BOOLEAN;
ALTER TABLE institution ADD COLUMN IF NOT EXISTS nba_accredited BOOLEAN;
ALTER TABLE institution ADD COLUMN IF NOT EXISTS accreditation_valid_until TEXT;

CREATE TABLE IF NOT EXISTS course (
    course_id            TEXT PRIMARY KEY,
    institution_id       TEXT REFERENCES institution(institution_id),
    course_name          TEXT NOT NULL,
    department           TEXT,
    duration_years       INTEGER,
    intake_capacity      INTEGER,
    fee_per_year         NUMERIC(12,2),
    course_status        TEXT,
    last_updated         TEXT
);

ALTER TABLE course ADD COLUMN IF NOT EXISTS course_status TEXT;

CREATE TABLE IF NOT EXISTS faculty (
    faculty_id            TEXT PRIMARY KEY,
    institution_id        TEXT REFERENCES institution(institution_id),
    faculty_name          TEXT NOT NULL,
    designation           TEXT,
    qualification         TEXT,
    specialization        TEXT,
    department            TEXT,
    years_of_experience   INTEGER,
    date_joined           TEXT,
    last_updated          TEXT
);

ALTER TABLE faculty ADD COLUMN IF NOT EXISTS specialization TEXT;
ALTER TABLE faculty ADD COLUMN IF NOT EXISTS years_of_experience INTEGER;

CREATE TABLE IF NOT EXISTS scholarship (
    scholarship_id        TEXT PRIMARY KEY,
    scheme_name           TEXT NOT NULL,
    administering_body    TEXT,
    amount                TEXT,
    applicable_states     TEXT,
    last_updated          TEXT
);

CREATE TABLE IF NOT EXISTS approval (
    approval_id           TEXT PRIMARY KEY,
    institution_id        TEXT REFERENCES institution(institution_id),
    approval_type         TEXT NOT NULL,      -- nba | closed | unapproved
    nba_status            TEXT,
    valid_until           TEXT,
    closure_year          TEXT,
    reason                TEXT,
    state                 TEXT,
    last_updated          TEXT
);

CREATE TABLE IF NOT EXISTS student (
    student_id             TEXT PRIMARY KEY,
    institution_id         TEXT REFERENCES institution(institution_id),
    student_name           TEXT NOT NULL
);

-- internship changed shape in the Phase-4 scale-up (was student-linked);
-- the old table is dropped so the new institution-linked one replaces it.
DROP TABLE IF EXISTS internship CASCADE;
CREATE TABLE IF NOT EXISTS internship (
    internship_id          TEXT PRIMARY KEY,
    institution_id         TEXT REFERENCES institution(institution_id),
    domain                 TEXT,
    organization_name      TEXT,
    duration_weeks         INTEGER,
    stipend_amount         NUMERIC(12,2),
    mode                   TEXT,
    is_ppo_linked          BOOLEAN,
    program_source         TEXT
);

-- Lineage: every canonical record traces back to its source(s).
CREATE TABLE IF NOT EXISTS entity_mapping (
    id                       SERIAL PRIMARY KEY,
    master_entity_id         TEXT NOT NULL,
    entity_type               TEXT NOT NULL,
    source_system              TEXT NOT NULL,
    source_database            TEXT NOT NULL,
    source_table                TEXT,
    source_record_id           TEXT NOT NULL,
    match_score                 REAL,
    created_at                  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_lineage (
    id                        SERIAL PRIMARY KEY,
    canonical_entity_id        TEXT NOT NULL,
    source_system                TEXT NOT NULL,
    source_database              TEXT NOT NULL,
    source_table                  TEXT,
    source_record_id             TEXT,
    transformation_version        TEXT DEFAULT 'v1',
    validation_status              TEXT DEFAULT 'pending',
    ingestion_timestamp             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_entity_mapping_master ON entity_mapping(master_entity_id);
CREATE INDEX IF NOT EXISTS idx_institution_state ON institution(state);
CREATE INDEX IF NOT EXISTS idx_course_institution ON course(institution_id);
CREATE INDEX IF NOT EXISTS idx_faculty_institution ON faculty(institution_id);
CREATE INDEX IF NOT EXISTS idx_approval_institution ON approval(institution_id);
