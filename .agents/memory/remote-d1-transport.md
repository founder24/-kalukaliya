---
name: Remote D1 transport fallback
description: Production D1 access behavior when the Cloudflare REST query endpoint disagrees with Wrangler
---

When production D1 is reachable through the configured Wrangler remote workflow but the standalone Cloudflare REST query endpoint intermittently returns error 7404 (“database could not be found”), use `wrangler d1 execute --remote --env production` for D1 reads and writes. Keep Vectorize, Workers AI, and KV on their verified APIs. Large note payloads can also hit `SQLITE_TOOBIG` when `notes_en` and `rag_text` are duplicated in one statement; write those fields in separate guarded statements.

**Why:** During a production content replacement, the configured account and token listed the database successfully through Wrangler while equivalent REST calls intermittently reported a false database-not-found response. A guarded write can already have completed before a helper fails while parsing Wrangler’s progress output, so resume from the stored `rag_indexed_at`/mapping state and verify before retrying.

**How to apply:** Use JSON mode for command reads. For `--file` writes, parse the JSON envelope after Wrangler’s upload/progress lines; do not treat non-JSON stdout at byte zero as a failed database mutation. Keep every multi-chapter maintenance query, cleanup, and verification target parameterized or derived from the active chapter list—never reuse a previous chapter ID. Avoid raw `BEGIN TRANSACTION`/`COMMIT` statements in remote D1 files.