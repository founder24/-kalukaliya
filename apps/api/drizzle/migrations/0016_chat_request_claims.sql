-- Keep one logical chat turn from consuming two quota slots when the browser
-- retries after a response or stream transport failure.
CREATE TABLE IF NOT EXISTS chat_request_claims (
  request_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  period TEXT NOT NULL,
  is_anon INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'reserved',
  session_id TEXT,
  response_content TEXT,
  response_metadata TEXT,
  created_at INTEGER DEFAULT (unixepoch()),
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS chat_request_claims_expiry_idx
  ON chat_request_claims(expires_at);