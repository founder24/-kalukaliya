---
name: CF_ACCOUNT_ID vs CLOUDFLARE_ACCOUNT_ID in embedder
description: embedder._cf_embed_url() must fall back to CLOUDFLARE_ACCOUNT_ID; using only CF_ACCOUNT_ID silently breaks embedding in Replit dev
---

## Rule
`embedder._cf_embed_url()` must resolve:
  `account_id = settings.CF_ACCOUNT_ID or settings.CLOUDFLARE_ACCOUNT_ID`

**Why:** Replit Secrets and the GCP KV secret are mapped to CLOUDFLARE_ACCOUNT_ID (same physical
account). Cloud Run wrangler binding uses CF_ACCOUNT_ID. Reading only CF_ACCOUNT_ID caused
RuntimeError in dev; `ingest_chapter_v2` caught it silently and stamped `notes_rag_indexed_at`
without writing any chunks to MongoDB or Vectorize. The `chunks` collection had 0 documents
despite 95 chapters being "indexed".

**How to apply:** If you add any new CF Workers AI REST call, resolve account_id the same way.
`_vectorize_available()` in retrieval_v2.py already does this correctly — use it as a reference.
