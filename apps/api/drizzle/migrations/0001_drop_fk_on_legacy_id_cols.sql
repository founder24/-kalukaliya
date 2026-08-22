CREATE TABLE IF NOT EXISTS chapters_new (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  title TEXT NOT NULL,
  slug TEXT NOT NULL,
  slug_as TEXT,
  chapter_number INTEGER,
  status TEXT DEFAULT 'draft',
  content_type TEXT DEFAULT 'standard',
  notes_en TEXT,
  notes_as TEXT,
  rag_text TEXT,
  rag_text_as TEXT,
  rag_updated_at INTEGER,
  rag_indexed_at INTEGER,
  rag_sections_en TEXT DEFAULT '[]',
  rag_sections_as TEXT DEFAULT '[]',
  published_topics TEXT DEFAULT '[]',
  qa_en TEXT DEFAULT '[]',
  qa_as TEXT DEFAULT '[]',
  word_count_en INTEGER DEFAULT 0,
  word_count_as INTEGER DEFAULT 0,
  created_at INTEGER DEFAULT (unixepoch()),
  updated_at INTEGER DEFAULT (unixepoch())
);

INSERT OR IGNORE INTO chapters_new SELECT * FROM chapters;

DROP TABLE IF EXISTS chapters;

ALTER TABLE chapters_new RENAME TO chapters;

CREATE INDEX IF NOT EXISTS chapters_subject_idx ON chapters(subject_id);

CREATE UNIQUE INDEX IF NOT EXISTS chapters_subject_slug_idx ON chapters(subject_id, slug);

CREATE INDEX IF NOT EXISTS chapters_status_idx ON chapters(status);

CREATE TABLE IF NOT EXISTS chunks_new (
  id TEXT PRIMARY KEY,
  document_id TEXT,
  chapter_id TEXT,
  subject_id TEXT,
  source_type TEXT NOT NULL,
  medium TEXT NOT NULL,
  chunk_type TEXT,
  content TEXT NOT NULL,
  vector_id TEXT,
  metadata TEXT DEFAULT '{}',
  created_at INTEGER DEFAULT (unixepoch())
);

INSERT OR IGNORE INTO chunks_new SELECT * FROM chunks;

DROP TABLE IF EXISTS chunks;

ALTER TABLE chunks_new RENAME TO chunks;

CREATE INDEX IF NOT EXISTS chunks_chapter_idx ON chunks(chapter_id);

CREATE INDEX IF NOT EXISTS chunks_subject_idx ON chunks(subject_id);

CREATE INDEX IF NOT EXISTS chunks_vector_idx ON chunks(vector_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0001_drop_fk_on_legacy_id_cols');
