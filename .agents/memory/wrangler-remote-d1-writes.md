---
name: Wrangler remote D1 writes
description: Safe format for production D1 content maintenance through Wrangler
---

Remote `wrangler d1 execute --file` rejects raw `BEGIN TRANSACTION` and `COMMIT` statements; use supported atomic APIs or separate statements, then verify the affected rows and invalidate any related KV cache.

**Why:** The remote D1 execution service enforces transaction handling outside submitted SQL and fails before running a file that contains those statements.

**How to apply:** Keep production content writes idempotent, avoid transaction-control SQL in Wrangler files, and perform a read-back plus cache invalidation before treating the update as live.