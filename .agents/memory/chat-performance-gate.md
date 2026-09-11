---
name: Chat performance gate
description: Durable constraints for the production Workers AI first-token probe
---

Bind freshness probes to a discovered subject ID. Avoid spelling out “Education Council” in the prompt because strict curriculum parsing can interpret “Education” as a school subject and reject the request as ambiguous.

Do not require raw attributed web URLs in student-facing source cards. Validate `web_used`, `web_status`, SSE ordering, the Workers AI model, and first-token timing instead.

**Why:** Curriculum fail-closed behavior and removal of arbitrary external URLs made the old production probe fail even when web retrieval and generation were healthy. GitHub runner latency can also differ from probes run elsewhere, so preserve the measured threshold but diagnose runner variance rather than weakening it silently.

**How to apply:** Use a current AHSEC→ASSEB status question bound by `subject_id`; keep direct and web samples separate and inspect their measured first-token values when CI fails.