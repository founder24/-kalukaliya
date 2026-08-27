-- Authoritative quota and refresh-token state must use D1's serialized writes.
-- KV has no compare-and-swap primitive, so it cannot safely protect these
-- security-sensitive operations under concurrent requests.

CREATE TABLE IF NOT EXISTS anonymous_quota_usage (
  anon_id TEXT NOT NULL,
  period TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER DEFAULT (unixepoch()),
  PRIMARY KEY (anon_id, period)
);

CREATE INDEX IF NOT EXISTS anonymous_quota_updated_idx
  ON anonymous_quota_usage(updated_at);

CREATE TABLE IF NOT EXISTS refresh_token_claims (
  jti TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  claimed_at INTEGER DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS rtc_expires_idx ON refresh_token_claims(expires_at);
CREATE INDEX IF NOT EXISTS rtc_user_idx ON refresh_token_claims(user_id);