---
name: Workers AI abort semantics
description: Staging evidence for binding-level cancellation and limits on claims about provider-side termination.
---

# Workers AI abort semantics

Treat a rejected buffered `Ai.run` promise and a settled streaming-reader cancellation as evidence that the Worker binding observed cancellation; they do not prove provider-side model computation terminated.

**Why:** A staging-only probe using the real Workers AI binding observed the buffered call reject when its forwarded signal aborted and the streaming source reader's cancel operation settle. No provider-side inference status or identifier was exposed.

**How to apply:** When verifying or reporting AI cancellation, describe the binding and stream settlement separately from provider compute. Keep provider-side termination unknown unless Cloudflare exposes explicit evidence for it.