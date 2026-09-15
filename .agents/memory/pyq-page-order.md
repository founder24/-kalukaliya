---
name: Chapter PYQ page order
description: The chapter PYQ image contract is one ordered entry per scanned page.
---

The chapter PYQ image contract is an ordered array of one `{id, url, uploaded_at}` entry per scanned page. New clients must append sequentially and render the flat order; do not sort by year or assume grouped `file_urls` data.

**Why:** The chapter upload route returns raw page entries, while the older viewer expected grouped papers. That mismatch could hide pages or reorder scans, which also breaks predictable ad placement between pages.

**How to apply:** Normalize legacy grouped records only at the viewer boundary. Keep new uploads awaited one at a time and insert between-page ads while iterating the normalized page list.