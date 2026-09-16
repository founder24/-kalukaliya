---
name: Chapter question-bank rollout
description: Public chapter question banks require staged backend rollout because the frontend may see the legacy PYQ endpoint first.
---

The chapter reader should keep a non-fabricating legacy-PYQ fallback while the solved question-bank endpoint is being rolled out. The full bank may include important questions and textbook exercises only when their stored chapter source includes an answer or solution.

**Why:** The frontend preview can run against an older backend deployment, and silently replacing the legacy endpoint would turn existing PYQs into an empty Questions tab. Hiding missing solutions is also misleading.

**How to apply:** Keep the fallback limited to legacy PYQs, label missing solutions explicitly, and let the new backend payload take over once deployed. Populate `qa_rag_sections` from exact chapter notes before promising solved important/exercise coverage.