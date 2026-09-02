-- A payment can have one staff-reviewed refund request. This is the database
-- serialization point for browser retries and concurrent submissions.
CREATE UNIQUE INDEX IF NOT EXISTS refund_payment_idx ON refund_requests(payment_id);