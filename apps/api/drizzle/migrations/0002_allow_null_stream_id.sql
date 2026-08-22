-- Migration 0002: allow NULL stream_id on subjects
-- Applied: 2026-08-17
--
-- Why: 45 MongoDB subjects (NEP college-level programs) had no stream association.
-- The original NOT NULL + FK (stream_id → streams.id) prevented inserting them.
-- NULL stream_id is semantically correct for standalone subjects outside the
-- board → class → stream hierarchy (e.g. Dibrugarh University NEP programs).
--
-- SQLite unique-index semantics: NULL != NULL, so (NULL, 'math') and
-- (NULL, 'physics') are both allowed because they have distinct slugs.

-- 1. Create new table with nullable stream_id
CREATE TABLE subjects_new (
    id           TEXT PRIMARY KEY,
    stream_id    TEXT,              -- nullable (was NOT NULL REFERENCES streams(id))
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL,
    description  TEXT,
    image_url    TEXT,
    pyq_papers   TEXT DEFAULT '[]',
    is_published INTEGER DEFAULT 0,
    created_at   INTEGER DEFAULT (unixepoch()),
    updated_at   INTEGER DEFAULT (unixepoch())
);

-- 2. Copy all existing rows
INSERT INTO subjects_new SELECT * FROM subjects;

-- 3. Swap tables
DROP TABLE subjects;
ALTER TABLE subjects_new RENAME TO subjects;

-- 4. Restore indexes
CREATE INDEX IF NOT EXISTS subjects_stream_idx
    ON subjects (stream_id);

-- NULL != NULL in SQLite, so rows with stream_id=NULL are unique per slug.
CREATE UNIQUE INDEX IF NOT EXISTS subjects_stream_slug_idx
    ON subjects (stream_id, slug);

-- 5. Record migration
INSERT OR IGNORE INTO schema_migrations (version) VALUES ('0002_allow_null_stream_id');
