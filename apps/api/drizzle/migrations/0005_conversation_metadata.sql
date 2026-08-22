-- Per-conversation UI metadata for the D1-backed saved-history API.
-- Chat messages remain normalized in `chats`. This table preserves user
-- actions such as rename, star, and archive without duplicating message data.
CREATE TABLE IF NOT EXISTS conversation_metadata (
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  title TEXT,
  starred INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (user_id, session_id)
);

CREATE INDEX IF NOT EXISTS conversation_metadata_user_updated_idx
  ON conversation_metadata(user_id, updated_at DESC);