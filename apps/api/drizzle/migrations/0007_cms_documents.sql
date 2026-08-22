CREATE TABLE IF NOT EXISTS cms_documents (
  id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cms_documents_updated_idx ON cms_documents(updated_at DESC);