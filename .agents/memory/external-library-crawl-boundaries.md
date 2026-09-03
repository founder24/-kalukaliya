---
name: External library crawl boundaries
description: Durable safeguards for refreshing large public DSpace and educational-site catalogs.
---

Deduplicate both queued pages and document candidates before applying crawl limits. Normalize DSpace item and bitstream query variants so alternate record views cannot multiply one paper into many records.

**Why:** Public college repositories expose repeated parent links, full-record variants, pagination links, and duplicate bitstream anchors. Deduplicating only at the end caused crawl queues and document counts to expand far beyond the unique archive.

**How to apply:** For any external-library refresh, keep traversal confined to the approved seed graph, reject search/statistics/admin routes, normalize volatile query parameters, and count only unique canonical candidates.

Do not run page-by-page OCR inline across an entire college archive. Import source metadata, validated PDF identity, checksums, page counts, and any native text first; schedule image-only OCR as a separate resumable workload.

**Why:** Most college question papers are scans. Inline OCR made a catalog refresh stall for minutes per document and prevented thousands of otherwise valid records from becoming available.

**How to apply:** Treat deferred OCR as an explicit extraction state, preserve the complete source link, and process scans later in bounded batches before RAG indexing.