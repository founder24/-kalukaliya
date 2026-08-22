CREATE TABLE IF NOT EXISTS cms_document_revisions (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cms_revisions_document_idx ON cms_document_revisions(document_id, created_at DESC);