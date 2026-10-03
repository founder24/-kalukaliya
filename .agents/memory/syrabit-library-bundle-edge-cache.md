---
name: Syrabit library bundle edge cache
description: The Worker KV cache can keep public library bundles stale after content changes
---

The public library bundle has a separate Edge Worker KV cache for full, slim, and board-specific boot responses. Purging Cloudflare URL cache does not clear those Worker KV entries.

**Why:** Staff-published courses were present in the live catalog but absent from normal bundle URLs until the `api:library-bundle:` Worker KV entries were deleted; a cache-busted request had shown the current data but did not refresh the normal URL.

**How to apply:** When content changes, invalidate the affected full, `slim=1`, and `boot=<boardId>` Worker KV variants. Verify with normal public URLs, not only a cache-busted request.