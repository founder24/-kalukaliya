---
name: Worker chat web egress and latency
description: Production-proven constraints for bounded web retrieval and fast Workers AI streaming.
---

Use Crossref's public JSON API for the bounded educational web-search branch. Wikimedia's REST and Action APIs return immediate errors when called from the production Cloudflare Worker, even though they work from a normal shell.

**Why:** Repeated production deployments showed both Wikimedia API forms failing from Worker egress. Crossref returned attributed DOI sources within the bounded retrieval budget.

**How to apply:** Do not switch the Worker web adapter back to Wikimedia without testing from an actually deployed Worker. Keep provider failures non-blocking and web text explicitly delimited as untrusted quoted data.

Use Cloudflare's low-latency Llama 8B instruct model as the streaming primary and keep the larger Qwen model as fallback when the under-three-second first-token target applies.

**Why:** The prior GLM primary produced a 6.7-second direct-RAG first token. The fast model produced a 1.275-second direct sample and a 1.993–2.092-second range across three attributed web samples.

**How to apply:** Preserve the release performance gate when changing models or prompt size, and require native `@cf/` model diagnostics plus source-card-before-token ordering.