-- Migration 0003: enforce unique payment/order IDs for idempotent INSERT OR IGNORE
--
-- The INSERT OR IGNORE idempotency pattern in /payments/verify, /credit-topup/verify,
-- and the Razorpay webhook only works if the table has a UNIQUE constraint on the
-- column used as the dedup key (razorpay_order_id).  Without it, every INSERT
-- succeeds (new UUID primary key) and concurrent retries grant duplicate credits.
--
-- Before adding the index, deduplicate any existing rows in case of past duplicates.
-- In practice the D1 database was freshly migrated so this is a no-op.

-- Step 1: remove duplicate rows, keeping the earliest (min rowid) per order_id
DELETE FROM payments
WHERE razorpay_order_id IS NOT NULL
  AND rowid NOT IN (
    SELECT MIN(rowid)
    FROM payments
    WHERE razorpay_order_id IS NOT NULL
    GROUP BY razorpay_order_id
  );

-- Step 2: add UNIQUE index on razorpay_order_id (partial — NULLs are excluded so
--         manual/admin payment rows without an order_id are unaffected)
CREATE UNIQUE INDEX IF NOT EXISTS payments_order_id_unique
  ON payments (razorpay_order_id)
  WHERE razorpay_order_id IS NOT NULL;
