---
name: Worker chat web egress and latency
description: Production-proven constraints for bounded web retrieval and fast Workers AI streaming.
---

Use Crossref for non-board educational fallback. For Assam board facts, query the official AHSEC WordPress pages API by recognized topic; use the ASSEB formation page only for merger/status questions. Cache successful payloads briefly in KV.

**Why:** Repeated production deployments showed Wikimedia, Bing RSS, DuckDuckGo Lite, Google News RSS, and WordPress public search failing or timing out from Worker egress even when they worked from a normal shell. ASSEB and Crossref were reachable within the bounded path.

**How to apply:** Treat shell reachability as insufficient; test from the deployed Worker. Require topic-specific official evidence, never turn an official failure into a scholarly success, distinguish HTTP failure from empty, and delimit web text as untrusted.

Run the web branch only for explicit web-search or freshness intent, not merely because a question lacks chapter or subject scope.

**Why:** Broad unscoped questions triggered irrelevant Crossref papers and added roughly 0.9 seconds before generation without improving curriculum accuracy.

**How to apply:** Let ordinary questions use curriculum retrieval plus Workers AI directly. Keep Crossref available for requests such as “latest”, “current”, “news”, or “search the web”.

Use Cloudflare's low-latency Llama 8B instruct model as the streaming primary and keep the larger Qwen model as fallback when the under-three-second first-token target applies.

**Why:** The prior GLM primary produced a 6.7-second direct-RAG first token. The fast model produced a 1.275-second direct sample and a 1.993–2.092-second range across three attributed web samples.

**How to apply:** Preserve the release performance gate when changing models or prompt size, and require native `@cf/` model diagnostics plus source-card-before-token ordering.