---
name: Workers AI stream lifecycle
description: Completion-marker and cancellation rules for bounded Workers AI streams.
---

# Workers AI stream lifecycle

Treat `data: [DONE]` as the stream completion signal; do not wait for the reader to reach EOF after it. A rejected buffered `Ai.run` promise or settled reader cancellation shows the Worker binding observed cancellation, not that provider-side model computation terminated.

**Why:** A stream can emit content and then hit the local attempt timeout if the parser ignores `[DONE]` and waits for a body close. Production logs showed provider-stream timeouts after partial output, and an open-body regression fixture reproduced the EOF-wait failure. A staging-only cancellation probe also showed that binding/reader settlement is separate from provider-compute status.

**How to apply:** Keep the streaming attempt cap in place, stop reading on `[DONE]` even when it is split across chunks or lacks a final newline, and do not await reader cancellation. When verifying cancellation, describe binding and stream settlement separately from provider compute; keep provider-side termination unknown without explicit evidence.