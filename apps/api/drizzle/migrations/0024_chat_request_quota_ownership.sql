-- Edge-accepted chat requests retain idempotency claims without pretending the
-- API Worker owns a second D1 quota reservation.
ALTER TABLE chat_request_claims
  ADD COLUMN quota_reserved INTEGER NOT NULL DEFAULT 1;

-- Keep the monthly period with the original quota-ownership migration. Some
-- production databases already received this column outside the migration
-- ledger, so the later monthly-claims migration must not add it again.
ALTER TABLE chat_request_claims
  ADD COLUMN monthly_period TEXT;