---
name: Release failure rehearsals
description: How to prove independent release-check scheduling without weakening production gates
---

Use a manually dispatched, test-only workflow to confirm deployed endpoints, force one release check to fail, and run independent checks plus cleanup in parallel.

**Why:** A full production deploy rehearsal can be blocked by unrelated build or content gates before the dependency behavior under test becomes observable. Relaxing those gates would invalidate the safety proof.

**How to apply:** Keep the forced failure manual-only, do not alter production behavior, preserve actual results for every independent check, and make fixture cleanup unconditional.