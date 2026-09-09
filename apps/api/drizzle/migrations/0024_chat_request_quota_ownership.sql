-- Edge-accepted chat requests retain idempotency claims without pretending the
-- API Worker owns a second D1 quota reservation.
ALTER TABLE chat_request_claims
  ADD COLUMN quota_reserved INTEGER NOT NULL DEFAULT 1;