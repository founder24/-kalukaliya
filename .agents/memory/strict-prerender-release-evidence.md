---
name: Strict prerender release evidence
description: Diagnose production topic coverage failures across D1 migrations, public endpoints, and the checker version on main.
---

Before correcting published content for a strict prerender failure, compare the exact production D1 value, the public API response, and the checker source at the deployed main SHA. A successful conditional migration does not prove that it changed a row, and passing local tests do not prove the production checker matches the local one.

**Why:** A guarded NCERT migration was recorded as successful without matching the already-partially-correct live text; an outdated checker in main then rejected the valid production derivation. After the checker was corrected, the release still had one unresolved sitemap-topic coverage gap.

**How to apply:** Confirm the migration's affected-row or postcondition evidence, inspect exact failure logs, and do not claim the Pages release or production score until strict sitemap coverage and post-release checks pass.
