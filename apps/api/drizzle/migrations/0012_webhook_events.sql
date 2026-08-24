-- Keep Razorpay webhook retries from applying an entitlement more than once.
CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  received_at INTEGER DEFAULT (unixepoch()),
  processed_at INTEGER
);