---
name: Assamese translation boundary
description: Raw Assamese model output must be rejected before cleanup, accumulation, or persistence
---

**Rule:** Every model-generated Assamese translation must pass the shared preamble validator while it is still raw, before chunk assembly or any chapter write.

**Why:** Translation workers write directly into bilingual fields, so cleanup-only handling can publish assistant commentary through a path that bypasses English note validation.

**How to apply:** Include the chapter and chunk or content-record identity in the validation record ID, and treat a preamble rejection as deterministic rather than retrying it.